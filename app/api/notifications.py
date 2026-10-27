"""Alert delivery channels (Section 49). Reads need a tenant token; changes need an OWNER token."""
import secrets as _secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.core.notifications import (
    DIGEST_TYPES, PHONE_RE, DeliveryError, alert_payload, render_alert,
    send_email, send_webhook, _send_text, validate_webhook_url, valid_hhmm,
)
from app.core.secrets import encrypt_secret
from app.models.alerts import Alert, AlertSeverity
from app.models.config import NotificationChannel, NotificationDelivery
from app.core.clock import utcnow

router = APIRouter()
MAX_CHANNELS_PER_TENANT = 20


Severity = Literal["critical", "high", "medium", "informational", "opportunity"]
ChannelType = Literal["email", "webhook", "slack", "teams", "sms", "whatsapp"]
KINDS = {"deviation", "opportunity", "trend", "daily", "silence", "threshold", "forecast", "risk", "unknown", "quality"}


class ChannelCreate(BaseModel):
    tenant_id: str
    channel_type: ChannelType
    target: str
    label: Optional[str] = None
    min_severity: Severity = "high"
    quiet_start: Optional[str] = None          # UTC "HH:MM"
    quiet_end: Optional[str] = None
    digest_mode: Literal["off", "daily"] = "off"
    digest_hour: int = 7                       # UTC hour
    filter_kinds: Optional[list[str]] = None
    filter_entities: Optional[list[str]] = None


class ChannelUpdate(BaseModel):
    enabled: Optional[bool] = None
    min_severity: Optional[Severity] = None
    label: Optional[str] = None
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    clear_quiet_hours: bool = False
    digest_mode: Optional[Literal["off", "daily"]] = None
    digest_hour: Optional[int] = None
    filter_kinds: Optional[list[str]] = None
    filter_entities: Optional[list[str]] = None
    clear_filters: bool = False


class ChannelOut(BaseModel):
    id: str
    channel_type: str
    target: str
    label: Optional[str]
    min_severity: str
    enabled: bool
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    digest_mode: str = "off"
    digest_hour: int = 7
    filter_kinds: Optional[list[str]] = None
    filter_entities: Optional[list[str]] = None
    signing_secret: Optional[str] = None   # returned ONCE, at creation of a webhook channel

    model_config = ConfigDict(from_attributes=True)


def _normalize_target(channel_type: str, raw: str) -> tuple[str, Optional[str]]:
    """Validated target plus a signing secret (webhooks only; shown once)."""
    raw = (raw or "").strip()
    if channel_type == "email":
        from email_validator import validate_email, EmailNotValidError
        try:
            return validate_email(raw, check_deliverability=False).normalized, None
        except EmailNotValidError:
            raise HTTPException(400, "target must be a valid email address")
    if channel_type in ("sms", "whatsapp"):
        num = raw.replace(" ", "").replace("-", "")
        if not PHONE_RE.match(num):
            raise HTTPException(400, "target must be a phone number in international format, e.g. +254712345678")
        return num, None
    try:
        validate_webhook_url(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return raw, (_secrets.token_urlsafe(32) if channel_type == "webhook" else None)


def _check_prefs(channel_type: str, *, quiet_start, quiet_end, digest_mode, digest_hour, filter_kinds, filter_entities) -> None:
    if (quiet_start is None) != (quiet_end is None):
        raise HTTPException(400, "quiet hours need both a start and an end")
    for v in (quiet_start, quiet_end):
        if v is not None and not valid_hhmm(v):
            raise HTTPException(400, "quiet hours must be UTC times formatted HH:MM")
    if not 0 <= digest_hour <= 23:
        raise HTTPException(400, "digest_hour must be 0-23")
    if digest_mode == "daily" and channel_type not in DIGEST_TYPES:
        raise HTTPException(400, f"daily digests are available on {', '.join(DIGEST_TYPES)} channels")
    if filter_kinds is not None:
        bad = [k for k in filter_kinds if k not in KINDS]
        if bad:
            raise HTTPException(400, f"unknown alert kind: {bad[0]}")
    for lst in (filter_kinds, filter_entities):
        if lst is not None and (len(lst) > 50 or any(not isinstance(x, str) or not x or len(x) > 64 for x in lst)):
            raise HTTPException(400, "filters must be short lists of non-empty strings")


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
    target, secret = _normalize_target(payload.channel_type, payload.target)
    _check_prefs(payload.channel_type, quiet_start=payload.quiet_start, quiet_end=payload.quiet_end,
                 digest_mode=payload.digest_mode, digest_hour=payload.digest_hour,
                 filter_kinds=payload.filter_kinds, filter_entities=payload.filter_entities)
    ch = NotificationChannel(
        tenant_id=payload.tenant_id, channel_type=payload.channel_type, target=target, label=payload.label,
        min_severity=payload.min_severity, secret_encrypted=encrypt_secret(secret) if secret else None,
        quiet_start=payload.quiet_start, quiet_end=payload.quiet_end, digest_mode=payload.digest_mode,
        digest_hour=payload.digest_hour, filter_kinds=payload.filter_kinds or None,
        filter_entities=payload.filter_entities or None,
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
    data = payload.model_dump(exclude_unset=True)
    clear_q, clear_f = data.pop("clear_quiet_hours", False), data.pop("clear_filters", False)
    if clear_q:
        data["quiet_start"] = data["quiet_end"] = None
    if clear_f:
        data["filter_kinds"] = data["filter_entities"] = None
    merged = {k: data.get(k, getattr(ch, k)) for k in
              ("quiet_start", "quiet_end", "digest_mode", "digest_hour", "filter_kinds", "filter_entities")}
    _check_prefs(ch.channel_type, **merged)
    for k, v in data.items():
        if k in ("filter_kinds", "filter_entities") and v == []:
            v = None
        setattr(ch, k, v)
    db.commit(); db.refresh(ch)
    audit.record(db, "channel.update", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=channel_id,
                 detail=data)
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
        evidence={}, created_at=utcnow(),
    )
    try:
        if ch.channel_type == "email":
            send_email(sample, ch.target)
        elif ch.channel_type == "webhook":
            payload = alert_payload(sample); payload["event"] = "alert.test"
            send_webhook(sample, ch, payload)
        else:
            head, body = render_alert(sample)
            _send_text(ch, head, body)
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
