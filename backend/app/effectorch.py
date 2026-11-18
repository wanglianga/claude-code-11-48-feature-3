"""Effect-run orchestration: snapshot locked batches, dispatch per-center
effect jobs under the common analysis rule, and record each center's local
model outcome. Only aggregate diagnostics that passed the center's own
minimum-cell check are stored; protected categories carry no effect values.

A corrected center batch can only produce a NEW effect run (new pool_version);
old runs keep their center set and conclusions.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .agents import get_agent_client
from .models import (
    Center,
    EffectPlanVersion,
    EffectRun,
    EffectRunBatch,
    EffectRunCenter,
)
from .orchestrator import _mappings_by_center, locked_batches
from .pooling import build_pooling

# Protected per-center categories (no effect values leave the center).
PROTECTED_STATUSES = (
    "insufficient_events",
    "small_cell_suppressed",
    "no_common_support",
    "model_not_converged",
    "missing_mapping",
    "failed",
)

STATUS_REASONS = {
    "insufficient_events": "单中心事件过少（2×2 表存在 0 格），效应无法定义",
    "small_cell_suppressed": "2×2 表存在小于最小单元格的格子，按披露规则本地抑制",
    "no_common_support": "治疗组间协变量无共同支持（不可比）",
    "model_not_converged": "中心本地模型不收敛",
    "missing_mapping": "缺变量映射（覆盖缺口）",
    "failed": "中心执行失败",
    "suppressed_review": "审查阶段抑制（披露保护）",
}

MEASURES_DEFAULT = ["risk_difference", "log_odds_ratio_adjusted"]


def validate_spec(spec: dict) -> None:
    required = ["cohort_conditions", "treatment_variable", "treated", "control",
                "outcome_variable", "outcome_value", "primary_measure",
                "primary_pooling"]
    for key in required:
        if spec.get(key) in (None, "", []):
            raise HTTPException(400, f"spec.{key} 不能为空")
    if spec["treated"] == spec["control"]:
        raise HTTPException(400, "治疗组与对照组取值必须不同")
    if spec["primary_pooling"] not in ("fixed", "random"):
        raise HTTPException(400, "primary_pooling 必须是 fixed 或 random")
    measures = spec.get("measures") or MEASURES_DEFAULT
    if spec["primary_measure"] not in measures:
        raise HTTPException(400, "primary_measure 必须包含在 measures 中")


def create_effect_run(db: Session, plan_version: EffectPlanVersion,
                      user_id: int, k_threshold: int) -> EffectRun:
    if plan_version.status != "approved":
        raise HTTPException(409, "方案版本未获方法学批准，不能启动效应运行")
    locked = locked_batches(db)
    if not locked:
        raise HTTPException(409, "没有已锁定的数据批次，请先由数据管理员锁定批次")

    pool_version = (db.query(EffectRun)
                    .join(EffectPlanVersion,
                          EffectRun.plan_version_id == EffectPlanVersion.id)
                    .filter(EffectPlanVersion.plan_id == plan_version.plan_id)
                    .count() + 1)
    run = EffectRun(plan_version_id=plan_version.id, pool_version=pool_version,
                    spec_snapshot=plan_version.spec,
                    spec_hash=plan_version.spec_hash, status="pending",
                    k_threshold=k_threshold, created_by=user_id)
    db.add(run)
    db.flush()
    for center_id, batch in locked.items():
        db.add(EffectRunBatch(run_id=run.id, center_id=center_id,
                              batch_id=batch.id, batch_label=batch.label))
        db.add(EffectRunCenter(run_id=run.id, center_id=center_id, status="pending"))
    db.commit()
    db.refresh(run)
    return run


def execute_effect_run(db: Session, run: EffectRun) -> EffectRun:
    """Dispatch the effect job to every center in the run's batch snapshot."""
    client = get_agent_client()
    run.status = "running"
    db.commit()

    centers = {c.id: c for c in db.query(Center).all()}
    mappings = _mappings_by_center(db)
    spec = run.spec_snapshot
    effect_spec = {
        "treatment_variable": spec["treatment_variable"],
        "treated": spec["treated"],
        "control": spec["control"],
        "outcome_variable": spec["outcome_variable"],
        "outcome_value": spec["outcome_value"],
        "covariates": spec.get("covariates", []),
        "covariate_types": spec.get("covariate_types", {}),
        "min_cell": run.k_threshold,
    }
    conditions = spec.get("cohort_conditions", [])

    for cr in run.center_results:
        center = centers[cr.center_id]
        job = {
            "job_id": f"effectrun-{run.id}-{center.code}",
            "conditions": conditions,
            "effect_spec": effect_spec,
            "mapping": mappings.get(cr.center_id, {}),
        }
        started = time.monotonic()
        result = client.execute(center.code, job)
        cr.duration_ms = int((time.monotonic() - started) * 1000)
        status = result.get("status", "failed")
        if status == "included":
            cr.status = "included"
            cr.diagnostics = result.get("diagnostics")
            cr.detail = None
            cr.error = None
        elif status in PROTECTED_STATUSES:
            cr.status = status
            cr.diagnostics = None
            cr.detail = result.get("detail")
            cr.error = result.get("error")
        else:
            cr.status = "failed"
            cr.diagnostics = None
            cr.error = result.get("error", "unknown failure")
        db.commit()

    run.finished_at = datetime.now(timezone.utc)
    run.status = ("completed"
                  if all(c.status == "included" for c in run.center_results)
                  else "completed_with_gaps")
    db.commit()
    db.refresh(run)
    return run


