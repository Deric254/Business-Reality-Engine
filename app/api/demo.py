"""Sample data for a business that has none yet: pick a sample, see the app working, remove it when real data arrives."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import audit, demo
from app.core.auth_users import authorize_tenant
from app.core.db import get_db

router = APIRouter()


class LoadIn(BaseModel):
    tenant_id: str
    kind: str = "duka"


@router.get("/samples")
def list_samples(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return {"samples": [{"id": k.id, "title": k.title, "blurb": k.blurb} for k in demo.KINDS], "loaded": demo.has_sample(db, tenant_id)}


@router.post("/samples")
def load_sample(body: LoadIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, body.tenant_id, require_role="owner")
    result = demo.load_sample(db, body.tenant_id, body.kind)
    audit.record(db, "demo.loaded", request=request, tenant_id=body.tenant_id, actor_user_id=me.user_id, detail={"kind": result["kind"]})
    return result


@router.delete("/samples")
def remove_sample(tenant_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    result = demo.remove_sample(db, tenant_id)
    audit.record(db, "demo.removed", request=request, tenant_id=tenant_id, actor_user_id=me.user_id)
    return result
