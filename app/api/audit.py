"""Read access to the audit trail (Section 48). Owners of a tenant see that tenant's entries only; the log is append-only (no write/delete endpoints)."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.models.core import AuditLog

router = APIRouter()


class AuditOut(BaseModel):
    id: str
    ts: datetime
    action: str
    actor_user_id: Optional[str]
    actor_type: str
    target: Optional[str]
    success: bool
    detail: dict
    ip: Optional[str]

    class Config:
        from_attributes = True


@router.get("/", response_model=list[AuditOut])
def list_audit(
    tenant_id: str, action: Optional[str] = Query(default=None, max_length=80), before: Optional[datetime] = None,
    limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    authorize_tenant(authorization, tenant_id, require_role="owner")
    q = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
    if action:
        q = q.filter(AuditLog.action.like(action.replace("%", "").replace("_", r"\_") + "%", escape="\\"))
    if before:
        q = q.filter(AuditLog.ts < before.replace(tzinfo=None))
    return q.order_by(AuditLog.ts.desc()).limit(limit).all()