def included_rows(run: EffectRun, centers: dict[int, Center]) -> list[dict]:
    return [{"center": centers[cr.center_id].code, "diagnostics": cr.diagnostics}
            for cr in run.center_results
            if cr.status == "included" and cr.diagnostics]


def protected_rows(run: EffectRun, centers: dict[int, Center]) -> list[dict]:
    out = []
    for cr in run.center_results:
        if cr.status in ("included", "pending"):
            continue
        out.append({
            "center": centers[cr.center_id].code,
            "status": cr.status,
            "reason": STATUS_REASONS.get(cr.status, cr.status),
            "detail": cr.detail,
            "error": cr.error,
        })
    return out


def pooling_payload(run: EffectRun, centers: dict[int, Center]) -> dict:
    """Coordinator-side pooling over the included centers' diagnostics."""
    spec = run.spec_snapshot
    rows = included_rows(run, centers)
    pooling = build_pooling(
        rows,
        primary_measure=spec.get("primary_measure", "risk_difference"),
        primary_pooling=spec.get("primary_pooling", "fixed"),
    )
    return {
        "pooling": pooling,
        "center_diags": {r["center"]: r["diagnostics"] for r in rows},
        "protected": protected_rows(run, centers),
    }


def build_output_payload(run: EffectRun, centers: dict[int, Center]) -> dict:
    """Frozen payload for a new EffectOutput (review may modify it afterwards)."""
    pp = pooling_payload(run, centers)
    pooled_over = sorted(pp["center_diags"])
    return {
        "pooling": pp["pooling"],
        "center_diags": pp["center_diags"],
        "protected": pp["protected"],
        "suppressed_centers": [],
        "pooled_over": pooled_over,
        "pooled_suppressed": False,
        "coverage": {
            "included_centers": pooled_over,
            "protected_centers": [p["center"] for p in pp["protected"]],
            "complete": not pp["protected"],
        },
        "premises": pp["pooling"]["premises"],
        "support_range": pp["pooling"]["support_range"],
    }


def analyze_effect_payload(payload: dict) -> list[dict]:
    """Differencing guard: a suppressed center must not remain inside a visible
    pooled estimate (pooled + the other centers' rows would reveal it)."""
    flags: list[dict] = []
    suppressed = set(payload.get("suppressed_centers", []))
    pooled_over = set(payload.get("pooled_over", []))
    if payload.get("pooled_suppressed"):
        return flags
    for code in sorted(suppressed & pooled_over):
        flags.append({
            "type": "differencing", "center": code,
            "text": (f"中心 {code} 已被抑制但仍包含在当前汇总估计中，"
                     "合并后可由差分反推；请重新汇总（repool）或抑制汇总估计。"),
        })
    return flags


def repool_payload(payload: dict) -> dict:
    """Recompute pooling over the non-suppressed centers (review action)."""
    import copy

    p = copy.deepcopy(payload)
    suppressed = set(p.get("suppressed_centers", []))
    rows = [{"center": code, "diagnostics": diag}
            for code, diag in p["center_diags"].items() if code not in suppressed]
    spec_measure = p["pooling"].get("measure_primary", "risk_difference")
    primary_pooling = p.get("premises", {}).get("primary_pooling", "fixed")
    p["pooling"] = build_pooling(rows, spec_measure, primary_pooling)
    p["pooled_over"] = sorted(r["center"] for r in rows)
    p["premises"] = p["pooling"]["premises"]
    p["support_range"] = p["pooling"]["support_range"]
    return p
