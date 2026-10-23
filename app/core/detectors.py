"""
Additional detectors (Sections 19-20) and the per-tenant detection cycle.

Every detector shares the alert lifecycle rules (Section 25): an open alert for
the same dedup_key is UPDATED, never duplicated; a recently closed one keeps
the condition quiet (cooldown). "Bad direction" is per metric: a revenue-like
metric is bad when it falls, a cost-like one when it rises; a move the good way
is reported as an opportunity.

  historical deviation  - detection.detect_historical_deviation (period vs baseline)
  opportunity           - good-direction move past 2x the threshold
  sustained trend       - several periods in a row moving the bad way (OLS fit)
  daily anomaly         - yesterday vs the same weekday in prior weeks (weekday
                          seasonality; day boundaries in the tenant's timezone)
  threshold             - fixed line on the period total
  data silence          - a connector that used to send data has stopped ("unknown")
"""
import logging
import statistics
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from app.core.tenant_time import start_of_local_day, tenant_zone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import coverage as coverage_mod
from app.core.alert_engine import post_process_tenant
from app.core.business_memory import measure_due_cases
from app.core.detection import (
    _headline, _noun, close_if_cleared, compare_headline, comparison_text, money_formatter, resolve_alert,
    METRIC_FIELDS, _find_open_alert, _in_cooldown, _sum_metric,
    detect_historical_deviation, detect_threshold_breach, explain_deviation,
)
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent, Connector

logger = logging.getLogger("business_reality_engine")

DEFAULT_DIRECTION = {"expense": "rise"}   # everything else is revenue-like: bad when it falls


def default_direction(entity: str) -> str:
    return DEFAULT_DIRECTION.get(entity, "drop")


def _upsert(db: Session, tenant_id: str, dedup_key: str, now: datetime, evidence: dict, fields: dict) -> Optional[Alert]:
    """Update the open alert for dedup_key, else create one unless in cooldown."""
    evidence = {**evidence, "dedup_key": dedup_key}
    existing = _find_open_alert(db, tenant_id, dedup_key)
    if existing:
        keep = {k: v for k, v in (existing.evidence or {}).items() if k in ("escalated_at",)}
        existing.evidence = {**evidence, **keep}
        for k, v in fields.items():
            if k not in ("title", "state"):
                setattr(existing, k, v)
        db.commit(); db.refresh(existing)
        return existing
    if _in_cooldown(db, tenant_id, dedup_key, now):
        return None
    alert = Alert(tenant_id=tenant_id, state=AlertState.detected, evidence=evidence, **fields)
    db.add(alert); db.commit(); db.refresh(alert)
    return alert


def _ago(hours: float) -> str:
    return f"{hours:.0f} hours ago" if hours < 48 else f"{hours / 24:.0f} days ago"


def _period_phrase(period_days: int) -> str:
    return "week" if period_days == 7 else f"{period_days}-day period"


# ---------------- opportunity ----------------

def detect_opportunity(
    db: Session, tenant_id: str, entity: str = "sale", field: str = "amount", direction: str = "drop",
    period_days: int = 7, baseline_periods: int = 4, min_change: float = 0.20, now: Optional[datetime] = None,
) -> Optional[Alert]:
    now = now or datetime.utcnow()
    cur_start = now - timedelta(days=period_days)
    current = _sum_metric(db, tenant_id, entity, field, cur_start, now)
    base = [
        _sum_metric(db, tenant_id, entity, field, now - timedelta(days=period_days * (i + 1)), now - timedelta(days=period_days * i))
        for i in range(1, baseline_periods + 1)
    ]
    base = [v for v in base if v > 0]
    if not base:
        return None
    avg = sum(base) / len(base)
    change = (current - avg) / avg
    good = change if direction == "drop" else -change      # positive = moved the GOOD way
    key = f"opportunity:{entity}:{field}:{period_days}d"
    if good < min_change:
        if good < min_change / 2:
            close_if_cleared(db, tenant_id, key, now)
        return None
    rising = change > 0
    related, drivers = explain_deviation(
        db, tenant_id, entity, cur_start, now, now - timedelta(days=period_days * 2), now - timedelta(days=period_days),
        rising=rising,
    )
    return _upsert(
        db, tenant_id, key, now,
        {"metric": f"{entity}.{field}", "current": current, "baseline": round(avg, 2),
         "change_pct": round(change * 100, 2), "period_days": period_days, "direction": direction},
        dict(
            severity=AlertSeverity.opportunity, title=_headline(entity, field, change, period_days),
            what_happened="Good news. " + comparison_text(entity, field, current, avg, period_days, len(base), money_formatter(db, tenant_id, field)),
            magnitude=round(change, 4), period_start=cur_start, period_end=now,
            related_variables=related, possible_explanations=drivers, confidence=0.7 if len(base) >= 2 else 0.4,
            recommended_action="Find out what caused this (a promotion, a new customer, a price change) so you can repeat it.",
        ),
    )


