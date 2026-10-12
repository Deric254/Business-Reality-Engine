"""
Section 42: external context.

Holidays (and manually entered promotions/events) live in their own table.
They never change business totals; they are attached to alerts as possible
context and they temper day-level anomalies (a quiet public holiday is not an
incident). Holiday calendars come from the `holidays` package for the tenant's
country.
"""
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.config import ExternalContext
from app.models.core import Tenant

KINDS = {"holiday", "promotion", "weather", "event", "other"}


def sync_holidays(db: Session, tenant_id: str, years: Optional[list[int]] = None) -> dict:
    """Idempotently load public holidays for the tenant's country. Unknown/missing country -> nothing, said plainly."""
    import holidays as hol
    t = db.get(Tenant, tenant_id)
    country = (t.country or "").upper() if t else ""
    if not country:
        return {"added": 0, "country": None, "note": "Set the business country in Settings to load public holidays."}
    years = years or [date.today().year - 1, date.today().year, date.today().year + 1]
    try:
        cal = hol.country_holidays(country, years=years)
    except NotImplementedError:
        return {"added": 0, "country": country, "note": f"No holiday calendar available for '{country}'."}
    rows = [{"tenant_id": tenant_id, "kind": "holiday", "name": str(name)[:200], "starts_on": d, "ends_on": d,
             "source": "calendar", "details": {"country": country}} for d, name in cal.items()]
    added = 0
    for i in range(0, len(rows), 500):
        stmt = pg_insert(ExternalContext).on_conflict_do_nothing(constraint="uq_external_context_item").returning(ExternalContext.id)
        added += len(list(db.execute(stmt, rows[i:i + 500])))
    db.commit()
    return {"added": added, "country": country, "note": None}


def context_between(db: Session, tenant_id: str, start: Optional[datetime], end: Optional[datetime]) -> list[dict]:
    if not start or not end:
        return []
    s, e = start.date(), (end - timedelta(microseconds=1)).date() if end > start else end.date()
    rows = (db.query(ExternalContext)
            .filter(ExternalContext.tenant_id == tenant_id, ExternalContext.starts_on <= e, ExternalContext.ends_on >= s)
            .order_by(ExternalContext.starts_on).limit(10).all())
    return [{"kind": r.kind, "name": r.name, "starts_on": r.starts_on.isoformat(), "ends_on": r.ends_on.isoformat()} for r in rows]


def is_holiday(db: Session, tenant_id: str, day: date) -> Optional[str]:
    r = (db.query(ExternalContext.name)
         .filter(ExternalContext.tenant_id == tenant_id, ExternalContext.kind == "holiday",
                 ExternalContext.starts_on <= day, ExternalContext.ends_on >= day).first())
    return r[0] if r else None


def attach_context(db: Session, alert) -> bool:
    """Add overlapping external context to the alert's evidence (a new dict, so the change is tracked)."""
    ctx = context_between(db, alert.tenant_id, alert.period_start, alert.period_end)
    ev = alert.evidence or {}
    if (ev.get("external_context") or []) == ctx:
        return False
    alert.evidence = {**ev, "external_context": ctx}
    return True
