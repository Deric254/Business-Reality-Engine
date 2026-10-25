import os
from typing import Optional
from fastapi.encoders import jsonable_encoder
from app.core.cache import cached
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.business_questions import answer_question, compute_profit_breakdown

router = APIRouter()

KPI_CACHE_TTL_SECONDS = int(os.environ.get("KPI_CACHE_TTL_SECONDS", "30"))


ASK_CACHE_SECONDS = int(os.environ.get("ASK_CACHE_SECONDS", "120"))


class AskRequest(BaseModel):
    tenant_id: str
    question: str = Field(min_length=1, max_length=500)
    period_days: int = Field(default=30, ge=0, le=3650)


@router.post("/ask")
def ask(payload: AskRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, payload.tenant_id)
    # The same question asked again within two minutes (an example chip, a double click, a refresh) is answered at once.
    # Only real answers are kept, so a moment's outage is never remembered; new data drops the tenant's entries.
    import hashlib
    q = " ".join(payload.question.lower().split())
    key = f"ask:{payload.tenant_id}:{payload.period_days}:{hashlib.sha256(q.encode()).hexdigest()[:20]}"
    return cached(key, ASK_CACHE_SECONDS, lambda: answer_question(db, payload.tenant_id, payload.question, payload.period_days),
                  store_if=lambda r: bool(r.get("answered")))


@router.get("/kpis")
def kpis(tenant_id: str, period_days: int = Query(default=30, ge=0, le=3650), db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Deterministic-only breakdown — deliberately does NOT call the LLM.
    /ask is for a person typing a real question (worth an LLM call);
    a dashboard polling on every page load is not — that would burn
    tokens on routine traffic, the exact thing Section 54 cost control
    exists to prevent.
    """
    authorize_tenant(authorization, tenant_id)
    # Cached briefly: dashboards poll this, the answer only changes as data lands. The key carries the business's
    # current date, so a cached answer can never be served across midnight into a different period.
    from app.core import periods
    day = periods.local_today(db, tenant_id).isoformat()
    return cached(
        f"kpis:{tenant_id}:{period_days}:{day}", KPI_CACHE_TTL_SECONDS,
        lambda: jsonable_encoder(compute_profit_breakdown(db, tenant_id, period_days=period_days)),
    )