# ---------------- sustained trend ----------------

def _r_squared(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return (sxy * sxy) / (sxx * syy)


def detect_sustained_trend(
    db: Session, tenant_id: str, entity: str = "sale", field: str = "amount", direction: str = "drop",
    period_days: int = 7, lookback_periods: int = 6, min_total_change: float = 0.15, min_r2: float = 0.7,
    now: Optional[datetime] = None,
) -> Optional[Alert]:
    """A steady slide (or climb, for costs) across several periods — slow erosion that a
    single period-vs-baseline comparison can miss."""
    from app.core.forecasting import forecast_next_period
    now = now or datetime.utcnow()
    trend = forecast_next_period(db, tenant_id, entity, field, period_days, lookback_periods, now)
    vals = trend.historical_values
    if len(vals) < 4 or vals[0] <= 0 or vals[-1] <= 0 or sum(1 for v in vals if v > 0) < len(vals) - 1:
        return None
    xs = list(range(len(vals)))
    r2 = _r_squared(xs, vals)
    total = (vals[-1] - vals[0]) / vals[0]
    bad = -total if direction == "drop" else total
    steps_bad = sum(1 for a, b in zip(vals, vals[1:]) if (b < a if direction == "drop" else b > a))
    key = f"trend:{entity}:{field}:{period_days}d"
    if bad < min_total_change or r2 < min_r2 or steps_bad < len(vals) - 2:
        if bad < min_total_change / 2:
            close_if_cleared(db, tenant_id, key, now)
        return None
    verb = "fallen" if direction == "drop" else "risen"
    sev = AlertSeverity.high if bad >= 0.40 else AlertSeverity.medium if bad >= 0.25 else AlertSeverity.informational
    noun = _noun(entity, field)
    unit = _period_phrase(period_days)
    have = "has" if noun == "Spending" else "have"
    fmt = money_formatter(db, tenant_id, field)
    return _upsert(
        db, tenant_id, key, now,
        {"metric": f"{entity}.{field}", "period_days": period_days, "values": [round(v, 2) for v in vals],
         "slope_per_period": trend.slope, "r_squared": round(r2, 3), "forecast_next": trend.forecast_next, "direction": direction},
        dict(
            severity=sev, title=f"{noun} {have} {verb} steadily over the last {len(vals)} {unit}s",
            what_happened=(f"{noun} {have} {verb} {abs(total)*100:.0f}% over the last {len(vals)} {unit}s, from {fmt(vals[0])} to {fmt(vals[-1])}, "
                           f"and the move has been steady rather than a one-off. If it continues, the next {unit} would come to about {fmt(trend.forecast_next)}."),
            magnitude=round(total, 4), period_start=now - timedelta(days=period_days * len(vals)), period_end=now,
            confidence=round(min(0.9, 0.5 + r2 * 0.4), 2), data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now),
            recommended_action=f"A slow change rarely fixes itself. Look at what changed in your {_noun(entity, field).lower()} around the start of this {'slide' if direction == 'drop' else 'climb'}: prices, suppliers, staff, stock or customers.",
        ),
    )


# ---------------- daily anomaly (weekday seasonality) ----------------

