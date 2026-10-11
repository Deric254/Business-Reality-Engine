from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from app.core import audit
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.entity_resolution import resolve_entity, merge_entities
from app.models.core import ResolvedEntity

router = APIRouter()


class ResolveRequest(BaseModel):
    tenant_id: str
    entity_type: str   # customer | product | supplier | employee | branch | asset
    source: str
    raw_value: str
    attributes: dict = {}


class ResolveResponse(BaseModel):
    resolved_entity_id: str
    confidence: float
    matched_existing: bool
    needs_review: bool
    suggestions: list[dict]


class MergeRequest(BaseModel):
    tenant_id: str
    keep_id: str
    merge_id: str


class EntityOut(BaseModel):
    id: str
    entity_type: str
    canonical_name: str
    confidence: float

    class Config:
        from_attributes = True


@router.post("/resolve", response_model=ResolveResponse)
def resolve(payload: ResolveRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, payload.tenant_id)
    try:
        result = resolve_entity(
            db, payload.tenant_id, payload.entity_type, payload.source,
            payload.raw_value, payload.attributes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ResolveResponse(**result.__dict__)


@router.get("/", response_model=list[EntityOut])
def list_entities(tenant_id: str, entity_type: Optional[str] = None, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    q = db.query(ResolvedEntity).filter(ResolvedEntity.tenant_id == tenant_id)
    if entity_type:
        q = q.filter(ResolvedEntity.entity_type == entity_type)
    return q.all()


@router.post("/merge", response_model=EntityOut)
def merge(payload: MergeRequest, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Human-confirmed merge only — never triggered automatically. No role
    restriction: correcting entity resolution mistakes isn't a
    credential-holding action, staff can do it same as owner.
    """
    me = authorize_tenant(authorization, payload.tenant_id)
    try:
        out = merge_entities(db, payload.tenant_id, payload.keep_id, payload.merge_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(db, "entity.merge", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id,
                 target=payload.keep_id, detail={"merged": payload.merge_id})
    return out
