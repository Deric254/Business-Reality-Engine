"""
Section 57: multi-currency.

Reports are always in the tenant's base currency. An event in another
currency converts at ingestion with the latest rate effective on or before the
event's date; the original amount/currency/rate are kept in metadata so the
conversion is auditable. With no rate the event is flagged `failed` (so it is
excluded from every total rather than silently summed at the wrong value) and
`reprocess_unconverted` converts it once a rate is added.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session

from app.models.config import FxRate
from app.models.core import BusinessEvent, Tenant

NO_RATE_PREFIX = "no FX rate for "


def normalize_currency(code: Optional[str]) -> Optional[str]:
    code = (code or "").strip().upper()
    return code or None


def rate_for(db: Session, tenant_id: str, currency: str, on: date) -> Optional[FxRate]:
    return (
        db.query(FxRate)
        .filter(FxRate.tenant_id == tenant_id, FxRate.currency == currency, FxRate.effective_date <= on)
        .order_by(FxRate.effective_date.desc())
        .first()
    )


def convert_event_fields(db: Session, base: str, tenant_id: str, amount, currency: Optional[str],
                         ts: datetime, cache: dict) -> tuple[Optional[Decimal], Optional[str], dict, Optional[str]]:
    """-> (amount, currency, metadata_additions, error). Same-currency / no-currency events pass through."""
    cur = normalize_currency(currency)
    if cur is None or cur == base or amount is None:
        return amount, (cur or currency), {}, None
    key = (tenant_id, cur, ts.date())
    if key not in cache:
        cache[key] = rate_for(db, tenant_id, cur, ts.date())
    fx = cache[key]
    if fx is None:
        return amount, cur, {}, f"{NO_RATE_PREFIX}{cur} on {ts.date().isoformat()}"
    rate = Decimal(fx.rate_to_base)
    converted = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"))
    return converted, base, {"fx": {"original_amount": str(amount), "original_currency": cur, "rate": str(rate),
                                    "rate_date": fx.effective_date.isoformat()}}, None


def reprocess_unconverted(db: Session, tenant_id: str, limit: int = 5000) -> dict:
    """Convert events that failed only for a missing rate. Events with other validation errors stay failed."""
    base = db.get(Tenant, tenant_id).base_currency
    cache: dict = {}
    fixed = still = 0
    rows = db.query(BusinessEvent).filter(
        BusinessEvent.tenant_id == tenant_id, BusinessEvent.validation_status == "failed").limit(limit).all()
    for ev in rows:
        errs = list(ev.validation_errors or [])
        if not any(e.startswith(NO_RATE_PREFIX) for e in errs):
            continue
        amount, cur, meta, err = convert_event_fields(db, base, tenant_id, ev.amount, ev.currency, ev.timestamp, cache)
        if err:
            still += 1
            continue
        rest = [e for e in errs if not e.startswith(NO_RATE_PREFIX)]
        ev.amount, ev.currency = amount, cur
        ev.metadata_json = {**(ev.metadata_json or {}), **meta}
        ev.validation_errors = rest or None
        ev.validation_status = "failed" if rest else "validated"
        fixed += 1
    db.commit()
    return {"converted": fixed, "still_missing_rate": still}