def detect_daily_anomaly(
    db: Session, tenant_id: str, entity: str = "sale", field: str = "amount", direction: str = "drop",
    lookback_weeks: int = 8, min_z: float = 3.0, min_rel: float = 0.25, now: Optional[datetime] = None,
) -> Optional[Alert]:
    """Yesterday (a full day in the BUSINESS's timezone) vs the same weekday in prior weeks.
    Robust statistics (median/MAD) so one odd past day doesn't hide or fake an anomaly."""
    if field not in METRIC_FIELDS:
        raise ValueError(f"field must be one of {METRIC_FIELDS}")
    now = now or datetime.utcnow()
    tz = tenant_zone(db, tenant_id)
    utc = ZoneInfo("UTC")
    local_today = now.replace(tzinfo=utc).astimezone(tz).date()
    target = local_today - timedelta(days=1)
    days = [target - timedelta(days=7 * k) for k in range(0, lookback_weeks + 1)]

    def utc_bounds(d):
        start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(utc).replace(tzinfo=None)
        end = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(utc).replace(tzinfo=None) + timedelta(hours=24)
        return start, end

    col = getattr(BusinessEvent, field)
    totals = {}
    for d in days:      # <= 9 small indexed range queries; exact local-day boundaries incl. DST
        s, e = utc_bounds(d)
        totals[d] = float(
            db.query(func.coalesce(func.sum(col), 0))
            .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity,
                    BusinessEvent.timestamp >= s, BusinessEvent.timestamp < e, BusinessEvent.validation_status == "validated")
            .scalar() or 0
        )
    history = [totals[d] for d in days[1:]]
    if sum(1 for v in history if v > 0) < 4:
        return None                           # not enough same-weekday history to judge
    med = statistics.median(history)
    mad = statistics.median([abs(v - med) for v in history])
    sigma = max(1.4826 * mad, 0.10 * med)     # floor: never divide by ~0 on very regular data
    x = totals[target]
    z = (x - med) / sigma
    rel = (x - med) / med if med else 0.0
    bad = (z <= -min_z and rel <= -min_rel) if direction == "drop" else (z >= min_z and rel >= min_rel)
    key = f"daily_anomaly:{entity}:{field}"
    if not bad:
        close_if_cleared(db, tenant_id, key, now)       # a day-specific alert: the next normal day ends it
        return None
    sev = AlertSeverity.high if abs(rel) >= 0.60 else AlertSeverity.medium
    from app.core.context import is_holiday
    holiday = is_holiday(db, tenant_id, target)
    if holiday:                                   # Section 42: a public holiday explains an off day; do not raise an incident
        sev = AlertSeverity.informational
    fmt = money_formatter(db, tenant_id, field)
    return _upsert(
        db, tenant_id, key, now,
        {"metric": f"{entity}.{field}", "day": target.isoformat(), "weekday": target.strftime("%A"), "value": x,
         "typical_for_weekday": round(med, 2), "robust_z": round(z, 2), "history": [round(v, 2) for v in history],
         "timezone": str(tz), "direction": direction, **({"holiday": holiday} if holiday else {})},
        dict(
            severity=sev, title=compare_headline(_noun(entity, field), rel, f"a usual {target.strftime('%A')}", past=True),
            what_happened=(f"On {target.strftime('%A')} {target.isoformat()} {_noun(entity, field).lower()} came to {fmt(x)}. "
                           f"A usual {target.strftime('%A')} is about {fmt(med)} (the middle value of the last {len(history)} {target.strftime('%A')}s)."
                           f"{' This was a public holiday, which may explain it.' if holiday else ''}"),
            magnitude=round(rel, 4), period_start=utc_bounds(target)[0], period_end=utc_bounds(target)[1],
            confidence=0.6, kind="daily", data_coverage_pct=coverage_mod.coverage_pct_for_entity(db, tenant_id, entity, now=now),
            recommended_action="Think back to that day: were you closed, out of stock, short of staff, or were some records not entered?",
        ),
    )


# ---------------- data silence ----------------

def detect_data_silence(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[Alert]:
    """A connector that HAS sent data but has gone quiet past its own expected frequency.
    Never-synced connectors are an onboarding state, not an incident. Resolves its own
    alert when data resumes."""
    now = now or datetime.utcnow()
    connectors = db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.is_active == True).all()  # noqa: E712
    last = dict(
        db.query(BusinessEvent.connector_id, func.max(BusinessEvent.timestamp))
        .filter(BusinessEvent.tenant_id == tenant_id).group_by(BusinessEvent.connector_id).all()
    )
    out = []
    for c in connectors:
        last_at = last.get(c.id)
        if last_at is None:
            continue
        key = f"data_silence:{c.id}"
        status = coverage_mod._status_for_connector(c, last_at, now)
        if status.value != "stale":
            open_alert = _find_open_alert(db, tenant_id, key)
            if open_alert:                                   # data is back: close our own alert
                resolve_alert(db, open_alert, now)
            continue
        age = now - last_at
        limit = coverage_mod._STALE_AFTER.get(c.expected_frequency or "periodic", coverage_mod._DEFAULT_STALE_AFTER)
        sev = AlertSeverity.high if age > limit * 3 else AlertSeverity.medium
        hours = age.total_seconds() / 3600
        a = _upsert(
            db, tenant_id, key, now,
            {"connector_id": c.id, "connector": c.name, "last_data_at": last_at.isoformat(), "silent_hours": round(hours, 1),
             "expected_frequency": c.expected_frequency or "periodic"},
            dict(
                severity=sev, title=f"No new data from {c.name}",
                what_happened=(f"{c.name} last sent data {_ago(hours)}, which is longer than it normally goes without sending. "
                               f"Until it sends again, your sales and spending figures may be missing recent records, so other alerts may look better or worse than reality."),
                period_start=last_at, period_end=now, confidence=0.9, data_coverage_pct=0.0,
                recommended_action=f"Check that {c.name} is still switched on and connected (password changed? internet down? system closed?). Then open Data sources to confirm it is sending again.",
            ),
        )
        if a:
            out.append(a)
    return out


