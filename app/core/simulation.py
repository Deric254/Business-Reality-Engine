"""
Scenario Simulation (Section 44): "What happens if we increase price 5%?"

Built directly on the forecasting engine (Section 43) — the "if nothing
changes" baseline IS the next-period forecast. A scenario just overlays a
hypothetical percentage change on top of that baseline, on the revenue
and/or expense side.

Section 44 is explicit: "This should show assumptions and uncertainty."
That's not a nice-to-have here — every simulation result carries an
explicit assumptions list, because the biggest source of error in a
scenario like "raise price 5%" isn't the arithmetic, it's the unstated
assumption that transaction volume won't change. No demand elasticity is
modeled; that assumption is stated in the output, not buried.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.core.forecasting import forecast_next_period


@dataclass
class ScenarioResult:
    baseline_revenue: float
    baseline_expenses: float
    baseline_profit: float
    scenario_revenue: float
    scenario_expenses: float
    scenario_profit: float
    profit_impact: float
    profit_impact_pct: Optional[float]
    assumptions: list[str] = field(default_factory=list)
    uncertainty_note: str = ""


def simulate_scenario(
    db: Session, tenant_id: str,
    revenue_change_pct: Optional[float] = None,
    expense_change_pct: Optional[float] = None,
    period_days: int = 30, lookback_periods: int = 6,
    now: Optional[datetime] = None,
) -> ScenarioResult:
    """
    revenue_change_pct: e.g. 5.0 for "raise prices 5%" (applied to the
    revenue forecast, assuming volume unchanged — stated explicitly below).
    expense_change_pct: e.g. 10.0 for "what if expenses increase 10%".
    Both are optional and independent — either or both can be set.
    """
    revenue_trend = forecast_next_period(db, tenant_id, "sale", "amount", period_days, lookback_periods, now)
    expense_trend = forecast_next_period(db, tenant_id, "expense", "amount", period_days, lookback_periods, now)

    baseline_revenue = revenue_trend.forecast_next
    baseline_expenses = expense_trend.forecast_next
    baseline_profit = baseline_revenue - baseline_expenses

    scenario_revenue = baseline_revenue
    scenario_expenses = baseline_expenses
    assumptions = [
        f"Baseline is a linear projection from the last {lookback_periods} periods "
        f"({period_days} days each) — not a guarantee, a trend extrapolation.",
    ]

    if revenue_change_pct is not None:
        scenario_revenue = baseline_revenue * (1 + revenue_change_pct / 100)
        assumptions.append(
            f"Revenue change of {revenue_change_pct:+.1f}% is applied directly to the revenue "
            f"baseline. This assumes transaction VOLUME is unchanged — no demand elasticity is "
            f"modeled. A real price increase often reduces volume to some degree; this simulation "
            f"does not estimate that effect."
        )
    if expense_change_pct is not None:
        scenario_expenses = baseline_expenses * (1 + expense_change_pct / 100)
        assumptions.append(
            f"Expense change of {expense_change_pct:+.1f}% is applied directly to the expense baseline."
        )

    scenario_profit = scenario_revenue - scenario_expenses
    profit_impact = scenario_profit - baseline_profit
    profit_impact_pct = (profit_impact / abs(baseline_profit)) * 100 if baseline_profit else None

    uncertainty_note = (
        "This is a projection built on a linear trend and a stated hypothetical change — "
        "not a prediction. Real outcomes depend on factors this simulation does not model "
        "(seasonality, competitor actions, demand response to price, external events)."
    )

    return ScenarioResult(
        baseline_revenue=round(baseline_revenue, 2), baseline_expenses=round(baseline_expenses, 2),
        baseline_profit=round(baseline_profit, 2),
        scenario_revenue=round(scenario_revenue, 2), scenario_expenses=round(scenario_expenses, 2),
        scenario_profit=round(scenario_profit, 2),
        profit_impact=round(profit_impact, 2),
        profit_impact_pct=round(profit_impact_pct, 1) if profit_impact_pct is not None else None,
        assumptions=assumptions, uncertainty_note=uncertainty_note,
    )
