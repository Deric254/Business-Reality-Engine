"""
Forecast deviation, risk projection and "unknown" (insufficient data) detectors
(Sections 19-20, 43). Same lifecycle rules as every detector (dedup_key, cooldown).
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import coverage as coverage_mod
from app.core.detection import _noun, METRIC_FIELDS, _find_open_alert, _sum_metric
from app.core.detectors import _upsert, _r_squared
from app.core.forecasting import _least_squares, forecast_next_period
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent

MIN_PERIODS_WITH_DATA = 4


def _period_values(db, tenant_id, entity, field, period_days, n, now):
    """Oldest -> newest totals for n consecutive complete periods ending at `now`."""
    return [_sum_metric(db, tenant_id, entity, field, now - timedelta(days=period_days * i),
                        now - timedelta(days=period_days * (i - 1))) for i in range(n, 0, -1)]


def detect_forecast_deviation(db: Session, tenant_id: str, entity: str, field: str, direction: str,
                              period_days: int = 7, threshold: float = 0.25,
                              now: Optional[datetime] = None) -> Optional[Alert]:
    """The last complete period vs what the trend of the periods BEFORE it predicted (Section 43)."""
    if field not in METRIC_FIELDS:
        raise ValueError(f"field must be one of {METRIC_FIELDS}")
    now = now or datetime.utcnow()
    vals = _period_values(db, tenant_id, entity, field, period_days, 7, now)
    history, actual = vals[:-1], vals[-1]
    if sum(1 for v in history if v > 0) < MIN_PERIODS_WITH_DATA:
        return None
    pred = forecast_next_period(db, tenant_id, entity, field, period_days, 6, now=now - timedelta(days=period_days)).forecast_next
    if pred <= 0:
        return None
    rel = (actual - pred) / pred
    bad = rel <= -threshold if direction == "drop" else rel >= threshold
    if not bad:
        return None
    sev = AlertSeverity.high if abs(rel) >= 2 * threshold else AlertSeverity.medium
    word = "below" if rel < 0 else "above"
    return _upsert(
        db, tenant_id, f"forecast_miss:{entity}:{field}:{period_days}d", now,
        {"metric": f"{entity}.{field}", "forecast": round(pred, 2), "actual": round(actual, 2),
         "history": [round(v, 2) for v in history], "period_days": period_days, "direction": direction,
         "method": "linear trend of prior periods"},
        dict(kind="forecast", severity=sev, title=f"{_noun(entity, field)} {'came in' } {abs(rel)*100:.0f}% {word} what was expected",
             what_happened=(f"The last {period_days} days of {_noun(entity, field).lower()} came to {actual:,.0f}. Your recent trend pointed to "
                            f"{pred:,.0f}, so this is {abs(rel)*100:.0f}% {word} expectation."),
             magnitude=round(rel, 4), period_start=now - timedelta(days=period_days), period_end=now,
             confidence=0.55, data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now),
             recommended_action="Compare with the forecast's assumptions: did something change this period (price, stock, staffing, a one-off)?"),
    )


LOSS_WINDOW_DAYS, LOSS_MIN_DAYS_WITH_SALES = 30, 2


def _current_loss(db: Session, tenant_id: str, now: datetime, period_days: int) -> Optional[Alert]:
    """Plain arithmetic over the last 30 days (or as many as exist): did spending exceed sales? Needs no baseline, so it
    speaks from the first days of data. It stays quiet when no sales are recorded at all, since that usually means the
    sales source is not connected, not that nothing was sold."""
    start = now - timedelta(days=LOSS_WINDOW_DAYS)
    days_with_sales = (db.query(func.count(func.distinct(func.date(BusinessEvent.timestamp))))
                       .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == "sale", BusinessEvent.validation_status == "validated",
                               BusinessEvent.amount > 0, BusinessEvent.timestamp >= start, BusinessEvent.timestamp < now).scalar())
    sold, spent = (_sum_metric(db, tenant_id, entity, "amount", start, now) for entity in ("sale", "expense"))
    key = f"risk:profit:{period_days}d"                  # the same alert the trend projection uses, so they never double up
    loss = spent - sold
    if days_with_sales < LOSS_MIN_DAYS_WITH_SALES or spent <= 0 or loss <= 0:
        open_alert = _find_open_alert(db, tenant_id, key)
        if open_alert and open_alert.evidence.get("window_days"):
            open_alert.state = AlertState.resolved; open_alert.resolved_at = now
            open_alert.evidence = {**open_alert.evidence, "recovered_at": now.isoformat()}
            db.commit()
        return None
    sev = AlertSeverity.high if spent >= 1.25 * sold else AlertSeverity.medium
    return _upsert(
        db, tenant_id, key, now,
        {"metric": "profit.net", "window_days": LOSS_WINDOW_DAYS, "sales": round(sold, 2), "expenses": round(spent, 2), "net": round(-loss, 2), "projection": False},
        dict(kind="risk", severity=sev, title="Spending exceeds sales",
             what_happened=(f"Over the last {LOSS_WINDOW_DAYS} days (or since your data began) you spent {spent:,.0f} and sold {sold:,.0f}: "
                            f"a net loss of {loss:,.0f}. This is arithmetic on your records, not a forecast."),
             magnitude=None, period_start=start, period_end=now, confidence=0.9,
             data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, "sale", now=now),
             recommended_action="Check which costs are largest and whether every sale is being recorded; if this is a launch or stocking period, compare it with your plan."),
    )


def detect_profit_risk(db: Session, tenant_id: str, period_days: int = 7, horizon_periods: int = 4,
                       now: Optional[datetime] = None) -> Optional[Alert]:
    """Risk projection: net (sales - expenses) per period; alert when the trend crosses zero within the
    horizon, or net is already negative. Clearly a projection, with its fit quality in the evidence."""
    now = now or datetime.utcnow()
    sales = _period_values(db, tenant_id, "sale", "amount", period_days, 6, now)
    exp = _period_values(db, tenant_id, "expense", "amount", period_days, 6, now)
    if sum(1 for v in sales if v > 0) < MIN_PERIODS_WITH_DATA or sum(1 for v in exp if v > 0) < MIN_PERIODS_WITH_DATA:
        return _current_loss(db, tenant_id, now, period_days)       # too little history for a trend, but a loss needs no baseline
    net = [s - e for s, e in zip(sales, exp)]
    xs = list(range(len(net)))
    slope, intercept = _least_squares(xs, net)
    fit = _r_squared(xs, net)
    current = net[-1]
    crossing = None
    if current >= 0 and slope < 0:
        crossing = int((-(intercept + slope * (len(net) - 1))) / slope) if slope else None
        crossing = None if crossing is None or crossing < 0 else crossing + 1
    already = current < 0
    if not already and not (crossing is not None and crossing <= horizon_periods and fit >= 0.5):
        return None
    sev = AlertSeverity.high if already else AlertSeverity.medium
    msg = (f"Net result (sales minus expenses) was negative last period ({current:,.0f})." if already else
           f"At the current trend, net result may turn negative in about {crossing} period(s) of {period_days} days.")
    return _upsert(
        db, tenant_id, f"risk:profit:{period_days}d", now,
        {"metric": "profit.net", "net_by_period": [round(v, 2) for v in net], "slope_per_period": round(slope, 2),
         "trend_fit_r2": round(fit, 2), "periods_to_zero": crossing, "projection": True},
        dict(kind="risk", severity=sev, title="Profit at risk" if not already else "Spending exceeds sales",
             what_happened=msg + " This is a projection from past periods, not a certainty.",
             magnitude=None, period_start=now - timedelta(days=period_days * 6), period_end=now,
             confidence=round(min(0.7, 0.35 + fit * 0.35), 2),
             data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, "sale", now=now),
             recommended_action="Look at which expense categories grew and whether sales are softening; decide before the projected date."),
    )


def detect_insufficient_data(db: Session, tenant_id: str, entity: str, field: str, period_days: int,
                             baseline_periods: int, now: Optional[datetime] = None) -> Optional[Alert]:
    """Section 20 'unknown': a monitored metric we cannot judge yet says so instead of staying silent.
    Closes itself once enough history exists."""
    now = now or datetime.utcnow()
    key = f"unknown:{entity}:{field}:{period_days}d"
    need = max(MIN_PERIODS_WITH_DATA, baseline_periods + 1)
    have = sum(1 for v in _period_values(db, tenant_id, entity, field, period_days, need, now) if v > 0)
    open_alert = _find_open_alert(db, tenant_id, key)
    any_data = db.query(BusinessEvent.id).filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity).first()
    if have >= need or not any_data:
        if open_alert:
            open_alert.state = AlertState.resolved; open_alert.resolved_at = now
            open_alert.evidence = {**open_alert.evidence, "recovered_at": now.isoformat()}
            db.commit()
        return None
    return _upsert(
        db, tenant_id, key, now,
        {"metric": f"{entity}.{field}", "periods_with_data": have, "periods_needed": need, "period_days": period_days},
        dict(kind="unknown", severity=AlertSeverity.informational, title=f"Not enough history to monitor {entity} {field}",
             what_happened=(f"Only {have} of the last {need} {period_days}-day periods have {entity} data, so changes in {entity} "
                            f"{field} cannot be judged reliably yet. No alert about it should be read as 'all is well'."),
             confidence=1.0, data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now),
             recommended_action="Keep the data source connected, or import earlier history so monitoring can start."),
    )


def note_rebaseline(db: Session, tenant_id: str, entity: str, field: str, period_days: int, change, now: datetime) -> Optional[Alert]:
    """While a declared change is being re-baselined the metric is not judged; say so instead of staying silent (Section 53)."""
    return _upsert(
        db, tenant_id, f"unknown:{entity}:{field}:{period_days}d", now,
        {"metric": f"{entity}.{field}", "rebaselining": True, "model_version": change.version,
         "change": change.description or change.subject, "effective_at": change.effective_at.isoformat(), "period_days": period_days},
        dict(kind="unknown", severity=AlertSeverity.informational, title=f"Re-baselining {entity} {field} after a business change",
             what_happened=(f"Monitoring of {entity} {field} is paused for comparisons while new history builds after: "
                            f"{change.description or change.subject} (model version {change.version}). It resumes automatically."),
             confidence=1.0, data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now),
             recommended_action="No action needed unless the change was not expected; you can clear the re-baseline in Settings."),
    )
