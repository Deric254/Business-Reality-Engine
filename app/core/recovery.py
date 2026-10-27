"""
Password recovery and email invitations (Section 48).

Tokens are 256-bit random, single-use and expiring; only their SHA-256 is stored. Issuing a new token
voids older unused ones of the same purpose. The public endpoints answer identically whether or not an
email is registered (no account enumeration) and send mail in a background task so response time does
not leak it either.
"""
import hashlib
import os
import secrets
from datetime import timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import kv
from app.core.notifications import DeliveryError, send_plain_email, smtp_configured
from app.models.core import PasswordResetToken, User

import logging
from app.core.clock import utcnow
logger = logging.getLogger("business_reality_engine")

RESET_TTL = timedelta(minutes=60)
INVITE_TTL = timedelta(days=7)
MAX_RESETS_PER_EMAIL_PER_HOUR = 3


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(db: Session, user: User, purpose: str) -> str:
    assert purpose in ("reset", "invite")
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.purpose == purpose,
        PasswordResetToken.used_at.is_(None)).update({"used_at": utcnow()})
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(user_id=user.id, token_hash=_hash(raw), purpose=purpose,
                              expires_at=utcnow() + (RESET_TTL if purpose == "reset" else INVITE_TTL)))
    db.commit()
    return raw


def consume_token(db: Session, raw: str) -> Optional[tuple[str, str]]:
    """Atomically spends a valid token. Returns (user_id, purpose) or None. Two concurrent calls with the
    same token: exactly one wins."""
    row = db.execute(text("""
        UPDATE password_reset_tokens SET used_at = (now() AT TIME ZONE 'UTC')
        WHERE token_hash = :h AND used_at IS NULL AND expires_at > (now() AT TIME ZONE 'UTC')
        RETURNING user_id, purpose"""), {"h": _hash(raw)}).first()
    db.commit()
    return (row[0], row[1]) if row else None


def email_allowed(email: str) -> bool:
    """Per-address throttle for recovery mail (stops mailbox flooding)."""
    return kv.incr_ex(f"bre:recovery:{email.strip().lower()}", 3600) <= MAX_RESETS_PER_EMAIL_PER_HOUR


def link(path: str, token: str) -> str:
    return f"{os.environ.get('APP_BASE_URL', 'http://localhost:5173').rstrip('/')}/{path}?token={token}"


def send_reset_email(email: str, token: str) -> None:
    body = ("Someone asked to reset the password for your Business Reality Engine account.\n\n"
            f"Choose a new password here (valid for 60 minutes, one use):\n{link('reset-password', token)}\n\n"
            "If this was not you, ignore this email - your password has not changed.")
    _deliver(email, "Reset your password", body)


def send_invite_email(email: str, token: str, business: str) -> None:
    body = (f"You have been invited to {business} on Business Reality Engine.\n\n"
            f"Set your password and sign in (valid for 7 days, one use):\n{link('accept-invite', token)}\n")
    _deliver(email, f"You're invited to {business}", body)


def _deliver(email: str, subject: str, body: str) -> None:
    try:
        send_plain_email(email, subject, body)
    except DeliveryError as e:          # never surfaces to the (unauthenticated) caller
        logger.warning(f"Recovery email to {email[:3]}*** failed: {e}")


def mail_enabled() -> bool:
    return smtp_configured()
