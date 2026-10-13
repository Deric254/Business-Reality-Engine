"""
Alert delivery (Section 49).

Outbox pattern: detection never sends anything itself. `enqueue_deliveries`
turns new alerts into (alert, channel) rows — idempotent via a unique pair —
and `process_deliveries` sends due rows, with exponential backoff and a dead
state after MAX_ATTEMPTS. FOR UPDATE SKIP LOCKED lets any number of workers
run it concurrently without double-sending. Delivery is at-least-once (a crash
between "sent" and the commit can resend); webhook receivers should dedupe on
the alert id, which is in every payload.

Channels: email (SMTP), signed webhooks, Slack and Teams incoming webhooks, and SMS / WhatsApp via
Twilio's REST API. Per-channel preferences: severity floor, quiet hours (UTC; critical alerts bypass
them), a daily digest for low-urgency alerts, and kind / entity filters. Webhook-style targets are
validated against SSRF (https only, no private/loopback/link-local addresses) at creation AND at send
time.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
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
CHANNEL_TYPES = ("email", "webhook", "slack", "teams", "sms", "whatsapp")
URL_TYPES = ("webhook", "slack", "teams")
DIGEST_TYPES = ("email", "slack", "teams")
DIGESTABLE = {"medium", "informational", "opportunity"}   # critical / high are never held back
LEASE_MINUTES = 10           # a claimed delivery is invisible to other workers for this long
DIGEST_RETRY_MINUTES = 30
DIGEST_MAX_ITEMS = 50
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")   # E.164
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

def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def _smtp_send(msg: EmailMessage) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise DeliveryError("SMTP_HOST is not configured")
    port = int(os.environ.get("SMTP_PORT", "587"))
    use_ssl = os.environ.get("SMTP_SSL", "").lower() == "true"
    try:
        smtp = smtplib.SMTP_SSL(host, port, timeout=15) if use_ssl else smtplib.SMTP(host, port, timeout=15)
        with smtp:
            if not use_ssl and os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
                smtp.starttls()
            if os.environ.get("SMTP_USERNAME"):
                smtp.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise DeliveryError(f"SMTP error: {type(e).__name__}: {e}")


def send_email(alert: Alert, to_addr: str, kind: str = "alert") -> None:
    _smtp_send(build_email(alert, to_addr, kind))


def send_plain_email(to_addr: str, subject: str, body: str) -> None:
    """Transactional mail (password reset, invitations). Subject is forced to one line."""
    msg = EmailMessage()
    msg["Subject"] = _one_line(subject)
    msg["From"] = os.environ.get("SMTP_FROM", "alerts@localhost")
    msg["To"] = to_addr
    msg.set_content(body)
    _smtp_send(msg)


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


def _post_json(url: str, body: dict, what: str) -> None:
    try:
        validate_webhook_url(url)
    except ValueError as e:
        raise DeliveryError(f"blocked: {e}")
    try:
        r = httpx.post(url, json=body, headers={"User-Agent": "BusinessRealityEngine/1"}, timeout=10, follow_redirects=False)
    except httpx.HTTPError as e:
        raise DeliveryError(f"{what} error: {type(e).__name__}")
    if not 200 <= r.status_code < 300:
        raise DeliveryError(f"{what} returned HTTP {r.status_code}")


def render_alert(alert: Alert, kind: str = "alert") -> tuple[str, str]:
    """(headline, body) as plain text, shared by chat and SMS channels."""
    p = alert_payload(alert, kind)["alert"]
    prefix = "ESCALATED " if kind == "escalation" else ""
    head = _one_line(f"[{prefix}{p['severity'].upper()}] {p['title']}")
    body = _one_line(p["what_happened"] or "")
    if p["recommended_action"]:
        body += f" Recommended: {_one_line(p['recommended_action'])}"
    return head, body.strip()


def render_digest(alerts: list[Alert]) -> tuple[str, list[str]]:
    n = len(alerts)
    subject = f"Daily digest: {n} update{'s' if n != 1 else ''}"
    lines = []
    for a in alerts:
        sev = a.severity.value if hasattr(a.severity, "value") else str(a.severity)
        lines.append(f"[{sev.upper()}] {_one_line(a.title)} - {_alert_url(a)}")
    return subject, lines


def send_slack(text: str, channel: NotificationChannel) -> None:
    _post_json(channel.target, {"text": text[:3000]}, "Slack")


def send_teams(headline: str, body: str, channel: NotificationChannel) -> None:
    card = {"type": "AdaptiveCard", "version": "1.4", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "body": [{"type": "TextBlock", "text": headline[:300], "weight": "Bolder", "wrap": True},
                     {"type": "TextBlock", "text": body[:3000], "wrap": True}]}
    _post_json(channel.target, {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}, "Teams")


def twilio_configured(kind: str) -> bool:
    need = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_FROM" if kind == "whatsapp" else "TWILIO_SMS_FROM"]
    return all(os.environ.get(k) for k in need)


def send_twilio(kind: str, to_number: str, text: str) -> None:
    """SMS or WhatsApp through Twilio's Messages API. `kind` is 'sms' or 'whatsapp'."""
    if not twilio_configured(kind):
        raise DeliveryError("Twilio is not configured")
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    base = os.environ.get("TWILIO_API_BASE", "https://api.twilio.com").rstrip("/")
    frm = os.environ["TWILIO_WHATSAPP_FROM" if kind == "whatsapp" else "TWILIO_SMS_FROM"]
    to = to_number
    if kind == "whatsapp":
        frm = frm if frm.startswith("whatsapp:") else f"whatsapp:{frm}"
        to = f"whatsapp:{to_number}"
    try:
        r = httpx.post(f"{base}/2010-04-01/Accounts/{sid}/Messages.json", data={"From": frm, "To": to, "Body": text[:1500]},
                       auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]), timeout=10, follow_redirects=False)
    except httpx.HTTPError as e:
        raise DeliveryError(f"{kind} error: {type(e).__name__}")
    if not 200 <= r.status_code < 300:
        raise DeliveryError(f"{kind} provider returned HTTP {r.status_code}")


