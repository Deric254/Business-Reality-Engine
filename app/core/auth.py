import hmac
from fastapi import Header, HTTPException
from app.core.config import get_settings


def require_api_key(x_api_key: str = Header(default=None)):
    """
    Stopgap auth: every request must carry a valid service API key.
    This is NOT per-tenant authorization — it only proves the caller is a
    trusted client of the service. Per-tenant user auth (Section 48 RBAC)
    now covers most tenant-scoped routers on top of this — see SECURITY.md.
    """
    settings = get_settings()
    if not settings.API_KEY:
        # Dev mode with no key configured — allow through, but this must
        # never be true in production (enforced in get_settings()).
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.API_KEY):
        # Constant-time comparison — a naive != leaks timing information
        # about how many leading characters matched. Same principle
        # already applied to webhook token verification; this check
        # hadn't had it until now, worth fixing for consistency.
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def require_admin_key(x_admin_key: str = Header(default=None)):
    """
    SECURITY.md item 9/1: /api/system/* is deliberately cross-tenant (an
    ops view spanning every tenant) — the per-tenant JWT pattern used
    everywhere else is the wrong tool for it. This is a SEPARATE
    credential from the general service API key: holding API_KEY alone
    (which every tenant-scoped router already accepts) must NOT be
    sufficient to see cross-tenant connector/scheduler data.
    """
    settings = get_settings()
    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoints are not configured for this deployment (ADMIN_API_KEY not set)",
        )
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
