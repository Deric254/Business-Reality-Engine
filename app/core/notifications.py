"""
Alert delivery (Section 49).

Outbox pattern: detection never sends anything itself. `enqueue_deliveries`
turns new alerts into (alert, channel) rows — idempotent via a unique pair —
and `process_deliveries` sends due rows, with exponential backoff and a dead
state after MAX_ATTEMPTS. FOR UPDATE SKIP LOCKED lets any number of workers
run it concurrently without double-sending. Delivery is at-least-once (a crash
between "sent" and the commit can resend); webhook receivers should dedupe on
the alert id, which is in every payload.

Channels: email (SMTP) and signed webhooks. Webhook targets are validated
against SSRF (https only, no private/loopback/link-local addresses) at
creation AND at send time.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import smtplib
import socket
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.secrets import decrypt_secret
from app.models.alerts import Alert
from app.models.config import NotificationChannel, NotificationDelivery

logger = logging.getLogger("business_reality_engine")

SEVERITIES = ("critical", "high", "medium", "informational", "opportunity")
MAX_ATTEMPTS = 5
BACKOFF_MINUTES = [1, 5, 30, 120, 360]
BACKFILL_WINDOW_DAYS = 7     # never notify about alerts older than this (no flood on channel creation)


class DeliveryError(Exception):
    pass


# ---------- SSRF guard ----------

def _allow_private() -> bool:
    return os.environ.get("ALLOW_PRIVATE_WEBHOOK_TARGETS", "").lower() == "true"


def validate_webhook_url(url: str) -> None:
    """Raises ValueError unless `url` is a public https endpoint."""
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (_allow_private() and parsed.scheme == "http"):
        raise ValueError("webhook URL must use https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("webhook URL must have a host and no embedded credentials")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("webhook host does not resolve")
    if not _allow_private():
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise ValueError("webhook host resolves to a non-public address")


# ---------- enqueue ----------

_ENQUEUE_SQL = """
    INSERT INTO notification_deliveries
        (id, tenant_id, alert_id, channel_id, kind, status, attempts, next_attempt_at, created_at)
    SELECT gen_random_uuid(), a.tenant_id, a.id, c.id, :kind, 'pending', 0, (now() AT TIME ZONE 'UTC'), (now() AT TIME ZONE 'UTC')
    FROM alerts a
    JOIN notification_channels c ON c.tenant_id = a.tenant_id AND c.enabled
    WHERE {when}
      AND a.state::text NOT IN ('resolved', 'dismissed', 'false_positive')
      AND CASE a.severity::text WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END
          >= CASE c.min_severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END
    ON CONFLICT (alert_id, channel_id, kind) DO NOTHING
"""
_NEW_ALERT = """a.created_at >= c.created_at
      AND a.created_at >= (now() AT TIME ZONE 'UTC') - make_interval(days => :days)"""
# Escalation: the alert was escalated (alert_engine stamps evidence.escalated_at) after the channel
# existed, and is recent enough to still matter.
_ESCALATED = """(a.evidence->>'escalated_at') IS NOT NULL
      AND (a.evidence->>'escalated_at')::timestamp >= c.created_at
      AND (a.evidence->>'escalated_at')::timestamp >= (now() AT TIME ZONE 'UTC') - make_interval(days => :days)"""


def enqueue_deliveries(db: Session) -> int:
    """Create pending deliveries for recent alerts (kind 'alert') and for escalated alerts
    (kind 'escalation') that match an enabled channel's severity floor. Idempotent: the
    (alert, channel, kind) triple is unique, so each alert notifies once and escalates once.
    All timestamps are naive UTC; `now() AT TIME ZONE 'UTC'` keeps that true even when the
    database server's own time zone is not UTC."""
    total = 0
    for kind, when in (("alert", _NEW_ALERT), ("escalation", _ESCALATED)):
        res = db.execute(text(_ENQUEUE_SQL.format(when=when)), {"kind": kind, "days": BACKFILL_WINDOW_DAYS})
        total += res.rowcount or 0
    db.commit()
    return total


# ---------- message building ----------

def _alert_url(alert: Alert) -> str:
    base = os.environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/alert/{alert.id}"


def alert_payload(alert: Alert, kind: str = "alert") -> dict:
    return {
        "event": "alert.escalated" if kind == "escalation" else "alert.created",
        "alert": {
            "id": alert.id,
            "severity": alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity),
            "title": alert.title,
            "what_happened": alert.what_happened,
            "recommended_action": alert.recommended_action,
            "confidence": float(alert.confidence) if alert.confidence is not None else None,
            "created_at": alert.created_at.isoformat() + "Z" if alert.created_at else None,
            "url": _alert_url(alert),
        },
    }


