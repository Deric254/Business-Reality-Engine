"""
Detection Engine (Sections 19-20, 25) + basic Explanation (Section 27).

Two detection methods:
- Historical comparison: current period vs. a trailing baseline, flagged
  when the deviation crosses a threshold.
- Absolute threshold: a metric crosses a fixed line regardless of history.

Both write directly to the alerts table (Section 22 structure). Dedup
(Section 25) checks for an existing open alert of the same type/metric
before creating a new one, so re-running detection doesn't spam duplicates
— it updates the existing alert's evidence instead.

Explanation (Section 27): a revenue drop can come from fewer transactions,
a lower average ticket size, or fewer distinct customers — these are
different problems needing different responses. We decompose the same
event data along those dimensions rather than just reporting the total.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import BusinessEvent
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.core.coverage import coverage_pct_for_entity


@dataclass
class MetricWindow:
    tenant_id: str
    entity: str            # e.g. "sale", "expense"
    field: str              # "amount" or "quantity"
    period_days: int
    end: datetime


METRIC_FIELDS = ("amount", "quantity")  # the only numeric event columns a metric may sum


def _sum_metric(db: Session, tenant_id: str, entity: str, field: str, start: datetime, end: datetime) -> float:
    if field not in METRIC_FIELDS:   # never getattr() a caller-supplied name
        raise ValueError(f"field must be one of {METRIC_FIELDS}")
    col = getattr(BusinessEvent, field)
    result = (
        db.query(func.coalesce(func.sum(col), 0))
        .filter(
            BusinessEvent.tenant_id == tenant_id,
            BusinessEvent.entity == entity,
            BusinessEvent.timestamp >= start,
            BusinessEvent.timestamp < end,
            BusinessEvent.validation_status == "validated",
        )
        .scalar()
    )
    return float(result or 0)


def _severity_for_change(pct_change: float) -> AlertSeverity:
    """Larger deterioration -> higher severity. Improvement never triggers this path."""
    magnitude = abs(pct_change)
    if magnitude >= 0.30:
        return AlertSeverity.critical
    if magnitude >= 0.20:
        return AlertSeverity.high
    if magnitude >= 0.10:
        return AlertSeverity.medium
    return AlertSeverity.informational


ACTIVE_STATES = [
    AlertState.detected, AlertState.investigating, AlertState.acknowledged,
    AlertState.action_planned, AlertState.action_taken, AlertState.monitoring,
]
CLOSED_STATES = [AlertState.resolved, AlertState.dismissed, AlertState.false_positive]
# After a human closes an alert, the same condition must not re-alert immediately (Section 25
# suppression). A false positive is quiet for longer: the human said "this is not a problem".
COOLDOWN = {
    AlertState.resolved: timedelta(hours=24),
    AlertState.dismissed: timedelta(hours=48),
    AlertState.false_positive: timedelta(days=7),
}


def _find_open_alert(db: Session, tenant_id: str, dedup_key: str) -> Optional[Alert]:
    """
    Section 25 dedup. dedup_key is stored in evidence['dedup_key'] — a stable
    identifier for "this kind of alert about this metric", independent of
    the specific numbers this run. Only alerts still in an active state
    are reused. Indexed lookup (ix_alerts_dedup), not a scan of the tenant's alerts.
    """
    return (
        db.query(Alert)
        .filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE_STATES),
                Alert.evidence["dedup_key"].as_string() == dedup_key)
        .order_by(Alert.created_at.desc())
        .first()
    )


def _keep_lifecycle(existing, evidence: dict) -> dict:
    """A refresh replaces the measurements but must not erase lifecycle stamps (escalation)."""
    keep = {k: v for k, v in (existing.evidence or {}).items() if k == "escalated_at"}
    return {**evidence, **keep}


def _in_cooldown(db: Session, tenant_id: str, dedup_key: str, now: datetime) -> bool:
    """True if an alert with this dedup_key was closed recently enough to stay quiet."""
    closed = (
        db.query(Alert.state, Alert.updated_at, Alert.resolved_at)
        .filter(Alert.tenant_id == tenant_id, Alert.state.in_(CLOSED_STATES),
                Alert.evidence["dedup_key"].as_string() == dedup_key)
        .order_by(Alert.updated_at.desc())
        .limit(5)
        .all()
    )
    for state, updated_at, resolved_at in closed:
        closed_at = resolved_at or updated_at
        if closed_at and now - closed_at < COOLDOWN[state]:
            return True
    return False


def _period_stats(db: Session, tenant_id: str, entity: str, start: datetime, end: datetime) -> dict:
    """
    Section 6/27: decompose a total into the variables that actually drive
    it, so an alert can say WHY, not just WHAT.
    """
    row = (
        db.query(
            func.count(BusinessEvent.id),
            func.coalesce(func.sum(BusinessEvent.amount), 0),
            func.count(func.distinct(BusinessEvent.resolved_customer_id)),
        )
        .filter(
            BusinessEvent.tenant_id == tenant_id,
            BusinessEvent.entity == entity,
            BusinessEvent.timestamp >= start,
            BusinessEvent.timestamp < end,
            BusinessEvent.validation_status == "validated",
        )
        .one()
    )
    txn_count, total_amount, distinct_customers = row
    total_amount = float(total_amount or 0)
    avg_ticket = (total_amount / txn_count) if txn_count else 0.0
    return {
        "transaction_count": txn_count,
        "total_amount": total_amount,
        "avg_ticket": round(avg_ticket, 2),
        "distinct_customers": distinct_customers,
    }


def _pct_change(current: float, baseline: float) -> Optional[float]:
    if not baseline:
        return None
    return (current - baseline) / baseline


def explain_deviation(
    db: Session, tenant_id: str, entity: str,
    current_start: datetime, current_end: datetime,
    baseline_start: datetime, baseline_end: datetime,
    rising: bool = False,
) -> tuple[dict, list[dict]]:
    """
    Returns (related_variables, possible_explanations) for Section 27's
    "possible driver -> evidence for/against" structure.
    """
    current = _period_stats(db, tenant_id, entity, current_start, current_end)
    baseline = _period_stats(db, tenant_id, entity, baseline_start, baseline_end)

    txn_change = _pct_change(current["transaction_count"], baseline["transaction_count"])
    ticket_change = _pct_change(current["avg_ticket"], baseline["avg_ticket"])
    customer_change = _pct_change(current["distinct_customers"], baseline["distinct_customers"])

    related_variables = {
        "transaction_count": {"current": current["transaction_count"], "baseline": baseline["transaction_count"],
                               "change_pct": round(txn_change * 100, 1) if txn_change is not None else None},
        "avg_ticket": {"current": current["avg_ticket"], "baseline": baseline["avg_ticket"],
                       "change_pct": round(ticket_change * 100, 1) if ticket_change is not None else None},
        "distinct_customers": {"current": current["distinct_customers"], "baseline": baseline["distinct_customers"],
                                "change_pct": round(customer_change * 100, 1) if customer_change is not None else None},
    }

    explanations = []
    NOTABLE = 0.10  # a 10%+ move in a driver is worth naming; smaller is noise
    # For cost-like metrics the bad move is UP, so drivers are named by increases.
    sign = -1 if rising else 1
    word = "rose" if rising else "fell"

    if txn_change is not None and sign * txn_change <= -NOTABLE:
        explanations.append({
            "driver": "Transaction volume increase" if rising else "Transaction volume decline",
            "evidence": f"Transaction count {word} {abs(txn_change)*100:.1f}% "
                        f"({baseline['transaction_count']} -> {current['transaction_count']}).",
        })
    if ticket_change is not None and sign * ticket_change <= -NOTABLE:
        explanations.append({
            "driver": "Higher average value per record" if rising else "Lower average sale value",
            "evidence": f"Average ticket size {word} {abs(ticket_change)*100:.1f}% "
                        f"({baseline['avg_ticket']:.2f} -> {current['avg_ticket']:.2f}).",
        })
    if customer_change is not None and sign * customer_change <= -NOTABLE:
        explanations.append({
            "driver": "More distinct customers" if rising else "Fewer distinct customers",
            "evidence": f"Distinct customers {word} {abs(customer_change)*100:.1f}% "
                        f"({baseline['distinct_customers']} -> {current['distinct_customers']}).",
        })

    # Explicitly note what did NOT change — as informative as what did move.
    if ticket_change is not None and abs(ticket_change) < NOTABLE:
        explanations.append({
            "driver": "Average sale value (ruled out)",
            "evidence": f"Average ticket size is stable ({baseline['avg_ticket']:.2f} -> "
                        f"{current['avg_ticket']:.2f}, {ticket_change*100:+.1f}%) — not a pricing/mix issue.",
        })

    return related_variables, explanations


def detect_historical_deviation(
    db: Session,
    tenant_id: str,
    entity: str = "sale",
    field: str = "amount",
    period_days: int = 7,
    baseline_periods: int = 4,
    deterioration_threshold: float = 0.10,
    now: Optional[datetime] = None,
    direction: str = "drop",
) -> Optional[Alert]:
    """
    Compares the most recent `period_days` window against the average of
    the `baseline_periods` windows before it. Flags a move in the BAD
    direction past `deterioration_threshold`: a fall for revenue-like
    metrics (direction="drop"), a rise for cost-like ones (direction="rise").
    A move the good way is handled by detect_opportunity.
    """
    if direction not in ("drop", "rise"):
        raise ValueError("direction must be 'drop' or 'rise'")
    now = now or datetime.utcnow()
    current_start = now - timedelta(days=period_days)
    current_value = _sum_metric(db, tenant_id, entity, field, current_start, now)

    baseline_values = []
    for i in range(1, baseline_periods + 1):
        b_end = now - timedelta(days=period_days * i)
        b_start = now - timedelta(days=period_days * (i + 1))
        baseline_values.append(_sum_metric(db, tenant_id, entity, field, b_start, b_end))

    valid_baseline = [v for v in baseline_values if v > 0]
    if not valid_baseline:
        # No usable history yet — can't compare, so don't fabricate a false signal.
        return None

    baseline_avg = sum(valid_baseline) / len(valid_baseline)
    if baseline_avg == 0:
        return None

    pct_change = (current_value - baseline_avg) / baseline_avg
    rising = direction == "rise"
    bad_move = pct_change if rising else -pct_change     # positive = moved the bad way

    if bad_move < deterioration_threshold:
        # Flat, moved the good way, or within noise — no alert.
        return None

    dedup_key = f"historical_deviation:{entity}:{field}:{period_days}d"
    evidence = {
        "metric": f"{entity}.{field}",
        "current": current_value,
        "baseline": round(baseline_avg, 2),
        "change_pct": round(pct_change * 100, 2),
        "period_days": period_days,
        "baseline_periods_used": len(valid_baseline),
        "direction": direction,
        "dedup_key": dedup_key,
    }
    what_happened = (
        f"{entity.capitalize()} {field} is {abs(pct_change)*100:.1f}% {'above' if rising else 'below'} the "
        f"{baseline_periods}-period baseline over the last {period_days} days."
    )

    # Explanation (Section 27): compare current period against the single
    # most recent baseline period (not the multi-period average — that's a
    # single comparable window a transaction/ticket/customer breakdown can
    # be run against).
    related_variables, possible_explanations = explain_deviation(
        db, tenant_id, entity,
        current_start=current_start, current_end=now,
        baseline_start=now - timedelta(days=period_days * 2),
        baseline_end=now - timedelta(days=period_days),
        rising=rising,
    )

    existing = _find_open_alert(db, tenant_id, dedup_key)
    if existing:
        # Same ongoing issue — update evidence and severity, don't spam a new alert (Section 25).
        existing.evidence = _keep_lifecycle(existing, evidence)
        existing.severity = _severity_for_change(pct_change)
        existing.magnitude = round(pct_change, 4)
        existing.related_variables = related_variables
        existing.possible_explanations = possible_explanations
        existing.data_coverage_pct = coverage_pct_for_entity(db, tenant_id, entity, now=now)
        existing.what_happened = what_happened
        db.commit()
        db.refresh(existing)
        return existing

    if _in_cooldown(db, tenant_id, dedup_key, now):
        return None   # a human closed this recently; stay quiet (Section 25 suppression)

    alert = Alert(
        tenant_id=tenant_id,
        severity=_severity_for_change(pct_change),
        state=AlertState.detected,
        title=f"{entity.capitalize()} increase detected" if rising else f"{entity.capitalize()} deterioration detected",
        what_happened=what_happened,
        magnitude=round(pct_change, 4),
        period_start=current_start,
        period_end=now,
        evidence=evidence,
        related_variables=related_variables,
        possible_explanations=possible_explanations,
        confidence=0.7 if len(valid_baseline) >= 2 else 0.4,  # more baseline periods = more confidence
        data_coverage_pct=coverage_pct_for_entity(db, tenant_id, entity, now=now),
        recommended_action=(
            f"Review recent {entity} activity for the causes of the increase." if rising
            else f"Review recent {entity} activity for causes of the decline."
        ),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def detect_threshold_breach(
    db: Session,
    tenant_id: str,
    entity: str,
    field: str,
    threshold: float,
    direction: str,          # "below" or "above"
    period_days: int = 1,
    severity: AlertSeverity = AlertSeverity.critical,
    now: Optional[datetime] = None,
) -> Optional[Alert]:
    """Fixed-line rule, e.g. 'cash below minimum threshold' (Section 19)."""
    now = now or datetime.utcnow()
    start = now - timedelta(days=period_days)
    value = _sum_metric(db, tenant_id, entity, field, start, now)

    breached = value < threshold if direction == "below" else value > threshold
    if not breached:
        return None

    dedup_key = f"threshold:{entity}:{field}:{direction}:{threshold}"
    evidence = {
        "metric": f"{entity}.{field}",
        "value": value,
        "threshold": threshold,
        "direction": direction,
        "period_days": period_days,
        "dedup_key": dedup_key,
    }

    existing = _find_open_alert(db, tenant_id, dedup_key)
    if existing:
        existing.evidence = _keep_lifecycle(existing, evidence)
        db.commit()
        db.refresh(existing)
        return existing

    if _in_cooldown(db, tenant_id, dedup_key, now):
        return None

    alert = Alert(
        tenant_id=tenant_id,
        severity=severity,
        state=AlertState.detected,
        title=f"{entity.capitalize()} {field} threshold breached",
        what_happened=f"{entity.capitalize()} {field} is {value:.2f}, {direction} threshold {threshold:.2f}.",
        period_start=start,
        period_end=now,
        evidence=evidence,
        confidence=1.0,  # a fixed threshold breach is a fact, not an inference
        recommended_action=f"Immediate review required — {field} has crossed a defined safety threshold.",
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert
