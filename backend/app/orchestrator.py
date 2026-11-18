"""Run orchestration: snapshot locked batches, dispatch per-center jobs, record
per-center outcomes. A run always references the batches locked at start time;
batch replacement during a run only affects later runs."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .agents import get_agent_client
from .models import (
    Center,
    CenterMapping,
    DataBatch,
    Run,
    RunBatch,
    RunCenterResult,
    RuleVersion,
)


def locked_batches(db: Session) -> dict[int, DataBatch]:
    """center_id -> currently locked batch."""
    rows = db.query(DataBatch).filter(DataBatch.status == "locked").all()
    return {b.center_id: b for b in rows}


def create_run(db: Session, rule_version: RuleVersion, request_spec: dict,
               request_hash: str, user_id: int, k_threshold: int) -> Run:
    if rule_version.status != "approved":
        raise HTTPException(409, "rule version is not approved; runs require an approved rule")
    locked = locked_batches(db)
    if not locked:
        raise HTTPException(409, "no locked data batches; ask a data admin to lock batches first")

    run = Run(rule_version_id=rule_version.id, request_spec=request_spec,
              request_hash=request_hash, status="pending",
              k_threshold=k_threshold, created_by=user_id)
    db.add(run)
    db.flush()
    for center_id, batch in locked.items():
        db.add(RunBatch(run_id=run.id, center_id=center_id, batch_id=batch.id,
                        batch_label=batch.label))
        db.add(RunCenterResult(run_id=run.id, center_id=center_id, status="pending"))
    db.commit()
    db.refresh(run)
    return run


def execute_run(db: Session, run: Run) -> Run:
    """Dispatch the job to every center in the run's batch snapshot."""
    client = get_agent_client()
    run.status = "running"
    db.commit()

    centers = {c.id: c for c in db.query(Center).all()}
    mappings = _mappings_by_center(db)
    job_base = {
        "job_id": f"run-{run.id}",
        "conditions": run.rule_version.conditions,
        "request_spec": run.request_spec,
    }

    for cr in run.center_results:
        center = centers[cr.center_id]
        job = dict(job_base)
        job["job_id"] = f"run-{run.id}-{center.code}"
        job["mapping"] = mappings.get(cr.center_id, {})
        started = time.monotonic()
        result = client.execute(center.code, job)
        elapsed = int((time.monotonic() - started) * 1000)
        cr.duration_ms = elapsed
        status = result.get("status", "failed")
        if status == "success":
            cr.status = "success"
            cr.aggregates = {"groups": result.get("groups", []),
                             "total_n": result.get("total_n", 0)}
            cr.error = None
        elif status == "missing_mapping":
            cr.status = "missing_mapping"
            cr.error = result.get("error", "missing variable mapping")
        else:
            cr.status = "failed"
            cr.error = result.get("error", "unknown failure")
        db.commit()

    run.finished_at = datetime.now(timezone.utc)
    run.status = ("completed" if all(c.status == "success" for c in run.center_results)
                  else "completed_with_gaps")
    db.commit()
    db.refresh(run)
    return run


def _mappings_by_center(db: Session) -> dict[int, dict]:
    """center_id -> {variable: {field, value_map}} for the active dictionary."""
    rows = db.query(CenterMapping).all()
    out: dict[int, dict] = {}
    for m in rows:
        if m.status != "mapped" or not m.local_field:
            continue
        out.setdefault(m.center_id, {})[m.variable_name] = {
            "field": m.local_field,
            "value_map": m.value_map,
        }
    return out
