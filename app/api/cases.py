from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.business_memory import create_case_from_alert, measure_case_outcome, find_similar_cases
from app.models.alerts import Case, Alert

router = APIRouter()


class CreateCaseRequest(BaseModel):
    tenant_id: str
    alert_id: str
    decision: str   # e.g. "doing_this" | "already_handled" | "not_doing_this" — Section 37
    intervention: Optional[str] = None
    expected_outcome: Optional[str] = None
    measurement_period_days: Optional[int] = None


class CaseOut(BaseModel):
    id: str
    alert_id: Optional[str]
    problem: str
    decision: Optional[str]
    intervention: Optional[str]
    expected_outcome: Optional[str]
    actual_outcome: Optional[str]
    attribution_confidence: Optional[str]
    detected_at: datetime
    outcome_measured_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


def _get_owned_case(db: Session, case_id: str, tenant_id: str) -> Case:
    case = db.query(Case).filter(Case.id == case_id, Case.tenant_id == tenant_id).first()
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@router.post("/", response_model=CaseOut)
def create_case(payload: CreateCaseRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, payload.tenant_id)
    try:
        return create_case_from_alert(
            db, payload.tenant_id, payload.alert_id, payload.decision,
            payload.intervention, payload.expected_outcome, payload.measurement_period_days,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/", response_model=list[CaseOut])
def list_cases(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return db.query(Case).filter(Case.tenant_id == tenant_id).order_by(Case.detected_at.desc()).all()


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return _get_owned_case(db, case_id, tenant_id)


@router.post("/{case_id}/measure-outcome")
def measure_outcome(case_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    _get_owned_case(db, case_id, tenant_id)  # ownership check before the measurement logic runs
    try:
        return measure_case_outcome(db, tenant_id, case_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/similar/{alert_id}", response_model=list[CaseOut])
def similar_cases(alert_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 40: historical case retrieval for the same kind of alert (same detector and metric)."""
    authorize_tenant(authorization, tenant_id)
    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.tenant_id == tenant_id).first()
    if not alert:
        raise HTTPException(404, "Alert not found")
    return find_similar_cases(db, tenant_id, alert)
