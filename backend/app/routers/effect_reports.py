"""Published effect reports: frozen, hash-stamped, fully referenced."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import PublishedEffectReport, User
from ..security import content_hash

router = APIRouter(prefix="/api/effect-reports", tags=["effect-reports"])


@router.get("")
def list_reports(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.query(PublishedEffectReport).order_by(PublishedEffectReport.id.desc()).all()
    return [{
        "id": r.id, "output_id": r.output_id, "run_id": r.run_id,
        "plan_version_id": r.plan_version_id,
        "pool_version": r.report.get("provenance", {}).get("pool_version"),
        "title": r.report.get("title"),
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "report_hash": r.report_hash,
    } for r in rows]


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db),
               _: User = Depends(current_user)):
    r = db.get(PublishedEffectReport, report_id)
    if not r:
        raise HTTPException(404, "unknown effect report")
    return {"id": r.id, "output_id": r.output_id, "run_id": r.run_id,
            "plan_version_id": r.plan_version_id,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "report_hash": r.report_hash, "report": r.report}


@router.get("/{report_id}/verify")
def verify_report(report_id: int, db: Session = Depends(get_db),
                  _: User = Depends(current_user)):
    r = db.get(PublishedEffectReport, report_id)
    if not r:
        raise HTTPException(404, "unknown effect report")
    recomputed = content_hash(r.report)
    return {"report_id": r.id, "stored_hash": r.report_hash,
            "recomputed_hash": recomputed, "intact": recomputed == r.report_hash}