def _one_line(value: str) -> str:
    return " ".join((value or "").split())   # strips CR/LF: header-injection safe


def build_email(alert: Alert, to_addr: str, kind: str = "alert") -> EmailMessage:
    p = alert_payload(alert, kind)["alert"]
    msg = EmailMessage()
    prefix = "ESCALATED " if kind == "escalation" else ""
    msg["Subject"] = _one_line(f"[{prefix}{p['severity'].upper()}] {p['title']}")
    msg["From"] = os.environ.get("SMTP_FROM", "alerts@localhost")
    msg["To"] = to_addr
    lines = [p["title"], ""]
    if kind == "escalation":
        lines += ["This alert has gone unacknowledged and needs attention.", ""]
    lines += [p["what_happened"] or ""]
    if p["recommended_action"]:
        lines += ["", f"Recommended: {p['recommended_action']}"]
    lines += ["", f"Details: {p['url']}"]
    msg.set_content("\n".join(lines))
    return msg


# ---------- senders ----------

def send_email(alert: Alert, to_addr: str, kind: str = "alert") -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise DeliveryError("SMTP_HOST is not configured")
    port = int(os.environ.get("SMTP_PORT", "587"))
    msg = build_email(alert, to_addr, kind)
    try:
        if os.environ.get("SMTP_SSL", "").lower() == "true":
            smtp = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            smtp = smtplib.SMTP(host, port, timeout=15)
        with smtp:
            if os.environ.get("SMTP_SSL", "").lower() != "true" and os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
                smtp.starttls()
            if os.environ.get("SMTP_USERNAME"):
                smtp.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise DeliveryError(f"SMTP error: {type(e).__name__}: {e}")


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def send_webhook(alert: Alert, channel: NotificationChannel, payload: dict | None = None, kind: str = "alert") -> None:
    try:
        validate_webhook_url(channel.target)   # re-checked at send time (DNS may have changed)
    except ValueError as e:
        raise DeliveryError(f"blocked: {e}")
    body = json.dumps(payload or alert_payload(alert, kind), separators=(",", ":")).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-BRE-Timestamp": ts, "User-Agent": "BusinessRealityEngine/1"}
    if channel.secret_encrypted:
        headers["X-BRE-Signature"] = sign_payload(decrypt_secret(channel.secret_encrypted), ts, body)
    try:
        r = httpx.post(channel.target, content=body, headers=headers, timeout=10, follow_redirects=False)
    except httpx.HTTPError as e:
        raise DeliveryError(f"webhook error: {type(e).__name__}")
    if not 200 <= r.status_code < 300:
        raise DeliveryError(f"webhook returned HTTP {r.status_code}")


def send_to_channel(alert: Alert, channel: NotificationChannel, kind: str = "alert") -> None:
    if channel.channel_type == "email":
        send_email(alert, channel.target, kind)
    elif channel.channel_type == "webhook":
        send_webhook(alert, channel, kind=kind)
    else:
        raise DeliveryError(f"unknown channel type {channel.channel_type}")


# ---------- dispatch ----------

def process_deliveries(db: Session, limit: int = 100) -> dict:
    """Send due deliveries. Safe to run from many workers at once."""
    now = datetime.utcnow()
    rows = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now)
        .order_by(NotificationDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    stats = {"sent": 0, "retry": 0, "dead": 0}
    for d in rows:
        channel = db.get(NotificationChannel, d.channel_id)
        alert = db.get(Alert, d.alert_id)
        try:
            if not channel or not channel.enabled or not alert:
                raise DeliveryError("channel disabled or alert missing")
            send_to_channel(alert, channel, d.kind)
            d.status, d.sent_at, d.last_error = "sent", datetime.utcnow(), None
            stats["sent"] += 1
        except Exception as e:   # one bad channel never blocks the rest
            d.attempts += 1
            d.last_error = str(e)[:500]
            if d.attempts >= MAX_ATTEMPTS:
                d.status = "dead"
                stats["dead"] += 1
            else:
                d.next_attempt_at = datetime.utcnow() + timedelta(minutes=BACKOFF_MINUTES[d.attempts - 1])
                stats["retry"] += 1
            logger.warning(f"Delivery {d.id} failed (attempt {d.attempts}): {d.last_error}")
        db.commit()   # commit per row: a crash later never re-sends earlier ones
    return stats


def dispatch_cycle(db: Session) -> dict:
    return {"enqueued": enqueue_deliveries(db), **process_deliveries(db)}
