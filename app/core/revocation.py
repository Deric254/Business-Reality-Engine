"""
Token revocation (Section 48).

  - A token carries a unique `jti`. Logging out, rotating a refresh token or changing a password
    revokes jtis; `revoke_user_tokens` revokes EVERYTHING issued to a user up to now ("sign out
    everywhere", password change, suspected theft).
  - Access tokens are checked on every request against the fast KV store (Redis, else in-process),
    so a revoked access token dies immediately, not after its 30-minute life. If the KV store is
    flushed the exposure is bounded by that 30-minute life.
  - Refresh tokens are single-use and checked against the DATABASE (durable, authoritative) because
    refresh is infrequent. Presenting an already-used refresh token is treated as theft: every token
    of that user is revoked.
"""
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import kv

_JTI = "bre:revoked:jti:"
_USER = "bre:revoked:user:"
_USER_TTL = 8 * 24 * 3600          # longer than a refresh token's life


def now_ms() -> int:
    return int(time.time() * 1000)


def revoke_jti_fast(jti: str, exp_epoch: float) -> None:
    kv.set_ex(_JTI + jti, "1", max(1, int(exp_epoch - time.time())))


def is_revoked_fast(jti: Optional[str], user_id: str, iat_ms: Optional[int]) -> bool:
    if jti and kv.get(_JTI + jti):
        return True
    cutoff = kv.get(_USER + user_id)
    return bool(cutoff and (iat_ms is None or iat_ms <= int(cutoff)))


def revoke_token(db: Session, jti: str, user_id: str, exp_epoch: float) -> None:
    """Durable record (for refresh-token reuse detection) + fast-path entry."""
    db.execute(text(
        "INSERT INTO revoked_tokens (jti, user_id, expires_at) VALUES (:j, CAST(:u AS uuid), :e) ON CONFLICT (jti) DO NOTHING"),
        {"j": jti, "u": user_id, "e": datetime.utcfromtimestamp(exp_epoch)})
    revoke_jti_fast(jti, exp_epoch)


def consume_refresh_token(db: Session, jti: str, user_id: str, exp_epoch: float) -> bool:
    """Atomically mark a refresh token used. False if it was already used/revoked (=> reuse)."""
    row = db.execute(text(
        "INSERT INTO revoked_tokens (jti, user_id, expires_at) VALUES (:j, CAST(:u AS uuid), :e) "
        "ON CONFLICT (jti) DO NOTHING RETURNING jti"),
        {"j": jti, "u": user_id, "e": datetime.utcfromtimestamp(exp_epoch)}).first()
    return row is not None


def revoke_user_tokens(db: Session, user_id: str) -> int:
    """Everything issued to this user up to this instant becomes invalid. Durable (users.tokens_valid_after_ms)
    and fast (KV)."""
    ms = now_ms()
    db.execute(text("UPDATE users SET tokens_valid_after_ms = :m WHERE id = CAST(:u AS uuid)"), {"m": ms, "u": user_id})
    kv.set_ex(_USER + user_id, str(ms), _USER_TTL)
    return ms


def purge_expired(db: Session) -> int:
    r = db.execute(text("DELETE FROM revoked_tokens WHERE expires_at < (now() AT TIME ZONE 'UTC')"))
    return r.rowcount or 0
