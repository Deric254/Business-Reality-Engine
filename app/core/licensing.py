"""Licences: issue, activate (once), revoke, and the check every request makes.

A key is generated once, shown once, and stored only as a hash. Activation is atomic: the licence row is locked, so a
key can create exactly one business even if two people submit it at the same instant. Revoking takes effect on the
next request (the per-business status cache is dropped at once).
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import cache
from app.models.licence import Licence, LicencePayment

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"            # no 0/O/1/I/L: keys get read aloud and typed from messages
STATUS_TTL_SECONDS = 20
DEFAULT_GRACE_DAYS = 7


def enforced() -> bool:
    return os.environ.get("LICENCE_ENFORCED", "false").strip().lower() in ("1", "true", "yes", "on")


def grace_days() -> int:
    """Days a paying business may run past the end of its paid period (or past activation, if it has never paid)
    before access stops. LICENCE_GRACE_DAYS overrides the default of 7; 0 means no grace."""
    try:
        return max(0, min(365, int(os.environ.get("LICENCE_GRACE_DAYS", DEFAULT_GRACE_DAYS))))
    except ValueError:
        return DEFAULT_GRACE_DAYS


def new_key() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(20))          # 100 bits
    return "BRE-" + "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def normalize(key: str) -> str:
    return "".join(ch for ch in (key or "").upper() if ch.isalnum() or ch == "-").strip("-")


def hash_key(key: str) -> str:
    return hashlib.sha256(normalize(key).encode()).hexdigest()


def issue(db: Session, count: int, label: Optional[str], price_cents: int, currency: str, created_by: Optional[str]) -> list[dict]:
    out = []
    for _ in range(count):
        key = new_key()
        lic = Licence(key_hash=hash_key(key), key_hint=f"{key[:8]}…{key[-4:]}", label=label, status="unused",
                      price_cents=price_cents, currency=currency, created_by_user_id=created_by)
        db.add(lic); db.flush()
        out.append({"id": lic.id, "key": key, "hint": lic.key_hint})
    db.commit()
    return out


def lock_unused(db: Session, key: str) -> Licence:
    """The licence for this key, locked for the rest of the transaction, or a refusal that reveals nothing about why."""
    lic = db.query(Licence).filter(Licence.key_hash == hash_key(key)).with_for_update().first()
    if not lic or lic.status != "unused":
        raise HTTPException(400, "This licence key is not valid or has already been used")
    return lic


def revoke(db: Session, lic: Licence, reason: Optional[str]) -> None:
    if lic.status == "revoked":
        return
    lic.status, lic.revoked_at, lic.revoked_reason = "revoked", datetime.utcnow(), (reason or "").strip()[:300] or None
    db.commit()
    if lic.tenant_id:
        cache.invalidate_prefix(f"licence:{lic.tenant_id}")


def reinstate(db: Session, lic: Licence) -> str:
    """Undo a revoke. A key that was already used goes back to active; a key revoked before anyone used it goes back
    to unused. Returns the new status. Access resumes on the next request (the status cache is dropped at once)."""
    if lic.status != "revoked":
        raise HTTPException(400, "Only a revoked licence can be reinstated")
    lic.status = "active" if lic.tenant_id else "unused"
    lic.revoked_at = lic.revoked_reason = None
    db.commit()
    if lic.tenant_id:
        cache.invalidate_prefix(f"licence:{lic.tenant_id}")
    return lic.status


def record_payment(db: Session, lic: Licence, amount_cents: int, months: int, reference: Optional[str], method: str = "manual") -> LicencePayment:
    now = datetime.utcnow()
    start = lic.paid_through if lic.paid_through and lic.paid_through > now else now          # paying early extends, never overlaps
    pay = LicencePayment(licence_id=lic.id, amount_cents=amount_cents, currency=lic.currency, paid_at=now,
                         period_start=start, period_end=start + relativedelta(months=months), reference=reference or None, method=method)
    db.add(pay)
    lic.paid_through = pay.period_end
    db.commit()
    return pay


def access_state(status: str, price_cents: Optional[int], paid_through: Optional[datetime], redeemed_at: Optional[datetime],
                 now: Optional[datetime] = None) -> str:
    """What a licence lets its business do: the stored status, except that an active PAID licence becomes 'overdue' once the
    end of its last paid period (or, if it never paid, the day it was activated) is more than the grace period in the past.
    A free licence (price 0) is never overdue."""
    if status == "active" and (price_cents or 0) > 0:
        due = paid_through or redeemed_at
        if due and (now or datetime.utcnow()) > due + timedelta(days=grace_days()):
            return "overdue"
    return status


def tenant_status(tenant_id: str) -> str:
    """'active' | 'overdue' | 'revoked' | 'none' for a business, cached for a few seconds so this is not a query on every request."""
    def compute():
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            row = (db.query(Licence.status, Licence.price_cents, Licence.paid_through, Licence.redeemed_at)
                   .filter(Licence.tenant_id == tenant_id).first())
            return access_state(*row) if row else "none"
        finally:
            db.close()
    return cache.cached(f"licence:{tenant_id}", STATUS_TTL_SECONDS, compute)


def require_licence(tenant_id: str) -> None:
    """Called for every signed-in request. No-op until LICENCE_ENFORCED is switched on."""
    if not enforced():
        return
    status = tenant_status(tenant_id)
    if status == "revoked":
        raise HTTPException(403, "This licence has been revoked. Contact the service provider.")
    if status == "overdue":
        raise HTTPException(403, "This subscription is overdue, so access is paused. Your data is kept. "
                                 "Contact the service provider to renew and access resumes at once.")
    if status != "active":
        raise HTTPException(403, "This business has no active licence.")
