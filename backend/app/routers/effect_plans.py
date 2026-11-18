"""Effect research-protocol endpoints: versioned specs + methodology approval.

The plan fixes, BEFORE any run: the common cohort rule, the treatment
comparison, the outcome, adjustment covariates, and whether pooling is led by
a fixed-effect or a random-effects summary. Neither method is a default
"correct answer" — the choice and its premises are reviewed here and later
displayed on every run/output/report.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role
from ..effectorch import validate_spec
from ..models import EffectPlan, EffectPlanVersion, User
from ..security import content_hash

router = APIRouter(prefix="/api/effect-plans", tags=["effect-plans"])
researcher = require_role("researcher")
reviewer = require_role("reviewer")


def version_view(v: EffectPlanVersion, db: Session) -> dict:
    creator = db.get(User, v.created_by)
    return {
        "id": v.id, "plan_id": v.plan_id, "version_no": v.version_no,
        "status": v.status, "spec": v.spec, "spec_hash": v.spec_hash,
        "created_by": creator.display_name if creator else v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "submitted_at": v.submitted_at.isoformat() if v.submitted_at else None,
        "review_comment": v.review_comment,
    }


def plan_view(plan: EffectPlan, db: Session) -> dict:
    versions = (db.query(EffectPlanVersion)
                .filter(EffectPlanVersion.plan_id == plan.id)
                .order_by(EffectPlanVersion.version_no.desc()).all())
    owner = db.get(User, plan.owner_id)
    return {
        "id": plan.id, "name": plan.name,
        "owner": owner.display_name if owner else None,
        "current_version_id": plan.current_version_id,
        "versions": [version_view(v, db) for v in versions],
    }


@router.get("")
def list_plans(db: Session = Depends(get_db), _: User = Depends(current_user)):
    out = []
    for p in db.query(EffectPlan).order_by(EffectPlan.id).all():
        current = (db.get(EffectPlanVersion, p.current_version_id)
                   if p.current_version_id else None)
        approved = next((v for v in db.query(EffectPlanVersion)
                         .filter(EffectPlanVersion.plan_id == p.id,
                                 EffectPlanVersion.status == "approved")
                         .order_by(EffectPlanVersion.version_no.desc())), None)
        out.append({"id": p.id, "name": p.name,
                    "current_version_no": current.version_no if current else None,
                    "current_status": current.status if current else None,
                    "current_version_id": p.current_version_id,
                    "approved_version_id": approved.id if approved else None,
                    "approved_version_no": approved.version_no if approved else None})
    return out


@router.get("/review/queue")
def review_queue(db: Session = Depends(get_db), _: User = Depends(reviewer)):
    rows = (db.query(EffectPlanVersion)
            .filter(EffectPlanVersion.status == "in_review")
            .order_by(EffectPlanVersion.submitted_at).all())
    return [version_view(v, db) for v in rows]


class CreatePlanIn(BaseModel):
    name: str
    spec: dict


@router.post("", status_code=201)
def create_plan(body: CreatePlanIn, db: Session = Depends(get_db),
                user: User = Depends(researcher)):
    validate_spec(body.spec)
    plan = EffectPlan(name=body.name, owner_id=user.id)
    db.add(plan)
    db.flush()
    v = EffectPlanVersion(plan_id=plan.id, version_no=1, status="draft",
                          spec=body.spec, spec_hash=content_hash(body.spec),
                          created_by=user.id)
    db.add(v)
    db.flush()
    plan.current_version_id = v.id
    db.commit()
    return plan_view(plan, db)


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db),
             _: User = Depends(current_user)):
    plan = db.get(EffectPlan, plan_id)
    if not plan:
        raise HTTPException(404, "unknown effect plan")
    return plan_view(plan, db)


class SaveSpecIn(BaseModel):
    base_version_id: int
    spec: dict


@router.put("/{plan_id}/save")
def save_spec(plan_id: int, body: SaveSpecIn, db: Session = Depends(get_db),
              user: User = Depends(researcher)):
    plan = db.get(EffectPlan, plan_id)
    if not plan:
        raise HTTPException(404, "unknown effect plan")
    base = db.get(EffectPlanVersion, body.base_version_id)
    if not base or base.plan_id != plan_id:
        raise HTTPException(400, "base_version_id 不属于该方案")
    current = db.get(EffectPlanVersion, plan.current_version_id)
    if base.id != current.id:
        raise HTTPException(409, "方案在你编辑期间已被更新，请刷新后基于最新版本保存")
    validate_spec(body.spec)
    if content_hash(body.spec) == base.spec_hash:
        return {"unchanged": True, "plan": plan_view(plan, db)}
    next_no = (db.query(EffectPlanVersion)
               .filter(EffectPlanVersion.plan_id == plan_id).count() + 1)
    v = EffectPlanVersion(plan_id=plan_id, version_no=next_no, status="draft",
                          spec=body.spec, spec_hash=content_hash(body.spec),
                          created_by=user.id)
    db.add(v)
    db.flush()
    plan.current_version_id = v.id
    db.commit()
    return {"unchanged": False, "plan": plan_view(plan, db)}


@router.post("/{plan_id}/versions/{version_id}/submit")
def submit_version(plan_id: int, version_id: int, db: Session = Depends(get_db),
                   user: User = Depends(researcher)):
    v = db.get(EffectPlanVersion, version_id)
    if not v or v.plan_id != plan_id:
        raise HTTPException(404, "unknown plan version")
    if v.status != "draft":
        raise HTTPException(409, f"版本状态为 {v.status}，仅草稿可提交方法学审查")
    v.status = "in_review"
    v.submitted_at = datetime.now(timezone.utc)
    db.commit()
    return version_view(v, db)


class ReviewIn(BaseModel):
    comment: str | None = None


@router.post("/versions/{version_id}/approve")
def approve_version(version_id: int, body: ReviewIn,
                    db: Session = Depends(get_db), user: User = Depends(reviewer)):
    v = db.get(EffectPlanVersion, version_id)
    if not v:
        raise HTTPException(404, "unknown plan version")
    if v.status != "in_review":
        raise HTTPException(409, f"版本状态为 {v.status}，仅在审版本可批准")
    v.status = "approved"
    v.reviewed_by = user.id
    v.reviewed_at = datetime.now(timezone.utc)
    v.review_comment = body.comment
    db.commit()
    return version_view(v, db)


@router.post("/versions/{version_id}/reject")
def reject_version(version_id: int, body: ReviewIn,
                   db: Session = Depends(get_db), user: User = Depends(reviewer)):
    v = db.get(EffectPlanVersion, version_id)
    if not v:
        raise HTTPException(404, "unknown plan version")
    if v.status != "in_review":
        raise HTTPException(409, f"版本状态为 {v.status}，仅在审版本可拒绝")
    v.status = "rejected"
    v.reviewed_by = user.id
    v.reviewed_at = datetime.now(timezone.utc)
    v.review_comment = body.comment
    db.commit()
    return version_view(v, db)