def _send_text(channel: NotificationChannel, headline: str, body: str) -> None:
    """Deliver a pre-rendered message on a text-style channel."""
    t = channel.channel_type
    if t == "slack":
        send_slack(f"*{headline}*\n{body}" if body else f"*{headline}*", channel)
    elif t == "teams":
        send_teams(headline, body, channel)
    elif t in ("sms", "whatsapp"):
        msg = f"{headline}\n{body}" if body else headline
        send_twilio(t, channel.target, msg[:320] if t == "sms" else msg)
    else:
        raise DeliveryError(f"unknown channel type {t}")


def send_to_channel(alert: Alert, channel: NotificationChannel, kind: str = "alert") -> None:
    t = channel.channel_type
    if t == "email":
        send_email(alert, channel.target, kind)
    elif t == "webhook":
        send_webhook(alert, channel, kind=kind)
    elif t in ("slack", "teams", "sms", "whatsapp"):
        head, body = render_alert(alert, kind)
        link = _alert_url(alert)
        _send_text(channel, head, f"{body} {link}".strip() if t != "sms" else f"{body[:200]} {link}".strip())
    else:
        raise DeliveryError(f"unknown channel type {t}")


def send_digest(channel: NotificationChannel, alerts: list[Alert]) -> None:
    subject, lines = render_digest(alerts)
    if channel.channel_type == "email":
        send_plain_email(channel.target, subject, subject + "\n\n" + "\n".join(lines))
    elif channel.channel_type in ("slack", "teams"):
        _send_text(channel, subject, "\n".join(lines))
    else:
        raise DeliveryError(f"digest not supported on {channel.channel_type}")


# ---------- preferences ----------

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def valid_hhmm(value: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or ""))


def in_quiet_hours(now: datetime, start: str | None, end: str | None) -> bool:
    if not start or not end or start == end:
        return False
    cur, s, e = now.hour * 60 + now.minute, _minutes(start), _minutes(end)
    return s <= cur < e if s < e else (cur >= s or cur < e)       # window may cross midnight


