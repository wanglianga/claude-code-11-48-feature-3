#!/usr/bin/env python
"""End-to-end smoke verification against a RUNNING platform (real PostgreSQL +
HTTP center agents). Exercises every acceptance scenario:

  1. seeded users / centers / locked batches
  2. run over 6 centers; missing-mapping center is a coverage gap, not a zero
  3. batch replacement mid-stream: old run keeps its snapshot, new run picks up
  4. concurrent rule editing: 409 + condition-level diff + confirmed merge;
     in-review restrictions cannot be dropped silently
  5. small-sample output: review blocks, merge+suppress releases, published
     report carries full provenance and hash-verifies
  6. run comparison endpoint
  7. cross-center effect aggregation: methodology-approved plans; large vs
     small centres with opposite effects; no-overlap / small-cell / model-
     failure centres enter protected categories (never zero-filled); fixed &
     random pooling with heterogeneity hints and few-centre instability
     warnings; review-time centre suppression triggers the differencing guard
     until re-pooled; batch correction forms a new pool version while old
     reports keep their centre set

Usage: .venv/bin/python scripts/smoke.py  [BASE_URL]
"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
CHECKS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def login(username: str, password: str) -> dict:
    r = httpx.post(f"{BASE}/api/auth/login",
                   json={"username": username, "password": password}, timeout=10)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def main() -> None:
    admin = login("admin", "admin123")
    r1 = login("researcher1", "research123")
    r2 = login("researcher2", "research123")
    rev = login("reviewer", "review123")
    check("四类账号登录", True)

    centers = httpx.get(f"{BASE}/api/admin/centers", headers=admin).json()
    check("六家中心均已注册并锁定批次",
          len(centers) == 6 and all(c["locked_batch"] for c in centers))

    # ---- 2. runs & coverage gaps ------------------------------------------------
    spec = {"group_by": ["treatment.path"],
            "metrics": [{"type": "count"},
                        {"type": "outcome_count", "variable": "followup.outcome",
                         "value": "remission"}]}
    run_full = httpx.post(f"{BASE}/api/runs", headers=r1,
                          json={"rule_version_id": 1, "request_spec": spec}).json()
    check("全量运行完成（6/6 中心成功）",
          run_full["status"] == "completed" and run_full["centers_failed"] == 0)
    c5_n = next(c for c in run_full["center_results"]
                if c["center_code"] == "C5")["total_n"]

    spec_lab = dict(spec, metrics=spec["metrics"] + [
        {"type": "mean", "variable": "followup.lab_value"}])
    run_gap = httpx.post(f"{BASE}/api/runs", headers=r1,
                         json={"rule_version_id": 1, "request_spec": spec_lab}).json()
    c5 = next(c for c in run_gap["center_results"] if c["center_code"] == "C5")
    cov = run_gap["result"]["coverage"]
    check("C5 缺 lab 字段 → 标记 missing_mapping 而非零例",
          run_gap["status"] == "completed_with_gaps"
          and c5["status"] == "missing_mapping" and c5["total_n"] is None
          and [e["center"] for e in cov["excluded_centers"]] == ["C5"])
    check("覆盖缺口不计入总计",
          run_gap["result"]["totals"]["n"] == run_full["result"]["totals"]["n"] - c5_n,
          f"{run_gap['result']['totals']['n']} = "
          f"{run_full['result']['totals']['n']} - {c5_n}")

    # ---- 3. batch replacement keeps run snapshots -------------------------------
    batches_a = {b["center_code"]: b["batch_id"] for b in run_full["batches"]}
    new_batch = httpx.post(f"{BASE}/api/admin/centers/C2/batches/sync",
                           headers=admin, json={"label": "2026Q4"}).json()
    httpx.post(f"{BASE}/api/admin/batches/{new_batch['id']}/lock", headers=admin)
    again = httpx.get(f"{BASE}/api/runs/{run_full['id']}", headers=r1).json()
    check("批次替换后旧运行仍引用启动时锁定的批次",
          {b["center_code"]: b["batch_id"] for b in again["batches"]} == batches_a
          and again["result"]["totals"]["n"] == run_full["result"]["totals"]["n"])
    run_new = httpx.post(f"{BASE}/api/runs", headers=r1,
                         json={"rule_version_id": 1, "request_spec": spec}).json()
    batches_b = {b["center_code"]: b["batch_id"] for b in run_new["batches"]}
    check("新运行使用替换后的批次", batches_b["C2"] == new_batch["id"]
          and batches_b["C2"] != batches_a["C2"])

    cmp_res = httpx.get(f"{BASE}/api/runs/compare?a={run_full['id']}&b={run_new['id']}",
                        headers=r1).json()
    check("运行对比接口返回两次运行的批次与结果",
          cmp_res["a"]["batches"] and cmp_res["b"]["batches"])

    # ---- 4. concurrent rule editing ---------------------------------------------
    conds = [
        {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
         "op": "in", "value": ["RD01", "RD02"]},
        {"cid": "exc-kid", "kind": "exclude", "variable": "patient.birth_year",
         "op": "gt", "value": 2015},
    ]
    rule = httpx.post(f"{BASE}/api/rules", headers=r1,
                      json={"name": "并发演示规则", "conditions": conds}).json()
    v1 = rule["versions"][0]
    httpx.post(f"{BASE}/api/rules/{rule['id']}/versions/{v1['id']}/submit", headers=r1)

    conds_r1 = conds + [{"cid": "inc-sex", "kind": "include",
                         "variable": "patient.sex", "op": "eq", "value": "F"}]
    saved = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r1,
                      json={"base_version_id": v1["id"], "conditions": conds_r1}).json()
    v2 = next(v for v in saved["rule"]["versions"] if v["version_no"] == 2)

    conflict = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                         json={"base_version_id": v1["id"],
                               "conditions": [conds[0]]})  # drops the exclusion
    check("并发保存返回 409 与条件级差异", conflict.status_code == 409
          and conflict.json()["error"] == "concurrent_modification"
          and conflict.json()["their_diff"]["added"])
    merged = conflict.json()["merged"]
    # merged drops the in-review exclusion -> must also be acknowledged
    refused = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                        json={"base_version_id": v2["id"], "conditions": merged,
                              "confirm_merge": True})
    check("合并结果删除在审排除条件 → 需显式确认",
          refused.status_code == 409
          and refused.json()["error"] == "restriction_removal")
    confirmed = httpx.put(f"{BASE}/api/rules/{rule['id']}/save", headers=r2,
                          json={"base_version_id": v2["id"], "conditions": merged,
                                "confirm_merge": True,
                                "acknowledge_restriction_removal": True})
    check("确认合并后保存成功且在审版本未被抹掉",
          confirmed.status_code == 200
          and any(v["status"] == "in_review"
                  for v in confirmed.json()["rule"]["versions"]))

    # ---- 5. small-sample review flow --------------------------------------------
    small_conds = [
        {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
         "op": "eq", "value": "RD03"},
        {"cid": "inc-yob", "kind": "include", "variable": "patient.birth_year",
         "op": "ge", "value": 2005},
    ]
    srule = httpx.post(f"{BASE}/api/rules", headers=r1,
                       json={"name": "小样本队列", "conditions": small_conds}).json()
    sv1 = srule["versions"][0]
    httpx.post(f"{BASE}/api/rules/{srule['id']}/versions/{sv1['id']}/submit", headers=r1)
    httpx.post(f"{BASE}/api/rules/versions/{sv1['id']}/approve",
               headers=rev, json={"comment": "同意"})
    srun = httpx.post(f"{BASE}/api/runs", headers=r1, json={
        "rule_version_id": sv1["id"],
        "request_spec": {"group_by": ["treatment.path", "patient.sex"],
                         "metrics": [{"type": "count"},
                                     {"type": "outcome_count",
                                      "variable": "followup.outcome",
                                      "value": "remission"}]}}).json()
    out = httpx.post(f"{BASE}/api/runs/{srun['id']}/outputs", headers=r1,
                     json={"title": "小样本输出（冒烟）"}).json()
    check("小样本输出被标记并进入审查队列", out["flag_count"] > 0
          and out["status"] == "pending_review")
    blocked = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision",
                         headers=rev, json={"action": "approve"})
    check("存在未处理标记时批准被拒绝", blocked.status_code == 409)
    # merge the two smallest flagged groups, then suppress the remaining cells
    sizes = {g["label"]: g["n"] for g in out["table_data"]["groups"]}
    flagged_groups = sorted({f["group"] for f in out["flags"] if f["group"] != "__total__"},
                            key=lambda g: sizes.get(g, 0))
    step = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision", headers=rev,
                      json={"action": "merge",
                            "detail": {"groups": flagged_groups[:2],
                                       "into": "合并组"}}).json()
    cells = [{"group": f["group"], "metric": f["metric"]} for f in step["flags"]]
    cells += [{"group": "__total__", "metric": f["metric"]}
              for f in step["flags"] if f["reason"] == "differencing"]
    done = httpx.post(f"{BASE}/api/outputs/{out['id']}/decision", headers=rev,
                      json={"action": "suppress", "detail": {"cells": cells},
                            "comment": "合并+抑制后放行"}).json()
    check("合并 + 抑制后输出获批并发布",
          done["status"] == "approved" and done.get("published_report_id"))
    rep = httpx.get(f"{BASE}/api/reports/{done['published_report_id']}",
                    headers=rev).json()
    prov = rep["report"]["provenance"]
    check("发布报告引用完整运行版本（规则/批次/审查）",
          prov["run_id"] == srun["id"] and len(prov["batches"]) == 6
          and prov["conditions_hash"] and prov["review"]["action"] == "suppress")
    ver = httpx.get(f"{BASE}/api/reports/{done['published_report_id']}/verify",
                    headers=rev).json()
    check("报告哈希可复现校验", ver["intact"] is True)

    # ---- 6. cross-center effect aggregation ------------------------------------
    plans = httpx.get(f"{BASE}/api/effect-plans", headers=r1).json()
    fixed_plan = next(p for p in plans if "固定效应" in p["name"])
    random_plan = next(p for p in plans if "随机效应" in p["name"])
    check("种子效应方案已获方法学批准（固定/随机各一）",
          bool(fixed_plan["approved_version_id"]) and bool(random_plan["approved_version_id"]))

    # 6a. fixed-primary run: opposite directions, protected categories, hints
    erun = httpx.post(f"{BASE}/api/effect-runs", headers=r1,
                      json={"plan_version_id": fixed_plan["approved_version_id"]}).json()
    by_center = {c["center_code"]: c for c in erun["center_results"]}
    check("主队列：C1-C4 纳入、C5 无共同支持、C6 小单元格抑制",
          all(by_center[c]["status"] == "included" for c in ("C1", "C2", "C3", "C4"))
          and by_center["C5"]["status"] == "no_common_support"
          and by_center["C6"]["status"] == "small_cell_suppressed")
    check("保护类别中心不传出效应值（无零效应补位）",
          by_center["C5"]["diagnostics"] is None and by_center["C6"]["diagnostics"] is None)
    thetas = {c: (by_center[c]["diagnostics"]["effects"]["risk_difference"]["theta"])
              for c in ("C1", "C2", "C3", "C4")}
    check("大中心与小中心方向相反",
          all(thetas[c] > 0 for c in ("C1", "C2", "C3")) and thetas["C4"] < 0)
    pooling = erun["result"]["pooling"]
    rd = pooling["measures"]["risk_difference"]
    hint_types = {h["type"] for h in pooling["hints"]}
    check("汇总：总体估计+显著异质性+方向不一致提示+少中心随机效应不稳定",
          rd["fixed"]["theta"] > 0 and rd["heterogeneity"]["I2"] > 50
          and "direction" in hint_types and "random_stability" in hint_types
          and "unstable_few_centers" in pooling["stability"])
    check("报告要素：方法学前提+可比支持范围+贡献范围+未纳入中心",
          pooling["premises"]["primary_pooling"] == "fixed"
          and "patient.birth_year" in pooling["support_range"]
          and rd["contribution_range"]["min"]["center"] == "C4"
          and {p["center"] for p in erun["result"]["protected"]} == {"C5", "C6"})

    # 6b. random-primary sub-cohort run: single-centre model failure
    erun2 = httpx.post(f"{BASE}/api/effect-runs", headers=r1,
                       json={"plan_version_id": random_plan["approved_version_id"]}).json()
    by2 = {c["center_code"]: c for c in erun2["center_results"]}
    check("子队列：C2 本地模型不收敛进入保护类别，随机效应为主且标注不稳定",
          by2["C2"]["status"] == "model_not_converged"
          and erun2["result"]["pooling"]["premises"]["primary_pooling"] == "random"
          and "unstable_few_centers" in erun2["result"]["pooling"]["stability"])

    # 6c. review: suppress a centre -> differencing guard -> repool -> publish
    eout = httpx.post(f"{BASE}/api/effect-runs/{erun['id']}/outputs", headers=r1,
                      json={"title": "主队列效应汇总（冒烟）"}).json()
    r = httpx.post(f"{BASE}/api/effect-outputs/{eout['id']}/decision", headers=rev,
                   json={"action": "suppress_center", "detail": {"center": "C4"}})
    check("抑制纳入中心触发差分反推标记",
          r.json()["flag_count"] == 1 and r.json()["flags"][0]["type"] == "differencing")
    blocked = httpx.post(f"{BASE}/api/effect-outputs/{eout['id']}/decision",
                         headers=rev, json={"action": "approve"})
    check("存在差分反推标记时批准被拒绝", blocked.status_code == 409)
    r = httpx.post(f"{BASE}/api/effect-outputs/{eout['id']}/decision", headers=rev,
                   json={"action": "repool"})
    check("重新汇总后标记清除", r.json()["flag_count"] == 0
          and r.json()["payload"]["pooled_over"] == ["C1", "C2", "C3"])
    done = httpx.post(f"{BASE}/api/effect-outputs/{eout['id']}/decision", headers=rev,
                      json={"action": "approve", "comment": "重汇总后放行"}).json()
    check("效应输出获批并发布", done["status"] == "approved"
          and done.get("published_report_id"))
    rep = httpx.get(f"{BASE}/api/effect-reports/{done['published_report_id']}",
                    headers=rev).json()
    payload = rep["report"]["payload"]
    prov = rep["report"]["provenance"]
    check("效应报告：总体估计/异质性/支持范围/未纳入中心齐备且溯源完整",
          payload["pooling"]["measures"]["risk_difference"]["fixed"]["theta"] is not None
          and payload["pooling"]["hints"] and payload["support_range"]
          and {p["center"] for p in payload["protected"]} == {"C4", "C5", "C6"}
          and "C4" not in payload["center_diags"]
          and prov["pool_version"] == erun["pool_version"] and len(prov["batches"]) == 6)
    ver = httpx.get(f"{BASE}/api/effect-reports/{done['published_report_id']}/verify",
                    headers=rev).json()
    check("效应报告哈希可复现校验", ver["intact"] is True)

    # 6d. batch correction forms a NEW pool version; old report keeps its set
    new_batch = httpx.post(f"{BASE}/api/admin/centers/C2/batches/sync",
                           headers=admin, json={"label": "2026Q4-效应"}).json()
    httpx.post(f"{BASE}/api/admin/batches/{new_batch['id']}/lock", headers=admin)
    erun3 = httpx.post(f"{BASE}/api/effect-runs", headers=r1,
                       json={"plan_version_id": fixed_plan["approved_version_id"]}).json()
    check("中心修正批次后只形成新的汇总版本",
          erun3["pool_version"] == erun["pool_version"] + 1
          and {b["center_code"]: b["batch_id"] for b in erun3["batches"]}["C2"]
          == new_batch["id"])
    old = httpx.get(f"{BASE}/api/effect-reports/{done['published_report_id']}",
                    headers=r1).json()
    check("旧报告保留其中心集合与汇总版本",
          old["report"]["provenance"]["pool_version"] == erun["pool_version"]
          and old["report"]["payload"]["pooled_over"] == ["C1", "C2", "C3"])

    # 6e. methodology approval gate: unapproved plan cannot run
    spec = {"cohort_conditions": [
                {"cid": "i1", "kind": "include", "variable": "diagnosis.code",
                 "op": "in", "value": ["RD01", "RD02"]}],
            "treatment_variable": "treatment.path", "treated": "A", "control": "B",
            "outcome_variable": "followup.outcome", "outcome_value": "remission",
            "covariates": ["patient.sex"],
            "covariate_types": {"patient.sex": "categorical"},
            "measures": ["risk_difference"], "primary_measure": "risk_difference",
            "primary_pooling": "random"}
    plan = httpx.post(f"{BASE}/api/effect-plans", headers=r1,
                      json={"name": "冒烟·未审批方案", "spec": spec}).json()
    draft_v = plan["versions"][0]["id"]
    refused = httpx.post(f"{BASE}/api/effect-runs", headers=r1,
                         json={"plan_version_id": draft_v})
    check("未获方法学批准的方案不能运行", refused.status_code == 409)
    httpx.post(f"{BASE}/api/effect-plans/{plan['id']}/versions/{draft_v}/submit",
               headers=r1)
    httpx.post(f"{BASE}/api/effect-plans/versions/{draft_v}/approve",
               headers=rev, json={"comment": "方法学同意"})
    ok_run = httpx.post(f"{BASE}/api/effect-runs", headers=r1,
                        json={"plan_version_id": draft_v}).json()
    check("方法学批准后可运行", ok_run["status"].startswith("completed"))

    print(f"\n全部 {len(CHECKS)} 项冒烟检查通过")


if __name__ == "__main__":
    main()
