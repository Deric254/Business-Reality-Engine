"""Which metrics the scheduler watches for a tenant (Section 50)."""
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.models.config import MonitoredMetric

router = APIRouter()
MAX_METRICS_PER_TENANT = 50


class MetricCreate(BaseModel):
    tenant_id: str
    entity: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    field: Literal["amount", "quantity"] = "amount"
    period_days: int = Field(default=7, ge=1, le=90)
    baseline_periods: int = Field(default=4, ge=1, le=12)
    threshold_pct: Decimal = Field(default=Decimal("10"), gt=0, le=100)
    direction: Literal["drop", "rise"] = "drop"
    absolute_threshold: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("1e13"))


class MetricUpdate(BaseModel):
    enabled: Optional[bool] = None
    threshold_pct: Optional[Decimal] = Field(default=None, gt=0, le=100)
    baseline_periods: Optional[int] = Field(default=None, ge=1, le=12)
    direction: Optional[Literal["drop", "rise"]] = None
    absolute_threshold: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("1e13"))


class MetricOut(BaseModel):
    id: str
    entity: str
    field: str
    period_days: int
    baseline_periods: int
    threshold_pct: Decimal
    direction: str
    absolute_threshold: Optional[Decimal] = None
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


@router.get("/metrics", response_model=list[MetricOut])
def list_metrics(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return db.query(MonitoredMetric).filter_by(tenant_id=tenant_id).order_by(MonitoredMetric.created_at).all()


@router.post("/metrics", response_model=MetricOut)
def create_metric(payload: MetricCreate, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if db.query(MonitoredMetric).filter_by(tenant_id=payload.tenant_id).count() >= MAX_METRICS_PER_TENANT:
        raise HTTPException(409, f"At most {MAX_METRICS_PER_TENANT} monitored metrics per business")
    m = MonitoredMetric(**payload.model_dump())
    db.add(m)
    db.commit()   # a duplicate (tenant, entity, field, period) hits the unique constraint -> 409 via the global handler
    db.refresh(m)
    audit.record(db, "metric.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id, target=m.id,
                 detail={"entity": m.entity, "field": m.field, "direction": m.direction})
    return m


def _owned(db, metric_id, tenant_id) -> MonitoredMetric:
    m = db.query(MonitoredMetric).filter_by(id=metric_id, tenant_id=tenant_id).first()
    if not m:
        raise HTTPException(404, "Metric not found")
    return m


@router.patch("/metrics/{metric_id}", response_model=MetricOut)
def update_metric(metric_id: str, tenant_id: str, payload: MetricUpdate, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    m = _owned(db, metric_id, tenant_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit(); db.refresh(m)
    audit.record(db, "metric.update", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=metric_id,
                 detail=payload.model_dump(exclude_unset=True, mode="json"))
    return m


@router.delete("/metrics/{metric_id}")
def delete_metric(metric_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    db.delete(_owned(db, metric_id, tenant_id)); db.commit()
    audit.record(db, "metric.delete", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=metric_id)
    return {"deleted": True}
