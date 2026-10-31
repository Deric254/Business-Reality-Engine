"""The platform owner's side of licensing: issue keys, see every account and the money, revoke. Plus the one public
door businesses come through: activating a key.

Money shown here is only what has been recorded in the payments ledger. There is no estimate and no guess; a licence
with no recorded payment shows as unpaid. (Collecting the $1 automatically needs a payment provider account; until
one is connected, each payment is recorded here, and a provider's webhook can call the same function.)"""
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import TokenPair, _check_new_password, _throttle, _tokens
from app.core import audit, demo, licensing
from app.core.auth import require_admin_key
from app.core.auth_users import decode_token, hash_password
from app.core.db import get_db
from app.models.core import BusinessEvent, Connector, Tenant, User
from app.models.licence import Licence, LicencePayment
from app.core.clock import utcnow

router = APIRouter()               # /api/master
public = APIRouter()               # /api/licences


def master_user(authorization: Optional[str], db: Session) -> User:
    """The caller must hold a valid session AND be flagged master in the database right now. Anyone else gets 404, so
    the existence of these endpoints is not advertised."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(404, "Not found")
    cu = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
    user = db.get(User, cu.user_id)
    if not user or not user.is_master or cu.must_change:
        raise HTTPException(404, "Not found")
    return user


# ---------------- master ----------------

class BootstrapIn(BaseModel):
    email: EmailStr


@router.post("/bootstrap")
def bootstrap(body: BootstrapIn, request: Request, db: Session = Depends(get_db), x_admin_key: Optional[str] = Header(default=None)):
    """Make an existing user the master account (deployment admin key required). Their own business gets a free
    permanent licence so it is never locked out by its own rules."""
    _throttle(request)
    require_admin_key(x_admin_key)
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(404, "No user with that email")
    user.is_master = True
    if not db.query(Licence.id).filter(Licence.tenant_id == user.tenant_id).first():
        lic = licensing.issue(db, 1, "Platform owner", 0, "USD", user.id)[0]
        row = db.get(Licence, lic["id"])
        row.status, row.redeemed_at, row.tenant_id = "active", utcnow(), user.tenant_id
    db.commit()
    audit.record(db, "master.bootstrap", request=request, tenant_id=user.tenant_id, actor_type="admin", target=user.id)
    return {"master": True, "email": user.email}


@router.get("/whoami")
def whoami(db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    master_user(authorization, db)
    return {"master": True}


def _money(cents) -> float:
    return round((cents or 0) / 100, 2)


@router.get("/overview")
def overview(db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    master_user(authorization, db)
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counts = dict(db.query(Licence.status, func.count(Licence.id)).filter(Licence.price_cents > 0).group_by(Licence.status).all())
    paying = db.query(Licence).filter(Licence.status == "active", Licence.price_cents > 0, Licence.paid_through > now).all()
    overdue = db.query(func.count(Licence.id)).filter(Licence.status == "active", Licence.price_cents > 0,
                                                      (Licence.paid_through.is_(None)) | (Licence.paid_through <= now)).scalar()
    paused = sum(1 for l in db.query(Licence).filter(Licence.status == "active", Licence.price_cents > 0).all()
                 if licensing.access_state(l.status, l.price_cents, l.paid_through, l.redeemed_at, now) == "overdue")
    collected = db.query(func.coalesce(func.sum(LicencePayment.amount_cents), 0)).scalar()
    this_month = db.query(func.coalesce(func.sum(LicencePayment.amount_cents), 0)).filter(LicencePayment.paid_at >= month_start).scalar()
    return {
        "currency": "USD",
        "licences": {"issued": sum(counts.values()), "unused": counts.get("unused", 0), "active": counts.get("active", 0), "revoked": counts.get("revoked", 0)},
        "paid_up": len(paying), "overdue": overdue, "paused": paused,
        "monthly_recurring": _money(sum(l.price_cents / max(1, l.interval_months) for l in paying)),
        "collected_total": _money(collected), "collected_this_month": _money(this_month),
        "as_of": now.isoformat(timespec="seconds") + "Z",
    }


@router.get("/accounts")
def accounts(db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    master_user(authorization, db)
    now = utcnow()
    paid = dict(db.query(LicencePayment.licence_id, func.sum(LicencePayment.amount_cents)).group_by(LicencePayment.licence_id).all())
    owners = {}
    for t, e in db.query(User.tenant_id, User.email).filter(User.role == "owner").order_by(User.created_at).all():
        owners.setdefault(t, e)
    names = dict(db.query(Tenant.id, Tenant.name).all())
    last = dict(db.query(BusinessEvent.tenant_id, func.max(BusinessEvent.timestamp)).group_by(BusinessEvent.tenant_id).all())
    sources = dict(db.query(Connector.tenant_id, func.count(Connector.id)).group_by(Connector.tenant_id).all())
    rows = []
    for l in db.query(Licence).order_by(Licence.created_at.desc()).limit(5000).all():
        rows.append({
            "id": l.id, "hint": l.key_hint, "label": l.label, "status": l.status, "price": _money(l.price_cents), "currency": l.currency,
            "business": names.get(l.tenant_id), "owner_email": owners.get(l.tenant_id), "sources": sources.get(l.tenant_id, 0),
            "activated_at": l.redeemed_at, "last_record_at": last.get(l.tenant_id), "paid_through": l.paid_through,
            "total_paid": _money(paid.get(l.id)), "revoked_at": l.revoked_at, "revoked_reason": l.revoked_reason,
            "access": licensing.access_state(l.status, l.price_cents, l.paid_through, l.redeemed_at, now),
            "payment_state": ("free" if l.price_cents == 0 else "paid" if l.paid_through and l.paid_through > now else "unpaid") if l.status == "active" else None,
        })
    return rows


class IssueIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)
    label: Optional[str] = Field(default=None, max_length=120)
    price: float = Field(default=1.0, ge=0, le=100000)
    ref: Optional[str] = Field(default=None, max_length=64)      # which lead in another of the owner's systems this is for (see advisor_feed)


@router.post("/licences")
def issue_licences(body: IssueIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = master_user(authorization, db)
    keys = licensing.issue(db, body.count, (body.label or "").strip() or None, int(round(body.price * 100)), "USD", user.id,
                          source_ref=(body.ref or "").strip() or None)
    audit.record(db, "master.licences_issued", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, detail={"count": body.count, "label": body.label})
    return {"keys": keys, "note": "These keys are shown once and cannot be recovered. Copy them now."}


class RevokeIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


@router.post("/licences/{licence_id}/revoke")
def revoke_licence(licence_id: str, body: RevokeIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = master_user(authorization, db)
    lic = db.get(Licence, licence_id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    if lic.tenant_id and lic.tenant_id == user.tenant_id:
        raise HTTPException(400, "You cannot revoke the licence of your own master account")
    licensing.revoke(db, lic, body.reason)
    audit.record(db, "master.licence_revoked", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=licence_id, detail={"reason": body.reason})
    return {"id": lic.id, "status": lic.status}


@router.post("/licences/{licence_id}/reinstate")
def reinstate_licence(licence_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Undo a revoke. Access returns on the business's next request."""
    user = master_user(authorization, db)
    lic = db.get(Licence, licence_id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    status = licensing.reinstate(db, lic)
    audit.record(db, "master.licence_reinstated", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=licence_id)
    return {"id": lic.id, "status": status}


class PaymentIn(BaseModel):
    amount: float = Field(gt=0, le=1000000)
    months: int = Field(default=1, ge=1, le=36)
    reference: Optional[str] = Field(default=None, max_length=120)


@router.post("/licences/{licence_id}/payments")
def add_payment(licence_id: str, body: PaymentIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    user = master_user(authorization, db)
    lic = db.get(Licence, licence_id)
    if not lic or lic.status != "active":
        raise HTTPException(400, "Payments can only be recorded for an active licence")
    ref = (body.reference or "").strip() or None
    if ref and db.query(LicencePayment.id).filter(LicencePayment.reference == ref).first():
        raise HTTPException(409, "A payment with that reference is already recorded")
    pay = licensing.record_payment(db, lic, int(round(body.amount * 100)), body.months, ref)
    audit.record(db, "master.payment_recorded", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=licence_id,
                 detail={"amount": body.amount, "months": body.months, "reference": ref})
    return {"paid_through": pay.period_end}


def _open_business(db: Session, lic: Licence, business_name: str, email: str, password: str, timezone: str, base_currency: str) -> User:
    """Spend a licence on a new business and its owner, in one transaction (either all of it happens or none of it does)."""
    tenant = Tenant(name=business_name.strip(), timezone=timezone, base_currency=base_currency.upper())
    db.add(tenant); db.flush()
    user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(password), role="owner")
    db.add(user)
    lic.status, lic.redeemed_at, lic.tenant_id = "active", utcnow(), tenant.id
    db.commit()
    db.refresh(user)
    return user


class DemoBusinessesIn(BaseModel):
    kinds: list[str] = Field(default_factory=lambda: [k.id for k in demo.KINDS], min_length=1, max_length=len(demo.KINDS))


@router.post("/demo-businesses")
def create_demo_businesses(body: DemoBusinessesIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Ready-made businesses to show a prospect or to try the app: each has an owner login, a free licence (so it can be
    revoked like any other) and four months of sample activity. The passwords are returned here once and kept nowhere."""
    user = master_user(authorization, db)
    kinds = [demo.kind_by_id(k) for k in dict.fromkeys(body.kinds)]
    made = []
    for kind in kinds:
        issued = licensing.issue(db, 1, f"Demo: {kind.title}", 0, "USD", user.id)[0]
        lic = licensing.lock_unused(db, issued["key"])
        email, password = f"demo-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(9)
        owner = _open_business(db, lic, kind.business_name, email, password, "Africa/Nairobi", "KES")
        result = demo.load_sample(db, owner.tenant_id, kind.id)
        audit.record(db, "master.demo_business_created", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=lic.id, detail={"kind": kind.id})
        made.append({"kind": kind.id, "business_name": kind.business_name, "email": email, "password": password, "tenant_id": owner.tenant_id,
                     "licence_id": lic.id, "events": result["events"], "alerts": result["alerts"]})
    return {"businesses": made}


# ---------------- public: activate a key ----------------

class ActivateIn(BaseModel):
    licence_key: str = Field(min_length=10, max_length=60)
    business_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str
    timezone: str = "Africa/Nairobi"
    base_currency: str = "KES"


@public.post("/activate", response_model=TokenPair)
def activate(body: ActivateIn, request: Request, db: Session = Depends(get_db)):
    """Turn a licence key into a business and its owner. The key is spent in the same transaction that creates them:
    either all of it happens or none of it does, and a second use of the same key is refused."""
    _throttle(request)
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(400, "Unknown timezone")
    if not (len(body.base_currency) == 3 and body.base_currency.isalpha()):
        raise HTTPException(400, "Currency must be a 3-letter code such as KES")
    _check_new_password(body.password)
    lic = licensing.lock_unused(db, body.licence_key)
    if db.query(User.id).filter(User.email == body.email).first():
        db.rollback()
        raise HTTPException(409, "Could not activate with these details")
    user = _open_business(db, lic, body.business_name, body.email, body.password, body.timezone, body.base_currency)
    audit.record(db, "licence.activated", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=lic.id)
    return _tokens(user)
