"""Business-level views: health summary, relationships, data quality, discovery, external context, FX."""
import re
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import data_check as data_check_mod, fx as fx_mod
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


@router.get("/data-check")
def data_check(tenant_id: str, period_days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
               authorization: Optional[str] = Header(default=None)):
    """Why the dashboard shows what it shows: per source, what was read, counted, left out and why."""
    authorize_tenant(authorization, tenant_id)
    return data_check_mod.run(db, tenant_id, period_days)


@router.get("/health")
def health(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
           authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return health_summary(db, tenant_id, days)


@router.get("/quality")
def quality(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
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
def trend(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
          authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return build_trend(db, tenant_id, days)


@router.get("/analytics")
def analytics(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
              authorization: Optional[str] = Header(default=None)):
    """Everything the Analytics page draws, in one answer: headline figures, monthly series, branches, products, spending,
    weekdays, customers, the daily record, a forecast with its fit quality, and sentences generated from those figures.
    Cached briefly; ingestion drops the cache so new records show at once. The key carries the business's date."""
    authorize_tenant(authorization, tenant_id)
    from fastapi.encoders import jsonable_encoder
    from app.core import periods
    from app.core.analytics import build_analytics
    from app.core.cache import cached
    day = periods.local_today(db, tenant_id).isoformat()
    return cached(f"analytics:{tenant_id}:{days}:{day}", 30, lambda: jsonable_encoder(build_analytics(db, tenant_id, days)))


@router.get("/analytics.pdf")
def analytics_pdf(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db),
                  authorization: Optional[str] = Header(default=None)):
    """The Analytics page as a finished PDF report, built from the records alone (no AI service involved)."""
    from app.core import periods
    from app.core.analytics_pdf import build_analytics_pdf
    from app.core.detection import ACTIVE_STATES, currency_of
    from app.models.alerts import Alert
    from app.models.core import Tenant
    a = analytics(tenant_id, days, db, authorization)        # same authorisation and cache as the page itself
    top = (db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE_STATES))
           .order_by(Alert.priority_rank.desc().nullslast(), Alert.created_at.desc()).limit(3).all())
    pdf = build_analytics_pdf(a, [{"severity": al.severity.value, "title": al.title, "what_happened": al.what_happened} for al in top],
                              db.get(Tenant, tenant_id).name, currency_of(db, tenant_id), periods.local_today(db, tenant_id))
    name = f"analytics-{a['period']['first_day']}-to-{a['period']['last_day']}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


def build_trend(db: Session, tenant_id: str, days: int, now=None) -> dict:
    """Sales and expenses per day for the period, plus the same length of time before it, for the dashboard chart.
    Days are the business's own calendar days (never UTC days), the boundaries are the same ones the totals use
    (app/core/periods.py), and only validated events count. `days=0` is all time (no earlier period to draw);
    a span longer than 400 days is drawn by week, longer than 3 years by month, and `bucket` says which."""
    from datetime import timedelta
    from sqlalchemy import func
    from app.core import periods
    from app.models.core import BusinessEvent as E
    per = periods.resolve(db, tenant_id, days, now)
    span = per.days
    bucket = "day" if span <= 400 else "week" if span <= 1100 else "month"
    local = func.timezone(per.tz.key, func.timezone("UTC", E.timestamp))
    lo = per.start if per.all_time else per.prev_start
    cell: dict = {}
    for d, ent, amt in (db.query(func.date_trunc(bucket, local), E.entity, func.coalesce(func.sum(E.amount), 0))
                        .filter(E.tenant_id == tenant_id, E.entity.in_(("sale", "expense")), E.validation_status == "validated",
                                E.timestamp >= lo, E.timestamp < per.end)
                        .group_by(func.date_trunc(bucket, local), E.entity).all()):
        cell[(d.date(), ent)] = float(amt)

    def row(d):
        return {"date": d.isoformat(), "sales": round(cell.get((d, "sale"), 0.0), 2), "expenses": round(cell.get((d, "expense"), 0.0), 2)}

    def steps(first, last):
        d, out = first, []
        if bucket == "week":
            d = first - timedelta(days=first.weekday())
        elif bucket == "month":
            d = first.replace(day=1)
        while d <= last:
            out.append(row(d))
            d = d + timedelta(days=1) if bucket == "day" else d + timedelta(days=7) if bucket == "week" else (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return out
    current = steps(per.first_day, per.last_day)
    previous = [] if per.all_time else steps(per.first_day - timedelta(days=days), per.first_day - timedelta(days=1))
    return {"days": days, "bucket": bucket, "period": per.as_dict(), "previous": previous, "current": current}
