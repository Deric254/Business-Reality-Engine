"""
System self-monitoring (Section 51). Internal ops endpoints — deliberately
separate from the tenant-facing Data Coverage Map (Section 15). Coverage
answers "can this business trust its alerts"; this answers "is the engine
itself healthy" — connector failures across ALL tenants, scheduler run
history. Not something a business's own users should see.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Request
from app.core import audit
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from zoneinfo import available_timezones
from fastapi import HTTPException
from pydantic import EmailStr, Field
from app.core.auth_users import hash_password
from app.models.config import MonitoredMetric
from app.models.core import Connector, SchedulerRun, Tenant, User

router = APIRouter()


class SchedulerRunOut(BaseModel):
    id: str
    started_at: datetime
    completed_at: Optional[datetime]
    tenants_checked: Optional[str]
    checks_run: Optional[str]
    errors_count: Optional[str]
    status: str

    class Config:
        from_attributes = True


@router.get("/scheduler-runs", response_model=list[SchedulerRunOut])
def scheduler_runs(limit: int = 20, db: Session = Depends(get_db)):
    return db.query(SchedulerRun).order_by(SchedulerRun.started_at.desc()).limit(limit).all()


@router.get("/connector-health")
def connector_health(db: Session = Depends(get_db)):
    """
    Every active connector across every tenant, grouped by last_sync_status.
    Answers 'which connectors are actually failing right now' across the
    whole system — the thing an operator checks, not a business owner.
    """
    connectors = db.query(Connector).filter(Connector.is_active == True).all()
    by_status = {"success": [], "partial": [], "failed": [], "never_synced": []}
    for c in connectors:
        key = c.last_sync_status if c.last_sync_status in by_status else "never_synced"
        by_status[key].append({
            "connector_id": c.id, "tenant_id": c.tenant_id, "name": c.name,
            "connector_type": c.connector_type, "last_sync_at": c.last_sync_at,
        })
    return {
        "total_active": len(connectors),
        "by_status": {k: {"count": len(v), "connectors": v} for k, v in by_status.items()},
    }


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "Africa/Nairobi"
    base_currency: str = Field(default="KES", pattern=r"^[A-Z]{3}$")
    owner_email: Optional[EmailStr] = None
    owner_password: Optional[str] = Field(default=None, min_length=8, max_length=72)


@router.post("/tenants")
def create_tenant(payload: TenantCreate, request: Request, db: Session = Depends(get_db)):
    """Onboard a business (Section 50): creates the tenant, seeds the default
    monitored metrics, and optionally its first owner login. Admin key only."""
    if payload.timezone not in available_timezones():
        raise HTTPException(400, "Unknown timezone (use an IANA name such as Africa/Nairobi)")
    if payload.owner_password and len(payload.owner_password.encode()) > 72:
        raise HTTPException(400, "Password must be at most 72 bytes")
    if bool(payload.owner_email) != bool(payload.owner_password):
        raise HTTPException(400, "Provide both owner_email and owner_password, or neither")
    if payload.owner_email and db.query(User).filter(User.email == payload.owner_email).first():
        raise HTTPException(409, "Could not create this account")
    tenant = Tenant(name=payload.name.strip(), timezone=payload.timezone, base_currency=payload.base_currency)
    db.add(tenant)
    db.flush()
    for entity in ("sale", "expense"):
        db.add(MonitoredMetric(tenant_id=tenant.id, entity=entity, field="amount",
                               direction="rise" if entity == "expense" else "drop"))
    if payload.owner_email:
        db.add(User(tenant_id=tenant.id, email=payload.owner_email, password_hash=hash_password(payload.owner_password), role="owner"))
    db.commit()
    audit.record(db, "tenant.create", request=request, tenant_id=tenant.id, actor_type="admin", target=tenant.id,
                 detail={"name": tenant.name, "owner_created": bool(payload.owner_email)})
    return {"id": tenant.id, "name": tenant.name, "timezone": tenant.timezone,
            "base_currency": tenant.base_currency, "owner_created": bool(payload.owner_email)}
