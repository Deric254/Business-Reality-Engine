"""AI providers a business connects in the app. Keys are encrypted at rest and never returned."""
from typing import Optional
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth_users import authorize_tenant, decode_token, ensure_active
from app.core.db import get_db
from app.core.netguard import check_host_allowed
from app.core.secrets import encrypt_secret
from app.models.config import AIProvider
from app.reasoning.llm_providers import build_provider

router = APIRouter()
KINDS = {"anthropic", "openai", "openai_compatible"}


class ProviderIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    kind: str
    api_key: str = Field(min_length=8, max_length=500)
    model: str = Field(default="", max_length=120)
    base_url: Optional[str] = Field(default=None, max_length=300)


class ProviderOut(BaseModel):
    id: str
    label: str
    kind: str
    model: str
    base_url: Optional[str]
    priority: int
    is_active: bool


def _owner(authorization: Optional[str], tenant_id: str):
    authorize_tenant(authorization, tenant_id)
    user = ensure_active(decode_token(authorization.removeprefix("Bearer "), expected_type="access"))
    if user.role != "owner":
        raise HTTPException(403, "Only the owner can manage AI keys")
    return user


def _out(r: AIProvider) -> ProviderOut:
    return ProviderOut(id=r.id, label=r.label, kind=r.kind, model=r.model, base_url=r.base_url,
                       priority=r.priority, is_active=r.is_active)


def _clean_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise HTTPException(400, "The base URL must be a full https:// address")
    check_host_allowed(p.hostname)
    return url


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    rows = db.query(AIProvider).filter(AIProvider.tenant_id == tenant_id).order_by(AIProvider.priority, AIProvider.created_at).all()
    return [_out(r) for r in rows]


@router.post("/providers", response_model=ProviderOut)
def add_provider(tenant_id: str, body: ProviderIn, request: Request, db: Session = Depends(get_db),
                 authorization: Optional[str] = Header(default=None)):
    me = _owner(authorization, tenant_id)
    if body.kind not in KINDS:
        raise HTTPException(400, f"type must be one of {sorted(KINDS)}")
    base_url = _clean_url(body.base_url)
    key = body.api_key.strip()
    # Try it before saving, so a typo in the key or model is caught here and not later inside an alert.
    try:
        build_provider(body.kind, key, body.model.strip() or None, base_url).generate_json(
            "Reply with the single word OK.", "ping", max_tokens=16)
    except Exception as exc:
        audit.record(db, "ai.provider.rejected", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, success=False,
                     detail={"label": body.label, "kind": body.kind})
        db.commit()
        raise HTTPException(400, f"That key didn't work: {str(exc)[:200]}")
    last = db.query(AIProvider).filter(AIProvider.tenant_id == tenant_id).order_by(AIProvider.priority.desc()).first()
    row = AIProvider(tenant_id=tenant_id, label=body.label.strip(), kind=body.kind, base_url=base_url,
                     model=body.model.strip() or "", api_key=encrypt_secret(key),
                     priority=(last.priority + 10) if last else 10)
    if not row.model:
        from app.reasoning.llm_providers import _DEFAULT_MODELS
        row.model = _DEFAULT_MODELS.get(body.kind) or ""
    db.add(row)
    audit.record(db, "ai.provider.add", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=row.id,
                 detail={"label": row.label, "kind": row.kind})
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/providers/{provider_id}")
def remove_provider(provider_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                    authorization: Optional[str] = Header(default=None)):
    me = _owner(authorization, tenant_id)
    row = db.query(AIProvider).filter(AIProvider.id == provider_id, AIProvider.tenant_id == tenant_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    audit.record(db, "ai.provider.remove", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=provider_id,
                 detail={"label": row.label})
    db.commit()
    return {"removed": True}
