"""Alert delivery channels (Section 49). Reads need a tenant token; changes need an OWNER token."""
import secrets as _secrets
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.core.notifications import (
    DeliveryError, SEVERITIES, alert_payload, send_email, send_webhook, validate_webhook_url,
)
from app.core.secrets import encrypt_secret
from app.models.alerts import Alert, AlertSeverity
from app.models.config import NotificationChannel, NotificationDelivery

router = APIRouter()
MAX_CHANNELS_PER_TENANT = 20


class ChannelCreate(BaseModel):
    tenant_id: str
    channel_type: Literal["email", "webhook"]
    target: str
    label: Optional[str] = None
    min_severity: Literal["critical", "high", "medium", "informational", "opportunity"] = "high"


class ChannelUpdate(BaseModel):
    enabled: Optional[bool] = None
    min_severity: Optional[Literal["critical", "high", "medium", "informational", "opportunity"]] = None
    label: Optional[str] = None


class ChannelOut(BaseModel):
    id: str
    channel_type: str
    target: str
    label: Optional[str]
    min_severity: str
    enabled: bool
    signing_secret: Optional[str] = None   # returned ONCE, at creation of a webhook channel

    class Config:
        from_attributes = True


def _owned(db: Session, channel_id: str, tenant_id: str) -> NotificationChannel:
    ch = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id, NotificationChannel.tenant_id == tenant_id).first()
    if not ch:
        raise HTTPException(404, "Channel not found")   # 404, never reveal another tenant's channel
    return ch


@router.post("/channels", response_model=ChannelOut)
def create_channel(payload: ChannelCreate, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if db.query(NotificationChannel).filter_by(tenant_id=payload.tenant_id).count() >= MAX_CHANNELS_PER_TENANT:
        raise HTTPException(409, f"At most {MAX_CHANNELS_PER_TENANT} channels per business")
    secret = None
    if payload.channel_type == "email":
        from email_validator import validate_email, EmailNotValidError
        try:
            target = validate_email(payload.target, check_deliverability=False).normalized
        except EmailNotValidError:
            raise HTTPException(400, "target must be a valid email address")
    else:
        target = payload.target.strip()
        try:
            validate_webhook_url(target)
        except ValueError as e:
            raise HTTPException(400, str(e))
        secret = _secrets.token_urlsafe(32)
    ch = NotificationChannel(
        tenant_id=payload.tenant_id, channel_type=payload.channel_type, target=target, label=payload.label,
        min_severity=payload.min_severity, secret_encrypted=encrypt_secret(secret) if secret else None,
    )
    db.add(ch); db.commit(); db.refresh(ch)
    audit.record(db, "channel.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id, target=ch.id,
                 detail={"type": payload.channel_type, "min_severity": payload.min_severity})
    out = ChannelOut.model_validate(ch)
    out.signing_secret = secret
    return out


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return db.query(NotificationChannel).filter_by(tenant_id=tenant_id).order_by(NotificationChannel.created_at).all()


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: str, tenant_id: str, payload: ChannelUpdate, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    ch = _owned(db, channel_id, tenant_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    db.commit(); db.refresh(ch)
    audit.record(db, "channel.update", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=channel_id,
                 detail=payload.model_dump(exclude_unset=True))
    return ch


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    ch = _owned(db, channel_id, tenant_id)
    db.delete(ch); db.commit()
    audit.record(db, "channel.delete", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=channel_id)
    return {"deleted": True}


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Sends a clearly-labelled sample alert right now and reports success/failure."""
    authorize_tenant(authorization, tenant_id, require_role="owner")
    ch = _owned(db, channel_id, tenant_id)
    sample = Alert(
        id="00000000-0000-0000-0000-000000000000", tenant_id=tenant_id, severity=AlertSeverity.informational,
        title="Test alert from Business Reality Engine",
        what_happened="This is a test message. If you can read it, alert delivery to this channel works.",
        evidence={}, created_at=datetime.utcnow(),
    )
    try:
        if ch.channel_type == "email":
            send_email(sample, ch.target)
        else:
            payload = alert_payload(sample); payload["event"] = "alert.test"
            send_webhook(sample, ch, payload)
    except DeliveryError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@router.get("/deliveries")
def list_deliveries(tenant_id: str, limit: int = 50, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = (
        db.query(NotificationDelivery).filter_by(tenant_id=tenant_id)
        .order_by(NotificationDelivery.created_at.desc()).limit(min(max(limit, 1), 200)).all()
    )
    return [
        {"id": d.id, "alert_id": d.alert_id, "channel_id": d.channel_id, "status": d.status,
         "attempts": d.attempts, "last_error": d.last_error, "sent_at": d.sent_at, "created_at": d.created_at}
        for d in rows
    ]
