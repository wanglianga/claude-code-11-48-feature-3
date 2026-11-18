"""End-to-end review flow: small-sample output is blocked, reviewer merges and
suppresses, output is approved and published with full provenance."""
from __future__ import annotations

import pytest
from conftest import run_payload  # noqa: F401  (imported for reuse clarity)

# cohort slice engineered to contain groups below k=5 (the exact small groups
# depend on the demo data; the flow below discovers them from the flags)
SMALL_RULE_CONDITIONS = [
    {"cid": "inc-dx", "kind": "include", "variable": "diagnosis.code",
     "op": "eq", "value": "RD03"},
    {"cid": "inc-yob", "kind": "include", "variable": "patient.birth_year",
     "op": "ge", "value": 2005},
]
SMALL_SPEC = {
    "group_by": ["treatment.path", "patient.sex"],
    "metrics": [{"type": "count"},
                {"type": "outcome_count", "variable": "followup.outcome",
                 "value": "remission"}],
}


def _flagged_groups(output: dict) -> list[str]:
    return sorted({f["group"] for f in output["flags"] if f["group"] != "__total__"})


def _cells_with_totals(flags: list[dict]) -> list[dict]:
    """Flagged cells plus the column total whenever a differencing flag is
    present (a single hidden cell is derivable from a visible total)."""
    cells = [{"group": f["group"], "metric": f["metric"]} for f in flags]
    for f in flags:
        if f["reason"] == "differencing":
            cells.append({"group": "__total__", "metric": f["metric"]})
    return cells


@pytest.fixture()
def small_run(client, researcher1, reviewer):
    r = client.post("/api/rules", headers=researcher1,
                    json={"name": "小样本队列", "conditions": SMALL_RULE_CONDITIONS})
    rule = r.json()
    v1 = rule["versions"][0]
    client.post(f"/api/rules/{rule['id']}/versions/{v1['id']}/submit",
                headers=researcher1)
    client.post(f"/api/rules/versions/{v1['id']}/approve",
                headers=reviewer, json={"comment": "ok"})
    r = client.post("/api/runs", headers=researcher1,
                    json={"rule_version_id": v1["id"], "request_spec": SMALL_SPEC})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "completed"
    return r.json()


def test_small_sample_output_blocked_then_merged_and_published(
        client, researcher1, reviewer, small_run):
    run_id = small_run["id"]
    r = client.post(f"/api/runs/{run_id}/outputs", headers=researcher1,
                    json={"title": "RD03 青少年队列结局"})
    assert r.status_code == 201, r.text
    output = r.json()
    assert output["status"] == "pending_review"
    assert output["flag_count"] > 0
    small_groups = _flagged_groups(output)
    assert len(small_groups) >= 2

    # a plain approve is refused while flags are unresolved
    r = client.post(f"/api/outputs/{output['id']}/decision", headers=reviewer,
                    json={"action": "approve", "comment": "try"})
    assert r.status_code == 409

    # step 1: reviewer merges the two smallest flagged groups
    sizes = {g["label"]: g["n"] for g in output["table_data"]["groups"]}
    pair = sorted(small_groups, key=lambda g: sizes.get(g, 0))[:2]
    r = client.post(f"/api/outputs/{output['id']}/decision", headers=reviewer,
                    json={"action": "merge",
                          "detail": {"groups": pair, "into": "合并组"},
                          "comment": "合并小组"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_review"

    # step 2: suppress the remaining flagged cells (with totals when a single
    # hidden cell would be derivable by differencing)
    cells = _cells_with_totals(body["flags"])
    r = client.post(f"/api/outputs/{output['id']}/decision", headers=reviewer,
                    json={"action": "suppress", "detail": {"cells": cells},
                          "comment": "抑制剩余小格后放行"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["flag_count"] == 0
    report_id = body["published_report_id"]

    # published report carries full provenance and verifies cleanly
    r = client.get(f"/api/reports/{report_id}", headers=reviewer)
    rep = r.json()
    prov = rep["report"]["provenance"]
    assert prov["run_id"] == run_id
    assert prov["rule_version_no"] == 1
    assert len(prov["batches"]) == 6
    assert prov["review"]["action"] == "suppress"
    assert rep["report_hash"]
    r = client.get(f"/api/reports/{report_id}/verify", headers=reviewer)
    assert r.json()["intact"] is True


def test_suppress_path_also_releases(client, researcher1, reviewer, small_run):
    r = client.post(f"/api/runs/{small_run['id']}/outputs", headers=researcher1,
                    json={"title": "抑制路径验证"})
    output = r.json()
    cells = _cells_with_totals(output["flags"])
    r = client.post(f"/api/outputs/{output['id']}/decision", headers=reviewer,
                    json={"action": "suppress", "detail": {"cells": cells},
                          "comment": "抑制全部小格"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved", r.text


def test_researcher_cannot_review(client, researcher1, small_run):
    r = client.post(f"/api/runs/{small_run['id']}/outputs", headers=researcher1,
                    json={"title": "越权测试"})
    output = r.json()
    r = client.post(f"/api/outputs/{output['id']}/decision", headers=researcher1,
                    json={"action": "reject"})
    assert r.status_code == 403
