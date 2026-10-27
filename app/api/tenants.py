from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import decode_token, ensure_active
from app.models.core import Tenant

router = APIRouter()
SUPPORTED_LANGUAGES = {"en", "sw"}


class TenantOut(BaseModel):
    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


@router.get("/", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Scoped to the caller's own tenant — the token is the source of scope.
    No token -> 401; the service key never lists tenants."""
    if not authorization:
        raise HTTPException(401, "Authentication required")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Malformed Authorization header")
    current_user = ensure_active(decode_token(authorization.removeprefix("Bearer "), expected_type="access"))
    return db.query(Tenant).filter(Tenant.id == current_user.tenant_id).all()


class TenantSettings(BaseModel):
    id: str
    name: str
    timezone: str
    base_currency: str
    country: Optional[str] = None
    language: str = "en"
    area: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TenantSettingsUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    base_currency: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    area: Optional[str] = None


def _me(authorization: Optional[str], need_owner: bool = False):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    user = ensure_active(decode_token(authorization.removeprefix("Bearer "), expected_type="access"))
    if need_owner and user.role != "owner":
        raise HTTPException(403, "This action requires the 'owner' role")
    return user


@router.get("/me", response_model=TenantSettings)
def my_tenant(db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    return db.get(Tenant, _me(authorization).tenant_id)


@router.patch("/me", response_model=TenantSettings)
def update_my_tenant(payload: TenantSettingsUpdate, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    import re
    from zoneinfo import available_timezones
    t = db.get(Tenant, _me(authorization, need_owner=True).tenant_id)
    data = payload.model_dump(exclude_unset=True)
    if "timezone" in data and data["timezone"] not in available_timezones():
        raise HTTPException(400, "Unknown timezone (use an IANA name such as Africa/Nairobi)")
    if "base_currency" in data and not re.fullmatch(r"[A-Z]{3}", data["base_currency"] or ""):
        raise HTTPException(400, "base_currency must be a 3-letter code such as KES")
    if data.get("country") is not None:
        data["country"] = data["country"].strip().upper() or None
        if data["country"] and not re.fullmatch(r"[A-Z]{2}", data["country"]):
            raise HTTPException(400, "country must be a 2-letter ISO code such as KE")
    if "language" in data and data["language"] not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(400, "name cannot be empty")
    if "area" in data:
        data["area"] = (data["area"] or "").strip() or None
        if data["area"] and len(data["area"]) > 100:
            raise HTTPException(400, "area must be 100 characters or fewer")
    for k, v in data.items():
        setattr(t, k, v.strip() if k == "name" else v)
    db.commit(); db.refresh(t)
    return t
