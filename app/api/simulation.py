from typing import Optional
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.simulation import simulate_scenario

router = APIRouter()


class ScenarioRequest(BaseModel):
    tenant_id: str
    revenue_change_pct: Optional[float] = None
    expense_change_pct: Optional[float] = None
    period_days: int = 30
    lookback_periods: int = 6


@router.post("/")
def simulate(payload: ScenarioRequest, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, payload.tenant_id)
    result = simulate_scenario(
        db, payload.tenant_id, payload.revenue_change_pct, payload.expense_change_pct,
        payload.period_days, payload.lookback_periods,
    )
    return {
        "baseline": {
            "revenue": result.baseline_revenue, "expenses": result.baseline_expenses,
            "profit": result.baseline_profit,
        },
        "scenario": {
            "revenue": result.scenario_revenue, "expenses": result.scenario_expenses,
            "profit": result.scenario_profit,
        },
        "profit_impact": result.profit_impact,
        "profit_impact_pct": result.profit_impact_pct,
        "assumptions": result.assumptions,
        "uncertainty_note": result.uncertainty_note,
    }
