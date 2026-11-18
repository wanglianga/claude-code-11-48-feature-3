"""Effect outputs and the methodology/disclosure review queue.

Review actions mirror the aggregate-output review but operate on centers:
- suppress_center: move an included center into the protected display
  category (its diagnostics are removed from the payload),
- repool: recompute the pooled estimate over the remaining centers,
- suppress_pooled: hide the pooled estimate itself,
- approve / reject.

The differencing guard blocks approval while a suppressed center is still
inside a visible pooled estimate (pooled + the other centers' rows would
reveal it). Suppressed centers are never zero-filled.
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role
from ..effectorch import (
    STATUS_REASONS,
    analyze_effect_payload,
    repool_payload,
)
from ..models import (
    Center,
    EffectOutput,
    EffectReviewDecision,
    EffectRun,
    EffectRunBatch,
    PublishedEffectReport,
    User,
)
from ..security import content_hash

router = APIRouter(prefix="/api", tags=["effect-outputs"])
reviewer = require_role("reviewer")


def output_view(o: EffectOutput, db: Session, full: bool = False) -> dict:
    creator = db.get(User, o.created_by)
    out = {
        "id": o.id, "run_id": o.run_id, "title": o.title, "status": o.status,
        "created_by": creator.display_name if creator else o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "k_threshold": o.k_threshold,
        "flag_count": len(o.flags),
    }
    if full:
        out["payload"] = o.payload
        out["flags"] = o.flags
        out["decisions"] = [
            {"id": d.id, "action": d.action, "detail": d.detail,
             "comment": d.comment,
             "reviewer": db.get(User, d.reviewer_id).display_name,
             "decided_at": d.decided_at.isoformat()}
            for d in db.query(EffectReviewDecision)
            .filter(EffectReviewDecision.output_id == o.id)
            .order_by(EffectReviewDecision.id)
        ]
    return out


@router.get("/effect-outputs")
def list_outputs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = db.query(EffectOutput).order_by(EffectOutput.id.desc())
    if user.role == "researcher":
        q = q.filter(EffectOutput.created_by == user.id)
    return [output_view(o, db) for o in q.all()]


@router.get("/effect-outputs/{output_id}")
def get_output(output_id: int, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    o = db.get(EffectOutput, output_id)
    if not o:
        raise HTTPException(404, "unknown effect output")
    if user.role == "researcher" and o.created_by != user.id:
        raise HTTPException(403, "not your output")
    return output_view(o, db, full=True)


class DecisionIn(BaseModel):
    action: str  # suppress_center | repool | suppress_pooled | approve | reject
    detail: dict = {}
    comment: str | None = None


def _publish(db: Session, o: EffectOutput, decision: EffectReviewDecision,
             reviewer_user: User) -> PublishedEffectReport:
    run = db.get(EffectRun, o.run_id)
    centers = {c.id: c for c in db.query(Center).all()}
    batches = db.query(EffectRunBatch).filter(EffectRunBatch.run_id == run.id).all()
    report = {
        "title": o.title,
        "payload": o.payload,
        "provenance": {
            "run_id": run.id,
            "pool_version": run.pool_version,
            "plan_id": run.plan_version.plan_id,
            "plan_version_id": run.plan_version_id,
            "plan_version_no": run.plan_version.version_no,
            "spec_snapshot": run.spec_snapshot,
            "spec_hash": run.spec_hash,
            "batches": [{"center_code": centers[b.center_id].code,
                         "batch_id": b.batch_id, "batch_label": b.batch_label}
                        for b in batches],
            "k_threshold": run.k_threshold,
            "review": {"decision_id": decision.id,
                       "reviewer": reviewer_user.display_name,
                       "action": decision.action,
                       "decided_at": decision.decided_at.isoformat(),
                       "comment": decision.comment},
        },
    }
    pub = PublishedEffectReport(
        output_id=o.id, run_id=run.id, plan_version_id=run.plan_version_id,
        decision_id=decision.id, published_by=reviewer_user.id,
        report=report, report_hash=content_hash(report),
    )
    db.add(pub)
    return pub


@router.post("/effect-outputs/{output_id}/decision")
def decide(output_id: int, body: DecisionIn, db: Session = Depends(get_db),
           user: User = Depends(reviewer)):
    o = db.get(EffectOutput, output_id)
    if not o:
        raise HTTPException(404, "unknown effect output")
    if o.status != "pending_review":
        raise HTTPException(409, f"输出状态为 {o.status}，审查已结束")

    action = body.action
    # deep-copy so nested mutations never alias the stored JSON (SQLAlchemy
    # skips the UPDATE when the assigned value still compares equal)
    payload = copy.deepcopy(o.payload)

    if action == "reject":
        o.status = "rejected"
        db.add(EffectReviewDecision(output_id=o.id, reviewer_id=user.id,
                                    action="reject", detail=body.detail,
                                    comment=body.comment))
        db.commit()
        return output_view(o, db, full=True)

    if action == "suppress_center":
        code = body.detail.get("center")
        diags = payload.get("center_diags", {})
        if not code or code not in diags:
            raise HTTPException(400, "suppress_center 需要 detail.center 且该中心仍在纳入集中")
        if code in payload.get("suppressed_centers", []):
            raise HTTPException(400, f"中心 {code} 已被抑制")
        # move the center into the protected display category; its diagnostics
        # leave the payload so a later published report cannot leak them
        diags.pop(code)
        payload["center_diags"] = diags
        payload.setdefault("suppressed_centers", []).append(code)
        payload.setdefault("protected", []).append({
            "center": code, "status": "suppressed_review",
            "reason": STATUS_REASONS["suppressed_review"], "detail": None,
            "error": None,
        })
        payload["coverage"]["included_centers"] = sorted(diags)
        payload["coverage"]["protected_centers"] = sorted(
            p["center"] for p in payload["protected"])
    elif action == "repool":
        payload = repool_payload(payload)
    elif action == "suppress_pooled":
        payload["pooled_suppressed"] = True
    elif action != "approve":
        raise HTTPException(400, f"unknown action {action}")

    o.payload = payload
    o.flags = analyze_effect_payload(payload)
    d = EffectReviewDecision(output_id=o.id, reviewer_id=user.id, action=action,
                             detail=body.detail, comment=body.comment)
    db.add(d)
    db.flush()
    if o.flags:
        if action == "approve":
            db.rollback()
            raise HTTPException(409, "仍存在未解决的差分反推标记，不能批准；"
                                     "请重新汇总或抑制汇总估计")
        db.commit()  # intermediate modification, stays in the queue
        return output_view(o, db, full=True)

    if action != "approve":
        db.commit()
        return output_view(o, db, full=True)

    o.status = "approved"
    pub = _publish(db, o, d, user)
    db.commit()
    out = output_view(o, db, full=True)
    out["published_report_id"] = pub.id
    return out
