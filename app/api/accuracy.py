"""Section 53 (model versions) and Section 55 (backtesting, ground truth, live accuracy)."""
from datetime import date, datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import accuracy as acc
from app.core.audit import record as audit_record
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.core.locks import try_advisory_lock
from app.core.model_versions import MANUAL_KINDS, current_version, record_change
from app.models.config import BacktestRun, GroundTruthEvent, ModelChange

router = APIRouter()


def _owner(authorization, tenant_id):
    return authorize_tenant(authorization, tenant_id, require_role="owner")


# ---- model versions ----

class ChangeIn(BaseModel):
    kind: str
    subject: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    effective_at: datetime
    rebaseline: bool = False


@router.get("/model")
def model_overview(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(ModelChange).filter(ModelChange.tenant_id == tenant_id).order_by(ModelChange.version.desc()).limit(200).all()
    return {"version": current_version(db, tenant_id),
            "changes": [{"id": r.id, "version": r.version, "kind": r.kind, "subject": r.subject, "description": r.description,
                         "effective_at": r.effective_at, "rebaseline": r.rebaseline, "source": r.source} for r in rows]}


@router.post("/model/changes")
def declare_change(tenant_id: str, body: ChangeIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = _owner(authorization, tenant_id)
    if body.kind not in MANUAL_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(MANUAL_KINDS)}")
    if body.effective_at > datetime.utcnow().replace(microsecond=0) + timedelta(days=1):
        raise HTTPException(400, "effective_at cannot be in the future")
    row = record_change(db, tenant_id, body.kind, body.subject.strip(), body.effective_at, description=body.description,
                        rebaseline=body.rebaseline, source="manual")
    if row is None:
        raise HTTPException(409, "That change is already recorded")
    audit_record(db, "model.change_declared", tenant_id=tenant_id, actor_user_id=user.user_id, target=row.id,
                 detail={"kind": body.kind, "rebaseline": body.rebaseline})
    return {"id": row.id, "version": row.version}


@router.post("/model/changes/{change_id}/clear-rebaseline", status_code=204)
def clear_rebaseline(change_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = _owner(authorization, tenant_id)
    row = db.query(ModelChange).filter(ModelChange.id == change_id, ModelChange.tenant_id == tenant_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    row.rebaseline = False; db.commit()
    audit_record(db, "model.rebaseline_cleared", tenant_id=tenant_id, actor_user_id=user.user_id, target=change_id)


# ---- ground truth ----

class TruthIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    metric: Optional[str] = Field(default=None, max_length=50)
    starts_on: date
    ends_on: Optional[date] = None


@router.get("/ground-truth")
def list_truth(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(GroundTruthEvent).filter(GroundTruthEvent.tenant_id == tenant_id).order_by(GroundTruthEvent.starts_on.desc()).limit(500).all()
    return [{"id": r.id, "label": r.label, "metric": r.metric, "starts_on": r.starts_on, "ends_on": r.ends_on} for r in rows]


@router.post("/ground-truth")
def add_truth(tenant_id: str, body: TruthIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _owner(authorization, tenant_id)
    ends = body.ends_on or body.starts_on
    if ends < body.starts_on or (ends - body.starts_on).days > 90:
        raise HTTPException(400, "ends_on must be on/after starts_on and within 90 days")
    r = GroundTruthEvent(tenant_id=tenant_id, label=body.label.strip(), metric=(body.metric or None), starts_on=body.starts_on, ends_on=ends)
    db.add(r); db.commit()
    return {"id": r.id}


@router.delete("/ground-truth/{item_id}", status_code=204)
def delete_truth(item_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _owner(authorization, tenant_id)
    r = db.query(GroundTruthEvent).filter(GroundTruthEvent.id == item_id, GroundTruthEvent.tenant_id == tenant_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    db.delete(r); db.commit()


# ---- backtest / report ----

class BacktestIn(BaseModel):
    start: date
    end: date
    step_days: int = Field(default=1, ge=1, le=30)
    tolerance_days: int = Field(default=3, ge=0, le=30)
    threshold_multipliers: Optional[list[float]] = None


@router.post("/backtest")
def backtest(tenant_id: str, body: BacktestIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = _owner(authorization, tenant_id)
    with try_advisory_lock(f"backtest:{tenant_id}") as got:
        if not got:
            raise HTTPException(409, "A backtest is already running for this business")
        try:
            result = acc.run_backtest(tenant_id, body.start, body.end, step_days=body.step_days, tolerance_days=body.tolerance_days,
                                      threshold_multipliers=body.threshold_multipliers)
        except ValueError as e:
            raise HTTPException(400, str(e))
        run = BacktestRun(tenant_id=tenant_id, params=body.model_dump(mode="json"), result=result)
        db.add(run); db.commit()
        audit_record(db, "accuracy.backtest", tenant_id=tenant_id, actor_user_id=user.user_id, target=run.id)
        return {"id": run.id, **result}


@router.get("/backtest")
def list_backtests(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(BacktestRun).filter(BacktestRun.tenant_id == tenant_id).order_by(BacktestRun.created_at.desc()).limit(20).all()
    return [{"id": r.id, "created_at": r.created_at, "params": r.params, "result": r.result} for r in rows]


@router.get("/report")
def report(tenant_id: str, days: int = Query(180, ge=7, le=730), db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return acc.live_accuracy(db, tenant_id, days)