def quiet_until(now: datetime, end: str) -> datetime:
    h, m = (int(x) for x in end.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def _severity_of(alert: Alert) -> str:
    return alert.severity.value if hasattr(alert.severity, "value") else str(alert.severity)


def alert_entity(alert: Alert) -> str | None:
    """Entity type an alert is about, taken from its evidence ('sale.amount' -> 'sale')."""
    ev = alert.evidence or {}
    metric = ev.get("metric")
    if isinstance(metric, str) and metric:
        return metric.split(".")[0].split(":")[0]
    ent = ev.get("entity")
    return ent if isinstance(ent, str) else None


def matches_filters(alert: Alert, channel: NotificationChannel) -> bool:
    """Empty filters pass everything. An alert whose entity cannot be determined is never hidden."""
    if channel.filter_kinds and (alert.kind or "") not in channel.filter_kinds:
        return False
    if channel.filter_entities:
        ent = alert_entity(alert)
        if ent is not None and ent not in channel.filter_entities:
            return False
    return True


# ---------- dispatch ----------

def process_deliveries(db: Session, limit: int = 100) -> dict:
    """Send due deliveries. Safe to run from many workers at once: rows are claimed under SKIP LOCKED
    and leased (next_attempt_at pushed out) before the lock is released, so no other worker can pick
    them up while this one is sending. A crash mid-send means a retry after the lease, never a loss."""
    now = datetime.utcnow()
    claimed = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now)
        .order_by(NotificationDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    ids = [d.id for d in claimed]
    for d in claimed:
        d.next_attempt_at = now + timedelta(minutes=LEASE_MINUTES)
    db.commit()
    stats = {"sent": 0, "retry": 0, "dead": 0, "deferred": 0, "held": 0, "skipped": 0}
    for did in ids:
        d = db.get(NotificationDelivery, did)
        if d is None or d.status != "pending":
            continue
        channel = db.get(NotificationChannel, d.channel_id)
        alert = db.get(Alert, d.alert_id)
        try:
            if not channel or not channel.enabled or not alert:
                raise DeliveryError("channel disabled or alert missing")
            if not matches_filters(alert, channel):
                d.status, d.last_error = "skipped", "filtered by channel preferences"
                stats["skipped"] += 1
            elif (channel.digest_mode == "daily" and channel.channel_type in DIGEST_TYPES
                  and d.kind == "alert" and _severity_of(alert) in DIGESTABLE):
                d.status = "held"                              # sent later, inside the daily digest
                stats["held"] += 1
            elif _severity_of(alert) != "critical" and in_quiet_hours(now, channel.quiet_start, channel.quiet_end):
                d.next_attempt_at = quiet_until(now, channel.quiet_end)   # not an attempt, not an error
                stats["deferred"] += 1
            else:
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


def release_held(db: Session) -> int:
    """Held deliveries whose channel no longer digests (or was deleted/disabled-digest) go out normally."""
    res = db.execute(text("""
        UPDATE notification_deliveries d SET status = 'pending', next_attempt_at = (now() AT TIME ZONE 'UTC')
        FROM notification_channels c
        WHERE d.channel_id = c.id AND d.status = 'held' AND c.digest_mode <> 'daily'"""))
    db.commit()
    return res.rowcount or 0


def send_due_digests(db: Session, now: datetime | None = None) -> int:
    """Send each channel's daily digest once per UTC day, at or after its digest_hour. The channel row
    is locked (SKIP LOCKED) for the whole send, so concurrent workers can never send it twice; a failed
    send leaves the items held and is retried after DIGEST_RETRY_MINUTES."""
    now = now or datetime.utcnow()
    sent = 0
    ids = [r[0] for r in db.execute(text(
        "SELECT id FROM notification_channels WHERE enabled AND digest_mode = 'daily'")).all()]
    for cid in ids:
        ch = (db.query(NotificationChannel).filter(NotificationChannel.id == cid, NotificationChannel.digest_mode == "daily",
                                                   NotificationChannel.enabled.is_(True))
              .with_for_update(skip_locked=True).first())
        if ch is None:
            continue
        due = (now.hour >= ch.digest_hour and (ch.last_digest_at is None or ch.last_digest_at.date() < now.date())
               and (ch.last_digest_attempt_at is None
                    or ch.last_digest_attempt_at < now - timedelta(minutes=DIGEST_RETRY_MINUTES)))
        if not due:
            db.rollback()
            continue
        rows = (db.query(NotificationDelivery).filter_by(channel_id=ch.id, status="held")
                .order_by(NotificationDelivery.created_at).limit(DIGEST_MAX_ITEMS).all())
        if not rows:
            ch.last_digest_at = now
            db.commit()
            continue
        alerts = [a for a in (db.get(Alert, r.alert_id) for r in rows) if a is not None]
        ch.last_digest_attempt_at = now
        try:
            if alerts:
                send_digest(ch, alerts)
            for r in rows:
                r.status, r.sent_at, r.last_error = "sent", datetime.utcnow(), None
            ch.last_digest_at = now
            sent += 1
        except Exception as e:
            for r in rows:
                r.last_error = f"digest: {str(e)[:450]}"
            logger.warning(f"Digest for channel {ch.id} failed: {e}")
        db.commit()
    return sent


def dispatch_cycle(db: Session) -> dict:
    enq = enqueue_deliveries(db)
    released = release_held(db)
    stats = process_deliveries(db)
    return {"enqueued": enq, "released": released, **stats, "digests": send_due_digests(db)}
