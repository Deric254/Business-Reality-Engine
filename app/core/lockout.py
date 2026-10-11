"""
Account lockout (Section 48): MAX_FAILURES wrong passwords for one email inside WINDOW locks that
email for LOCK_SECONDS. Counted per email whether or not the account exists, so the response never
reveals which emails are registered. Trade-off, stated: an attacker who knows an email can keep it
locked (a nuisance, not a breach); the lock is short and a success resets the counter.
"""
import os

from fastapi import HTTPException

from app.core import kv

MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
WINDOW_SECONDS = int(os.environ.get("LOGIN_FAILURE_WINDOW_SECONDS", "900"))
LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "900"))


def _key(kind: str, email: str) -> str:
    return f"bre:login:{kind}:{email.strip().lower()}"


def check_not_locked(email: str) -> None:
    if kv.get(_key("lock", email)):
        raise HTTPException(429, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(max(1, kv.ttl(_key("lock", email))))})


def record_failure(email: str) -> bool:
    """Returns True when this failure triggered a lock."""
    n = kv.incr_ex(_key("fail", email), WINDOW_SECONDS)
    if n >= MAX_FAILURES:
        kv.set_ex(_key("lock", email), "1", LOCK_SECONDS)
        kv.delete(_key("fail", email))
        return True
    return False


def record_success(email: str) -> None:
    kv.delete(_key("fail", email))