# ---------------- orchestration ----------------

def run_metric_detectors(db: Session, tenant_id: str, *, entity: str, field: str, direction: Optional[str] = None,
                         period_days: int = 7, baseline_periods: int = 4, deterioration_threshold: float = 0.10,
                         absolute_threshold: Optional[float] = None, now: Optional[datetime] = None) -> list[Alert]:
    """Every detector for one monitored metric. One failing detector never stops the others."""
    direction = direction or default_direction(entity)
    from app.core.model_versions import active_rebaseline
    now_ = now or datetime.utcnow()
    rb = active_rebaseline(db, tenant_id, now_, period_days * (baseline_periods + 1))
    if rb:                                         # Section 53: do not compare across a declared structural change
        from app.core import detectors_extra as ex
        try:
            a = ex.note_rebaseline(db, tenant_id, entity, field, period_days, rb, now_)
            return [a] if a else []
        except Exception as e:
            db.rollback()
            logger.error(f"Rebaseline note failed for tenant={tenant_id}: {e}")
            return []
    steps = [
        lambda: detect_historical_deviation(db, tenant_id, entity, field, period_days, baseline_periods,
                                            deterioration_threshold, now, direction),
        lambda: detect_opportunity(db, tenant_id, entity, field, direction, period_days, baseline_periods, now=now),
        lambda: detect_sustained_trend(db, tenant_id, entity, field, direction, period_days, now=now),
        lambda: detect_daily_anomaly(db, tenant_id, entity, field, direction, now=now),
    ]
    from app.core import detectors_extra as ex
    steps += [
        lambda: ex.detect_forecast_deviation(db, tenant_id, entity, field, direction, period_days, now=now),
        lambda: ex.detect_insufficient_data(db, tenant_id, entity, field, period_days, baseline_periods, now=now),
    ]
    has_data = db.query(BusinessEvent.id).filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity).first()
    if absolute_threshold is not None and has_data:      # a brand-new tenant with no data is not "below the line"
        steps.append(lambda: detect_threshold_breach(
            db, tenant_id, entity, field, float(absolute_threshold), "below" if direction == "drop" else "above",
            period_days=period_days, severity=AlertSeverity.high, now=now))
    alerts = []
    for step in steps:
        try:
            a = step()
            if a:
                alerts.append(a)
        except Exception as e:
            db.rollback()
            logger.error(f"Detector failed for tenant={tenant_id} {entity}.{field}: {e}")
    return alerts


def run_tenant_cycle(db: Session, tenant_id: str, metrics: list[dict], now: Optional[datetime] = None, replay: bool = False) -> dict:
    """All detectors for one tenant, then ranking/correlation/escalation over its open alerts.
    A live run compares whole days: the day in progress is incomplete, and counting it would make every week look
    like a drop. A run for a given `now` (tests, replays) uses exactly that moment."""
    live = now is None
    now = now or datetime.utcnow()
    window = start_of_local_day(now, tenant_zone(db, tenant_id)) if live and not replay else now
    alerts, errors = [], 0
    for m in metrics:
        alerts += run_metric_detectors(db, tenant_id, now=window, **m)
    try:
        if not replay:                  # silence compares to the latest event ever stored: meaningless for a past date
            alerts += detect_data_silence(db, tenant_id, now)
    except Exception as e:
        db.rollback(); errors += 1
        logger.error(f"Silence detector failed for tenant={tenant_id}: {e}")
    try:
        from app.core import detectors_extra as ex
        a = ex.detect_profit_risk(db, tenant_id, now=window)
        if a:
            alerts.append(a)
    except Exception as e:
        db.rollback(); errors += 1
        logger.error(f"Profit risk detector failed for tenant={tenant_id}: {e}")
    measured = 0
    try:
        measured = 0 if replay else measure_due_cases(db, tenant_id, now)
    except Exception as e:
        db.rollback(); errors += 1
        logger.error(f"Case outcome measurement failed for tenant={tenant_id}: {e}")
    try:
        from app.core.model_versions import detect_model_changes, stamp_versions
        if not replay:
            detect_model_changes(db, tenant_id, now)
        stamp_versions(db, tenant_id)
    except Exception as e:
        db.rollback(); errors += 1
        logger.error(f"Model versioning failed for tenant={tenant_id}: {e}")
    try:
        post = post_process_tenant(db, tenant_id, now)
    except Exception as e:
        db.rollback(); errors += 1; post = {}
        logger.error(f"Alert post-processing failed for tenant={tenant_id}: {e}")
    return {"alerts": alerts, "post": {**post, "cases_measured": measured}, "errors": errors}
