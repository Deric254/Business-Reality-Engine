from typing import Optional
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.forecasting import forecast_next_period, forecast_threshold_crossing

router = APIRouter()


@router.get("/next-period")
def next_period(
    tenant_id: str, entity: str, field: Literal["amount", "quantity"],
    period_days: int = 7, lookback_periods: int = 6,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    authorize_tenant(authorization, tenant_id)
    trend = forecast_next_period(db, tenant_id, entity, field, period_days, lookback_periods)
    return {
        "period_days": trend.period_days, "historical_values": trend.historical_values,
        "slope": trend.slope, "forecast_next": trend.forecast_next, "direction": trend.direction,
    }


class ThresholdForecastRequest(BaseModel):
    tenant_id: str
    current_balance: float
    threshold: float
    lookback_days: int = 30
    max_horizon_days: int = 180


@router.post("/threshold-crossing")
def threshold_crossing(payload: ThresholdForecastRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, payload.tenant_id)
    result = forecast_threshold_crossing(
        db, payload.tenant_id, payload.current_balance, payload.threshold,
        payload.lookback_days, payload.max_horizon_days,
    )
    return {
        "current_balance": result.current_balance, "current_daily_run_rate": result.current_daily_run_rate,
        "trend_slope": result.trend_slope, "days_to_threshold": result.days_to_threshold,
        "projected_date": result.projected_date, "message": result.message,
    }
