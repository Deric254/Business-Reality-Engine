"""Business-level views: health summary, relationships, data quality, discovery, external context, FX."""
import re
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import fx as fx_mod
from app.core.audit import record as audit_record
from app.core.auth_users import authorize_tenant, decode_token, ensure_active
from app.core.business_health import discover, health_summary, quality_report
from app.core.context import KINDS, sync_holidays
from app.core.db import get_db
from app.core.relationships import relationships_for
from app.models.config import ExternalContext, FxRate

router = APIRouter()


def _tenant_user(authorization: Optional[str], tenant_id: str, need_owner: bool = False):
    authorize_tenant(authorization, tenant_id)
    user = ensure_active(decode_token(authorization.removeprefix("Bearer "), expected_type="access"))
    if need_owner and user.role != "owner":
        raise HTTPException(403, "This action requires the 'owner' role")
    return user


@router.get("/health")
def health(tenant_id: str, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
           authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return health_summary(db, tenant_id, days)


@router.get("/quality")
def quality(tenant_id: str, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db),
            authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return quality_report(db, tenant_id, days)


@router.get("/discovery")
def discovery(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return discover(db, tenant_id)


@router.get("/relationships/{entity_id}")
def relationships(entity_id: str, tenant_id: str, days: int = Query(90, ge=1, le=730), db: Session = Depends(get_db),
                  authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    out = relationships_for(db, tenant_id, entity_id, days)
    if out is None:
        raise HTTPException(404, "Entity not found")
    return out


# ---- external context (Section 42) ----

class ContextIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=200)
    starts_on: date
    ends_on: Optional[date] = None


@router.get("/context")
def list_context(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(ExternalContext).filter(ExternalContext.tenant_id == tenant_id).order_by(ExternalContext.starts_on.desc()).limit(500).all()
    return [{"id": r.id, "kind": r.kind, "name": r.name, "starts_on": r.starts_on, "ends_on": r.ends_on, "source": r.source} for r in rows]


@router.post("/context")
def add_context(tenant_id: str, body: ContextIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _tenant_user(authorization, tenant_id, need_owner=True)
    if body.kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
    ends = body.ends_on or body.starts_on
    if ends < body.starts_on or (ends - body.starts_on).days > 366:
        raise HTTPException(400, "ends_on must be on/after starts_on and within a year")
    if db.query(ExternalContext.id).filter(ExternalContext.tenant_id == tenant_id, ExternalContext.kind == body.kind,
                                           ExternalContext.name == body.name.strip(), ExternalContext.starts_on == body.starts_on).first():
        raise HTTPException(409, "That item already exists")
    r = ExternalContext(tenant_id=tenant_id, kind=body.kind, name=body.name.strip(), starts_on=body.starts_on, ends_on=ends, source="manual")
    db.add(r); db.commit()
    return {"id": r.id}


@router.delete("/context/{item_id}", status_code=204)
def delete_context(item_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _tenant_user(authorization, tenant_id, need_owner=True)
    r = db.query(ExternalContext).filter(ExternalContext.id == item_id, ExternalContext.tenant_id == tenant_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    db.delete(r); db.commit()


@router.post("/context/sync-holidays")
def holidays_sync(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _tenant_user(authorization, tenant_id, need_owner=True)
    return sync_holidays(db, tenant_id)


# ---- FX (Section 57) ----

class FxIn(BaseModel):
    currency: str
    rate_to_base: float = Field(gt=0, lt=1e9)
    effective_date: date


@router.get("/fx")
def list_fx(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(FxRate).filter(FxRate.tenant_id == tenant_id).order_by(FxRate.currency, FxRate.effective_date.desc()).limit(500).all()
    return [{"id": r.id, "currency": r.currency, "rate_to_base": float(r.rate_to_base), "effective_date": r.effective_date} for r in rows]


@router.put("/fx")
def put_fx(tenant_id: str, body: FxIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Add or replace the rate for a currency on a date, then convert events that were waiting for a rate."""
    user = _tenant_user(authorization, tenant_id, need_owner=True)
    cur = fx_mod.normalize_currency(body.currency)
    if not cur or not re.fullmatch(r"[A-Z]{3}", cur):
        raise HTTPException(400, "currency must be a 3-letter code such as USD")
    from app.models.core import Tenant
    if cur == db.get(Tenant, tenant_id).base_currency:
        raise HTTPException(400, "That is the base currency; no rate is needed")
    row = db.query(FxRate).filter(FxRate.tenant_id == tenant_id, FxRate.currency == cur, FxRate.effective_date == body.effective_date).first()
    if row:
        row.rate_to_base = body.rate_to_base
    else:
        db.add(FxRate(tenant_id=tenant_id, currency=cur, rate_to_base=body.rate_to_base, effective_date=body.effective_date))
    db.commit()
    audit_record(db, "fx.rate_set", tenant_id=tenant_id, actor_user_id=user.user_id, target=cur,
                 detail={"rate": body.rate_to_base, "date": body.effective_date.isoformat()})
    return fx_mod.reprocess_unconverted(db, tenant_id)


@router.post("/fx/reprocess")
def reprocess(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    _tenant_user(authorization, tenant_id, need_owner=True)
    return fx_mod.reprocess_unconverted(db, tenant_id)


@router.get("/trend")
def trend(tenant_id: str, days: int = Query(30, ge=7, le=180), db: Session = Depends(get_db),
          authorization: Optional[str] = Header(default=None)):
    """Daily sales and expenses for the last `days` days (zero-filled), plus the same length of time before it, for the
    dashboard chart. Only validated events count, exactly as in every other total."""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.models.core import BusinessEvent as E
    authorize_tenant(authorization, tenant_id)
    today = datetime.utcnow().date()
    start = today - timedelta(days=2 * days - 1)
    rows = (db.query(func.date(E.timestamp), E.entity, func.coalesce(func.sum(E.amount), 0))
            .filter(E.tenant_id == tenant_id, E.entity.in_(("sale", "expense")), E.validation_status == "validated",
                    E.timestamp >= datetime.combine(start, datetime.min.time()))
            .group_by(func.date(E.timestamp), E.entity).all())
    cell: dict = {}
    for d, ent, amt in rows:
        cell[(d, ent)] = float(amt)
    out = []
    for i in range(2 * days):
        d = start + timedelta(days=i)
        out.append({"date": d.isoformat(), "sales": round(cell.get((d, "sale"), 0.0), 2), "expenses": round(cell.get((d, "expense"), 0.0), 2)})
    return {"days": days, "previous": out[:days], "current": out[days:]}
