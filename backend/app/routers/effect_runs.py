"""Effect-run endpoints: start a coordinated run from an approved plan
version, inspect per-center local diagnostics and the pooled result."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import current_user, require_role
from ..effectorch import (
    STATUS_REASONS,
    create_effect_run,
    execute_effect_run,
    pooling_payload,
)
from ..models import (
    Center,
    EffectPlan,
    EffectPlanVersion,
    EffectRun,
    EffectRunBatch,
    User,
)

router = APIRouter(prefix="/api/effect-runs", tags=["effect-runs"])
researcher = require_role("researcher")


def _center_map(db: Session) -> dict[int, Center]:
    return {c.id: c for c in db.query(Center).all()}


def run_summary(run: EffectRun, db: Session) -> dict:
    pv = run.plan_version
    included = sum(1 for c in run.center_results if c.status == "included")
    return {
        "id": run.id, "status": run.status, "pool_version": run.pool_version,
        "plan_id": pv.plan_id if pv else None,
        "plan_version_id": run.plan_version_id,
        "plan_version_no": pv.version_no if pv else None,
        "primary_pooling": run.spec_snapshot.get("primary_pooling"),
        "primary_measure": run.spec_snapshot.get("primary_measure"),
        "k_threshold": run.k_threshold,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "centers_total": len(run.center_results),
        "centers_included": included,
    }


def run_detail(run: EffectRun, db: Session) -> dict:
    centers = _center_map(db)
    batches = db.query(EffectRunBatch).filter(EffectRunBatch.run_id == run.id).all()
    center_results = []
    for cr in run.center_results:
        c = centers[cr.center_id]
        batch = next((b for b in batches if b.center_id == cr.center_id), None)
        center_results.append({
            "center_code": c.code, "center_name": c.name,
            "status": cr.status,
            "status_reason": STATUS_REASONS.get(cr.status),
            "error": cr.error, "detail": cr.detail,
            "duration_ms": cr.duration_ms,
            "batch_id": batch.batch_id if batch else None,
            "batch_label": batch.batch_label if batch else None,
            "diagnostics": cr.diagnostics,
        })
    d = run_summary(run, db)
    d.update({
        "spec": run.spec_snapshot,
        "spec_hash": run.spec_hash,
        "batches": [{"center_code": centers[b.center_id].code,
                     "batch_id": b.batch_id, "batch_label": b.batch_label}
                    for b in batches],
        "center_results": center_results,
        "result": pooling_payload(run, centers),
    })
    return d


class CreateRunIn(BaseModel):
    plan_version_id: int


@router.post("", status_code=201)
def create(body: CreateRunIn, background: BackgroundTasks,
           db: Session = Depends(get_db), user: User = Depends(researcher)):
    pv = db.get(EffectPlanVersion, body.plan_version_id)
    if not pv:
        raise HTTPException(404, "unknown plan version")
    run = create_effect_run(db, pv, user.id, settings.small_cell_k)
    if settings.run_execution == "sync":
        execute_effect_run(db, run)
    else:
        run_id = run.id

        def _bg():
            with SessionLocal() as s:
                execute_effect_run(s, s.get(EffectRun, run_id))

        background.add_task(_bg)
    return run_detail(run, db)


@router.get("")
def list_runs(db: Session = Depends(get_db), _: User = Depends(current_user)):
    runs = db.query(EffectRun).order_by(EffectRun.id.desc()).all()
    return [run_summary(r, db) for r in runs]


@router.get("/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db),
            _: User = Depends(current_user)):
    run = db.get(EffectRun, run_id)
    if not run:
        raise HTTPException(404, "unknown effect run")
    return run_detail(run, db)


# --- effect outputs are created from a finished effect run -------------------

from ..effectorch import analyze_effect_payload, build_output_payload  # noqa: E402
from ..models import EffectOutput  # noqa: E402


class CreateOutputIn(BaseModel):
    title: str


@router.post("/{run_id}/outputs", status_code=201)
def create_output(run_id: int, body: CreateOutputIn,
                  db: Session = Depends(get_db), user: User = Depends(researcher)):
    run = db.get(EffectRun, run_id)
    if not run:
        raise HTTPException(404, "unknown effect run")
    if run.status not in ("completed", "completed_with_gaps"):
        raise HTTPException(409, f"运行状态为 {run.status}，不能生成输出")
    payload = build_output_payload(run, _center_map(db))
    o = EffectOutput(run_id=run.id, title=body.title, created_by=user.id,
                     payload=payload, flags=analyze_effect_payload(payload),
                     k_threshold=run.k_threshold)
    db.add(o)
    db.commit()
    db.refresh(o)
    from .effect_outputs import output_view
    return output_view(o, db, full=True)
