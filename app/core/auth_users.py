"""
Per-tenant user authentication (Section 48).

This is layered ON TOP OF the existing service API key
(app/core/auth.py), not a replacement for it — the service key remains
how internal/automation traffic authenticates; JWT is how an actual
human logging into the Control Room authenticates. Applied so far to
the connectors router only (the most credential-sensitive surface), as
a real, tested proof rather than a claim of full app coverage — see
SECURITY.md.
"""
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
import jwt
from fastapi import HTTPException, Header

ACCESS_TOKEN_MINUTES = 30
REFRESH_TOKEN_DAYS = 7
JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set — cannot issue or verify user tokens without it. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return secret


MAX_PASSWORD_BYTES = 72   # bcrypt's hard limit; longer input is rejected, never silently truncated


def hash_password(password: str) -> str:
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError("password too long")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: str, tenant_id: str, role: str, token_type: str, expires_delta: timedelta,
                 must_change: bool = False) -> str:
    from app.core.revocation import now_ms
    now = datetime.utcnow()
    payload = {
        "sub": user_id, "tenant_id": tenant_id, "role": role, "type": token_type,
        "iat": now, "exp": now + expires_delta,
        "jti": uuid.uuid4().hex, "iat_ms": now_ms(),     # unique id + ms-precision issue time, for revocation
    }
    if must_change:
        payload["mcp"] = True                          # password change required before anything else
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_access_token(user_id: str, tenant_id: str, role: str, must_change: bool = False) -> str:
    return create_token(user_id, tenant_id, role, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES), must_change)


def create_refresh_token(user_id: str, tenant_id: str, role: str, must_change: bool = False) -> str:
    return create_token(user_id, tenant_id, role, "refresh", timedelta(days=REFRESH_TOKEN_DAYS), must_change)


class CurrentUser:
    def __init__(self, user_id: str, tenant_id: str, role: str, jti: Optional[str] = None,
                 exp: Optional[float] = None, iat_ms: Optional[int] = None, must_change: bool = False):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.jti = jti
        self.exp = exp
        self.iat_ms = iat_ms
        self.must_change = must_change


def decode_token(token: str, expected_type: str = "access") -> CurrentUser:
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    if payload.get("type") != expected_type:
        article = "an" if expected_type[0] in "aeiou" else "a"
        raise HTTPException(401, f"Expected {article} {expected_type} token")

    from app.core.revocation import is_revoked_fast
    jti, iat_ms = payload.get("jti"), payload.get("iat_ms")
    if not jti or iat_ms is None:
        raise HTTPException(401, "Invalid token")        # tokens from before revocation support are not honoured
    if is_revoked_fast(jti, payload["sub"], iat_ms):
        raise HTTPException(401, "Token has been revoked")
    return CurrentUser(user_id=payload["sub"], tenant_id=payload["tenant_id"], role=payload["role"],
                       jti=jti, exp=float(payload["exp"]), iat_ms=int(iat_ms), must_change=bool(payload.get("mcp")))


def ensure_active(user: CurrentUser) -> CurrentUser:
    """An account on an owner-issued temporary password can only change that password."""
    if user.must_change:
        raise HTTPException(403, "Password change required")
    from app.core.licensing import require_licence
    require_licence(user.tenant_id)
    return user


def get_current_user(authorization: Optional[str] = Header(default=None)) -> CurrentUser:
    """FastAPI dependency — extracts and verifies the Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ")
    return ensure_active(decode_token(token, expected_type="access"))


def require_tenant_match(user: CurrentUser, tenant_id: str):
    """A valid token for tenant A must never authorize an action on tenant B's data."""
    if user.tenant_id != tenant_id:
        raise HTTPException(403, "Token does not authorize access to this tenant")


def require_owner(user: CurrentUser):
    if user.role != "owner":
        raise HTTPException(403, "This action requires the 'owner' role")


def authorize_tenant(authorization: Optional[str], tenant_id: str, require_role: Optional[str] = None) -> CurrentUser:
    """
    Mandatory per-tenant authorization for every user-facing endpoint.
    No header -> 401 (the shared service key alone NEVER grants access to
    a tenant's data). Token must be a valid, unexpired ACCESS token whose
    tenant matches `tenant_id`, with the required role if one is given.
    """
    if not authorization:
        raise HTTPException(401, "Authentication required")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Malformed Authorization header")
    current_user = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
    if current_user.must_change:
        raise HTTPException(403, "Password change required")
    require_tenant_match(current_user, tenant_id)
    from app.core.licensing import require_licence
    require_licence(current_user.tenant_id)
    if require_role == "owner":
        require_owner(current_user)
    return current_user


def authorize_tenant_or_service(authorization: Optional[str], x_api_key: Optional[str], tenant_id: str):
    """
    Machine-to-machine ingestion path ONLY (events API). A bearer token, if
    present, is validated exactly as authorize_tenant (never silently
    falls back); with no token, a valid service API key is accepted.
    """
    if authorization:
        return authorize_tenant(authorization, tenant_id)
    from app.core.auth import require_api_key, service_key_ingest_allowed
    if not service_key_ingest_allowed():
        # The service key is one credential for every business, so on a production server it cannot write events for
        # a business of the caller's choosing. Use a business sign-in, or a connector's own webhook token.
        raise HTTPException(401, "Sign in as the business, or use the connector's webhook token, to send events")
    require_api_key(x_api_key)
    return None
