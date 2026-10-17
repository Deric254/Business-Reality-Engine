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
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.detection import _sum_metric
from app.core.tenant_time import local_midnight_utc, tenant_zone
from app.models.core import BusinessEvent


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


LOOKBACK_DAYS, MIN_DAYS, MIN_ACTIVE_DAYS = 84, 28, 12   # twelve weeks at most; four to see a weekly pattern, on 12+ days with sales
_Z80 = 1.2816                               # a band that about eight in ten outcomes fall inside, if the pattern holds
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class DayForecast:
    day: date
    weekday: str
    expected: float
    low: float
    high: float


@dataclass
class SeasonalForecast:
    horizon_days: int
    days: list[DayForecast]
    total: float
    low: float
    high: float
    weeks_of_history: int
    busiest: str
    quietest: str
    trend_pct: float                        # change in the seasonally adjusted level across the history


def _daily_totals(db: Session, tenant_id: str, entity: str, first: date, last: date, tz) -> dict[date, float]:
    local = func.timezone(tz.key, func.timezone("UTC", BusinessEvent.timestamp))
    rows = (db.query(func.date(local), func.sum(BusinessEvent.amount))
            .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity, BusinessEvent.validation_status == "validated",
                    BusinessEvent.amount.isnot(None), BusinessEvent.timestamp >= local_midnight_utc(first, tz),
                    BusinessEvent.timestamp < local_midnight_utc(last + timedelta(days=1), tz))
            .group_by(func.date(local)).all())
    return {d: float(v) for d, v in rows}


def forecast_days(db: Session, tenant_id: str, entity: str, horizon_days: int = 7, now: Optional[datetime] = None) -> Optional[SeasonalForecast]:
    """The next `horizon_days` days (from tomorrow), day by day, using the business's own weekly pattern: how much
    stronger a Saturday is than a Tuesday, a straight-line trend on what is left once that pattern is removed, and a
    range from how far past days missed it. Closed weekdays stay at zero. None when there is less than four weeks of
    history, or too few days with records to show a pattern (weekly totals are not a daily rhythm): nothing is invented."""
    now = now or datetime.utcnow()
    tz = tenant_zone(db, tenant_id)
    today = now.replace(tzinfo=timezone.utc).astimezone(tz).date()
    yesterday = today - timedelta(days=1)                       # today is still incomplete, so it is not history
    totals = _daily_totals(db, tenant_id, entity, today - timedelta(days=LOOKBACK_DAYS), yesterday, tz)
    if len(totals) < MIN_ACTIVE_DAYS or (yesterday - min(totals)).days + 1 < MIN_DAYS:
        return None
    days = [min(totals) + timedelta(days=i) for i in range((yesterday - min(totals)).days + 1)]
    values = [totals.get(d, 0.0) for d in days]
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    by_weekday = defaultdict(list)
    for d, v in zip(days, values):
        by_weekday[d.weekday()].append(v)
    factor = {w: (sum(vs) / len(vs)) / mean for w, vs in by_weekday.items()}
    adjusted = [(i, v / factor[d.weekday()]) for i, (d, v) in enumerate(zip(days, values)) if factor[d.weekday()] > 0]
    slope, intercept = _least_squares([float(i) for i, _ in adjusted], [y for _, y in adjusted])
    recent = [y for _, y in adjusted[-28:]]
    recent_level, last_i = sum(recent) / len(recent), len(days) - 1
    level = lambda k: min(max(intercept + slope * (last_i + k), 0.6 * recent_level), 1.4 * recent_level)   # no runaway trends
    residuals = [v - (intercept + slope * i) * factor[d.weekday()] for i, (d, v) in enumerate(zip(days, values))]
    sigma = math.sqrt(sum(r * r for r in residuals) / max(1, len(residuals) - 2))
    out = []
    for j in range(1, horizon_days + 1):
        day = today + timedelta(days=j)
        expected = level((day - yesterday).days) * factor[day.weekday()]
        out.append(DayForecast(day, _WEEKDAYS[day.weekday()], round(expected, 2), round(max(0.0, expected - _Z80 * sigma), 2), round(expected + _Z80 * sigma, 2)))
    total, spread = sum(d.expected for d in out), _Z80 * sigma * math.sqrt(horizon_days)
    strongest, weakest = max(factor, key=factor.get), min(factor, key=factor.get)
    return SeasonalForecast(
        horizon_days, out, round(total, 2), round(max(0.0, total - spread), 2), round(total + spread, 2), len(days) // 7,
        _WEEKDAYS[strongest], _WEEKDAYS[weakest], round((slope * last_i) / intercept * 100, 1) if intercept > 0 else 0.0)


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
