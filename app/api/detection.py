from typing import Optional
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.detectors import run_tenant_cycle
from app.core.scheduler import _metrics_for_tenant
from app.core.detection import detect_historical_deviation

router = APIRouter()


class RunDetectionRequest(BaseModel):
    tenant_id: str
    entity: str = "sale"
    field: Literal["amount", "quantity"] = "amount"
    period_days: int = 7
    deterioration_threshold: float = 0.10


class AlertSummary(BaseModel):
    id: Optional[str]
    triggered: bool
    title: Optional[str] = None
    severity: Optional[str] = None


@router.post("/run/historical-deviation", response_model=AlertSummary)
def run_historical_deviation(payload: RunDetectionRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Manual trigger. The scheduler (app/core/scheduler.py, Sections 17-18)
    now runs this automatically on an interval for every active tenant —
    this endpoint remains for an on-demand check outside that cycle
    (e.g. right after fixing something, without waiting for the next tick).
    """
    authorize_tenant(authorization, payload.tenant_id)
    alert = detect_historical_deviation(
        db, payload.tenant_id, payload.entity, payload.field,
        payload.period_days, deterioration_threshold=payload.deterioration_threshold,
    )
    if not alert:
        return AlertSummary(id=None, triggered=False)
    return AlertSummary(id=alert.id, triggered=True, title=alert.title, severity=alert.severity.value)


class RunAllRequest(BaseModel):
    tenant_id: str


class RunAllResult(BaseModel):
    alerts: list[AlertSummary]
    post: dict
    errors: int


@router.post("/run/all", response_model=RunAllResult)
def run_all(payload: RunAllRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """On-demand full cycle for one tenant: every detector over the tenant's
    monitored metrics, data-silence, then correlation/ranking/escalation."""
    authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    result = run_tenant_cycle(db, payload.tenant_id, _metrics_for_tenant(db, payload.tenant_id))
    seen, out = set(), []
    for a in result["alerts"]:
        if a is not None and a.id not in seen:
            seen.add(a.id)
            out.append(AlertSummary(id=a.id, triggered=True, title=a.title, severity=a.severity.value))
    return RunAllResult(alerts=out, post=result["post"], errors=result["errors"])
