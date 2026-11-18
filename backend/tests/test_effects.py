"""Cross-center effect aggregation: pooling math, local diagnostics, review
guard, versioning and disclosure semantics."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "center_agent"))

from app.pooling import (  # noqa: E402
    build_pooling,
    chi2_sf,
    heterogeneity,
    pool_fixed,
    pool_random,
)
from center_agent import executor  # noqa: E402

SEEDED_FIXED_PLAN_VERSION = 1   # 固定效应为主（主队列）
SEEDED_RANDOM_PLAN_VERSION = 2  # 随机效应为主（byear >= 1990 子队列）


# --- pooling math -------------------------------------------------------------

def test_chi2_sf_known_values():
    assert abs(chi2_sf(3.841, 1) - 0.05) < 1e-3
    assert abs(chi2_sf(5.991, 2) - 0.05) < 1e-3
    assert abs(chi2_sf(11.345, 3) - 0.01) < 1e-3
    assert chi2_sf(0, 3) == 1.0


def test_fixed_pooling_inverse_variance():
    fe = pool_fixed([0.1, 0.2, 0.3], [0.01, 0.02, 0.03])
    w = [100.0, 50.0, 100.0 / 3.0]
    expected = (0.1 * w[0] + 0.2 * w[1] + 0.3 * w[2]) / sum(w)
    assert abs(fe["theta"] - expected) < 1e-9
    assert abs(fe["se"] - math.sqrt(1.0 / sum(w))) < 1e-9


def test_random_pooling_dersimonian_laird():
    re_ = pool_random([0.0, 0.4], [0.01, 0.01])
    # Q = 8, c = 100, tau2 = 7/100 -> w* = 1/0.08
    assert abs(re_["tau2"] - 0.07) < 1e-9
    assert abs(re_["theta"] - 0.2) < 1e-9
    assert abs(re_["se"] - math.sqrt(1.0 / 25.0)) < 1e-9
    het = heterogeneity([0.0, 0.4], [0.01, 0.01])
    assert abs(het["Q"] - 8.0) < 1e-9
    assert abs(het["I2"] - 87.5) < 1e-6


def test_build_pooling_hints_and_no_zero_fill():
    def diag(theta, se, n, imbal=False):
        return {"effects": {"risk_difference": {"theta": theta, "se": se,
                                                "variance": se * se}},
                "n_included": n,
                "balance": {"imbalanced": imbal, "max_abs_smd": 0.9 if imbal else 0.05,
                            "covariates": {}}}
    rows = [{"center": "C1", "diagnostics": diag(0.25, 0.08, 120)},
            {"center": "C2", "diagnostics": diag(0.25, 0.09, 100)},
            {"center": "C4", "diagnostics": diag(-0.18, 0.08, 110, imbal=True)}]
    p = build_pooling(rows, "risk_difference", "fixed")
    types = {h["type"] for h in p["hints"]}
    assert "direction" in types            # 方向不一致
    assert "case_mix" in types             # 病例组合失衡提示
    assert "random_stability" in types     # 中心数 < 5 的随机效应不稳定
    assert p["k_included"] == 3
    # 空池：不形成估计，也不以零效应补位
    empty = build_pooling([], "risk_difference", "random")
    assert empty["k_included"] == 0
    assert empty["measures"] == {}
    assert any(h["type"] == "no_pool" for h in empty["hints"])


# --- center-local executor protected categories -------------------------------

FIELDS = {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
          "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
          "treatment.path": "tx_path", "treatment.start_date": "tx_start",
          "followup.outcome": "fu_outcome", "followup.date": "fu_date",
          "followup.lab_value": "fu_lab"}

EFFECT_SPEC = {
    "treatment_variable": "treatment.path", "treated": "A", "control": "B",
    "outcome_variable": "followup.outcome", "outcome_value": "remission",
    "covariates": ["patient.sex", "patient.birth_year"],
    "covariate_types": {"patient.sex": "categorical", "patient.birth_year": "numeric"},
    "min_cell": 5,
}


def _write_center(tmp_path: Path, rows: list[dict]) -> str:
    d = tmp_path / "CX"
    d.mkdir(parents=True, exist_ok=True)
    header = ["patient_id"] + list(FIELDS.values())
    with (d / "registry.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({"patient_id": f"CX-P{i:03d}", **r})
    return str(d)


def _row(path, outcome, sex="F", byear=1980):
    return {"pat_sex": sex, "pat_byear": byear, "dx_code": "RD01",
            "dx_date": "2022-01-01", "tx_path": path, "tx_start": "2022-02-01",
            "fu_outcome": outcome, "fu_date": "2023-01-01", "fu_lab": "6.0"}


def _job():
    return {"job_id": "t", "conditions": [], "effect_spec": dict(EFFECT_SPEC),
            "mapping": {v: {"field": f, "value_map": None} for v, f in FIELDS.items()}}


def test_executor_included_diagnostics(tmp_path):
    rows = ([_row("A", "remission")] * 12 + [_row("A", "stable")] * 8
            + [_row("B", "remission")] * 6 + [_row("B", "stable")] * 14)
    res = executor.execute(_write_center(tmp_path, rows), _job())
    assert res["status"] == "included"
    d = res["diagnostics"]
    assert d["n_included"] == 40 and d["n_treated"] == 20 and d["n_control"] == 20
    rd = d["effects"]["risk_difference"]
    assert abs(rd["theta"] - (0.6 - 0.3)) < 1e-6
    assert d["converged"] is True and d["balance"]["covariates"]


def test_executor_insufficient_events_on_zero_cell(tmp_path):
    # treated arm has zero non-events -> 2x2 has a zero cell
    rows = [_row("A", "remission")] * 10 + [_row("B", "remission")] * 5 + [_row("B", "stable")] * 5
    res = executor.execute(_write_center(tmp_path, rows), _job())
    assert res["status"] == "insufficient_events"
    assert "effects" not in res.get("diagnostics", {})


def test_executor_small_cell_suppressed(tmp_path):
    rows = ([_row("A", "remission")] * 2 + [_row("A", "stable")] * 8
            + [_row("B", "remission")] * 5 + [_row("B", "stable")] * 5)
    res = executor.execute(_write_center(tmp_path, rows), _job())
    assert res["status"] == "small_cell_suppressed"
    # 披露保护：不得传出任何效应数值
    assert "diagnostics" not in res


def test_executor_no_common_support(tmp_path):
    rows = ([_row("A", "remission", byear=1960)] * 6 + [_row("A", "stable", byear=1958)] * 6
            + [_row("B", "remission", byear=1992)] * 5 + [_row("B", "stable", byear=1995)] * 5)
    res = executor.execute(_write_center(tmp_path, rows), _job())
    assert res["status"] == "no_common_support"


def test_executor_model_not_converged(tmp_path):
    # outcome perfectly separated by sex -> logistic model cannot converge
    rows = ([_row("A", "remission", sex="F")] * 6 + [_row("A", "stable", sex="M")] * 6
            + [_row("B", "remission", sex="F")] * 6 + [_row("B", "stable", sex="M")] * 6)
    res = executor.execute(_write_center(tmp_path, rows), _job())
    assert res["status"] == "model_not_converged"


def test_executor_missing_mapping(tmp_path):
    job = _job()
    del job["mapping"]["patient.birth_year"]
    rows = [_row("A", "remission")] * 6 + [_row("B", "stable")] * 6
    res = executor.execute(_write_center(tmp_path, rows), job)
    assert res["status"] == "missing_mapping"


# --- end-to-end API scenarios --------------------------------------------------

def test_seeded_effect_plans(client, researcher1):
    r = client.get("/api/effect-plans", headers=researcher1)
    plans = r.json()
    assert len(plans) == 2
    assert all(p["approved_version_id"] for p in plans)
    detail = client.get("/api/effect-plans/1", headers=researcher1).json()
    spec = detail["versions"][0]["spec"]
    assert spec["primary_pooling"] == "fixed"
    assert spec["treated"] == "A" and spec["control"] == "B"


def _run(client, headers, plan_version_id):
    r = client.post("/api/effect-runs", headers=headers,
                    json={"plan_version_id": plan_version_id})
    assert r.status_code == 201, r.text
    return r.json()


def test_main_plan_run_center_categories(client, researcher1):
    run = _run(client, researcher1, SEEDED_FIXED_PLAN_VERSION)
    assert run["pool_version"] == 1
    by_center = {c["center_code"]: c for c in run["center_results"]}
    # C1-C3 大中心正向、C4 反向纳入；C5 无共同支持；C6 小单元格抑制
    for code in ("C1", "C2", "C3", "C4"):
        assert by_center[code]["status"] == "included"
    assert by_center["C5"]["status"] == "no_common_support"
    assert by_center["C6"]["status"] == "small_cell_suppressed"
    assert by_center["C4"]["diagnostics"]["effects"]["risk_difference"]["theta"] < 0
    assert by_center["C1"]["diagnostics"]["effects"]["risk_difference"]["theta"] > 0
    # 保护类别不传出效应值（不以零效应补位）
    assert by_center["C5"]["diagnostics"] is None
    assert by_center["C6"]["diagnostics"] is None

    pooling = run["result"]["pooling"]
    assert pooling["k_included"] == 4
    rd = pooling["measures"]["risk_difference"]
    assert rd["fixed"]["theta"] > 0          # 大中心主导的总体估计为正
    assert rd["heterogeneity"]["I2"] > 50    # 显著异质性
    types = {h["type"] for h in pooling["hints"]}
    assert "direction" in types and "random_stability" in types
    assert pooling["premises"]["primary_pooling"] == "fixed"
    assert "patient.birth_year" in pooling["support_range"]
    protected = {p["center"]: p["status"] for p in run["result"]["protected"]}
    assert protected == {"C5": "no_common_support", "C6": "small_cell_suppressed"}


def test_subcohort_plan_model_failure_and_random_primary(client, researcher1):
    run = _run(client, researcher1, SEEDED_RANDOM_PLAN_VERSION)
    by_center = {c["center_code"]: c for c in run["center_results"]}
    assert by_center["C2"]["status"] == "model_not_converged"
    assert by_center["C5"]["status"] == "no_common_support"
    pooling = run["result"]["pooling"]
    assert pooling["premises"]["primary_pooling"] == "random"
    assert pooling["k_included"] < 5
    assert "unstable_few_centers" in pooling["stability"]


def test_unapproved_plan_cannot_run(client, researcher1, reviewer):
    spec = {
        "cohort_conditions": [
            {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
             "op": "in", "value": ["RD01", "RD02"]}],
        "treatment_variable": "treatment.path",
        "treated": "A", "control": "B",
        "outcome_variable": "followup.outcome", "outcome_value": "remission",
        "covariates": ["patient.sex"],
        "covariate_types": {"patient.sex": "categorical"},
        "measures": ["risk_difference"], "primary_measure": "risk_difference",
        "primary_pooling": "fixed",
    }
    r = client.post("/api/effect-plans", headers=researcher1,
                    json={"name": "未审批方案", "spec": spec})
    assert r.status_code == 201, r.text
    plan = r.json()
    draft_v = plan["versions"][0]["id"]
    r = client.post("/api/effect-runs", headers=researcher1,
                    json={"plan_version_id": draft_v})
    assert r.status_code == 409     # 未获方法学批准不能运行
    # 提交 -> 批准 -> 可运行
    client.post(f"/api/effect-plans/{plan['id']}/versions/{draft_v}/submit",
                headers=researcher1)
    r = client.post(f"/api/effect-plans/versions/{draft_v}/approve",
                    headers=reviewer, json={"comment": "同意"})
    assert r.json()["status"] == "approved"
    run = _run(client, researcher1, draft_v)
    assert run["status"].startswith("completed")


def test_spec_validation(client, researcher1):
    bad = {"cohort_conditions": [], "treatment_variable": "treatment.path",
           "treated": "A", "control": "A",
           "outcome_variable": "followup.outcome", "outcome_value": "remission",
           "primary_measure": "risk_difference", "primary_pooling": "fixed"}
    r = client.post("/api/effect-plans", headers=researcher1,
                    json={"name": "坏规格", "spec": bad})
    assert r.status_code == 400


def test_differencing_guard_and_repool_publish(client, researcher1, reviewer):
    run = _run(client, researcher1, SEEDED_FIXED_PLAN_VERSION)
    r = client.post(f"/api/effect-runs/{run['id']}/outputs", headers=researcher1,
                    json={"title": "主队列效应汇总"})
    out = r.json()
    assert out["flag_count"] == 0

    # 抑制 C4（反向中心）-> 仍在汇总内 -> 差分反推标记 -> 批准被拒
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "suppress_center", "detail": {"center": "C4"}})
    assert r.json()["flag_count"] == 1
    assert r.json()["flags"][0]["type"] == "differencing"
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "approve"})
    assert r.status_code == 409

    # 重新汇总后标记清除，可批准发布
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "repool"})
    assert r.json()["flag_count"] == 0
    assert r.json()["payload"]["pooled_over"] == ["C1", "C2", "C3"]
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "approve", "comment": "重汇总后放行"})
    assert r.json()["status"] == "approved"
    rep_id = r.json()["published_report_id"]

    r = client.get(f"/api/effect-reports/{rep_id}", headers=reviewer)
    rep = r.json()["report"]
    # 报告同时说明总体估计、异质性、可比支持范围与未纳入中心
    payload = rep["payload"]
    assert payload["pooled_over"] == ["C1", "C2", "C3"]
    assert "C4" not in payload["center_diags"]          # 被抑制中心无值泄漏
    protected = {p["center"] for p in payload["protected"]}
    assert protected == {"C4", "C5", "C6"}
    assert payload["pooling"]["measures"]["risk_difference"]["fixed"]["theta"]
    assert payload["pooling"]["hints"]
    assert payload["support_range"]
    prov = rep["provenance"]
    assert prov["run_id"] == run["id"] and prov["pool_version"] == 1
    assert len(prov["batches"]) == 6 and prov["spec_hash"]
    r = client.get(f"/api/effect-reports/{rep_id}/verify", headers=reviewer)
    assert r.json()["intact"] is True


def test_suppress_pooled_also_resolves_differencing(client, researcher1, reviewer):
    run = _run(client, researcher1, SEEDED_FIXED_PLAN_VERSION)
    out = client.post(f"/api/effect-runs/{run['id']}/outputs", headers=researcher1,
                      json={"title": "抑制汇总演示"}).json()
    client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                json={"action": "suppress_center", "detail": {"center": "C4"}})
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "suppress_pooled"})
    assert r.json()["flag_count"] == 0
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "approve"})
    assert r.json()["status"] == "approved"


def test_batch_correction_forms_new_pool_version(client, researcher1, reviewer, admin):
    run1 = _run(client, researcher1, SEEDED_FIXED_PLAN_VERSION)
    out = client.post(f"/api/effect-runs/{run1['id']}/outputs", headers=researcher1,
                      json={"title": "v1 汇总"}).json()
    r = client.post(f"/api/effect-outputs/{out['id']}/decision", headers=reviewer,
                    json={"action": "approve"})
    rep_id = r.json()["published_report_id"]

    # C2 修正批次：登记并锁定新批次
    r = client.post("/api/admin/centers/C2/batches/sync", headers=admin,
                    json={"label": "2026Q4"})
    new_batch = r.json()
    client.post(f"/api/admin/batches/{new_batch['id']}/lock", headers=admin)

    run2 = _run(client, researcher1, SEEDED_FIXED_PLAN_VERSION)
    assert run2["pool_version"] == 2     # 只能形成新的汇总版本
    batches2 = {b["center_code"]: b["batch_id"] for b in run2["batches"]}
    assert batches2["C2"] == new_batch["id"]

    # 旧运行与旧报告保留其中心集合与结论
    old = client.get(f"/api/effect-runs/{run1['id']}", headers=researcher1).json()
    assert old["pool_version"] == 1
    assert {b["center_code"] for b in old["batches"]} == {"C1", "C2", "C3", "C4", "C5", "C6"}
    rep = client.get(f"/api/effect-reports/{rep_id}", headers=researcher1).json()
    assert rep["report"]["provenance"]["pool_version"] == 1
    assert rep["report"]["payload"]["pooled_over"] == ["C1", "C2", "C3", "C4"]
