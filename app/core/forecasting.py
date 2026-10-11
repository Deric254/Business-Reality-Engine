"""
Forecasting (Section 43): "If this continues, what happens next?"

Deliberately simple and auditable — ordinary least-squares linear
regression, implemented by hand (no numpy/scipy dependency) rather than
a black-box model. A business manager needs to be able to trust WHY a
forecast says what it says; a linear trend on recent history is
something you can sanity-check by eye against the raw numbers, which
matters more here than marginal accuracy gains from a fancier model.

Two forecasts:
1. forecast_next_period — projects the next period's value for any
   metric from its recent period-over-period trend.
2. forecast_threshold_crossing — Section 43's own worked example: given
   a current balance and a recent net cashflow trend, project days until
   a safety threshold is crossed. Returns None if the trend isn't headed
   toward the threshold at all — never fabricates a countdown that isn't real.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.core.detection import _sum_metric


def _least_squares(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Returns (slope, intercept) for y = slope*x + intercept. No external deps."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0, mean_y
    slope = num / den
    intercept = mean_y - slope * mean_x
    return slope, intercept


@dataclass
class PeriodTrend:
    period_days: int
    historical_values: list[float]   # oldest to newest
    slope: float                      # change in value per period
    forecast_next: float
    direction: str                    # "up" | "down" | "flat"


def forecast_next_period(
    db: Session, tenant_id: str, entity: str, field: str,
    period_days: int = 7, lookback_periods: int = 6, now: Optional[datetime] = None,
) -> PeriodTrend:
    now = now or datetime.utcnow()
    values = []
    for i in range(lookback_periods, 0, -1):
        end = now - timedelta(days=period_days * (i - 1))
        start = now - timedelta(days=period_days * i)
        values.append(_sum_metric(db, tenant_id, entity, field, start, end))

    xs = list(range(len(values)))
    slope, intercept = _least_squares(xs, values)
    forecast_next = intercept + slope * len(values)  # project one period beyond the last

    magnitude = abs(slope)
    baseline = sum(values) / len(values) if values else 0
    direction = "flat"
    if baseline > 0 and magnitude / baseline > 0.02:  # >2% of average per period is a real trend, not noise
        direction = "up" if slope > 0 else "down"

    return PeriodTrend(
        period_days=period_days, historical_values=values, slope=round(slope, 2),
        forecast_next=round(max(forecast_next, 0), 2), direction=direction,
    )


@dataclass
class ThresholdForecast:
    current_balance: float
    current_daily_run_rate: float
    trend_slope: float
    days_to_threshold: Optional[int]
    projected_date: Optional[str]
    message: str


def forecast_threshold_crossing(
    db: Session, tenant_id: str, current_balance: float, threshold: float,
    lookback_days: int = 30, max_horizon_days: int = 180, now: Optional[datetime] = None,
) -> ThresholdForecast:
    """
    Section 43's own example: "At the current expense trajectory, cash
    reserves may fall below the business's defined safety threshold
    within N days." current_balance is supplied by the caller (the
    business's actual current cash figure) — this engine doesn't have
    a notion of an absolute balance on its own, only transaction flow.
    """
    now = now or datetime.utcnow()

    daily_net = []
    for i in range(lookback_days, 0, -1):
        day_end = now - timedelta(days=i - 1)
        day_start = now - timedelta(days=i)
        revenue = _sum_metric(db, tenant_id, "sale", "amount", day_start, day_end)
        expense = _sum_metric(db, tenant_id, "expense", "amount", day_start, day_end)
        daily_net.append(revenue - expense)

    xs = list(range(len(daily_net)))
    slope, intercept = _least_squares(xs, daily_net)
    current_run_rate = intercept + slope * (len(daily_net) - 1)

    heading_toward_threshold = (
        (current_balance > threshold and slope < 0) or
        (current_balance < threshold and slope > 0)
    )
    if not heading_toward_threshold:
        return ThresholdForecast(
            current_balance=current_balance, current_daily_run_rate=round(current_run_rate, 2),
            trend_slope=round(slope, 2), days_to_threshold=None, projected_date=None,
            message="Current trend is not heading toward the threshold — no near-term risk projected.",
        )

    balance = current_balance
    for day in range(1, max_horizon_days + 1):
        projected_daily_net = intercept + slope * (len(daily_net) - 1 + day)
        balance += projected_daily_net
        crossed = (balance <= threshold) if current_balance > threshold else (balance >= threshold)
        if crossed:
            projected_date = (now + timedelta(days=day)).date().isoformat()
            return ThresholdForecast(
                current_balance=current_balance, current_daily_run_rate=round(current_run_rate, 2),
                trend_slope=round(slope, 2), days_to_threshold=day, projected_date=projected_date,
                message=f"At the current trend, the threshold of {threshold:.2f} may be crossed "
                        f"within {day} days (around {projected_date}).",
            )

    return ThresholdForecast(
        current_balance=current_balance, current_daily_run_rate=round(current_run_rate, 2),
        trend_slope=round(slope, 2), days_to_threshold=None, projected_date=None,
        message=f"Trending toward the threshold but not projected to cross it within {max_horizon_days} days.",
    )
