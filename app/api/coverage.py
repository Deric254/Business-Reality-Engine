from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.coverage import compute_and_upsert_coverage

router = APIRouter()


class CoverageOut(BaseModel):
    area: str
    status: str
    last_data_at: Optional[datetime]
    checked_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/", response_model=list[CoverageOut])
def get_coverage(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Section 15: Data Coverage Map. Recomputes on every call for now — cheap
    at current scale, would move to a scheduled job (Section 51) once
    connector counts grow.
    """
    authorize_tenant(authorization, tenant_id)
    rows = compute_and_upsert_coverage(db, tenant_id)
    return rows
