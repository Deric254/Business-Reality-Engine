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
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import BusinessEvent, Tenant
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.core.coverage import coverage_pct_for_entity


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


_NOUNS = {("sale", "amount"): "Sales", ("expense", "amount"): "Spending", ("sale", "quantity"): "Items sold"}


def _noun(entity: str, field: str) -> str:
    return _NOUNS.get((entity, field), f"{entity.capitalize()} {field}")


def currency_of(db: Session, tenant_id: str) -> str:
    tenant = db.get(Tenant, tenant_id)
    return (tenant.base_currency if tenant else "") or ""


def money_formatter(db: Session, tenant_id: str, field: str = "amount"):
    """Amounts in the business's own currency ('KES 9,060'); counts such as items sold get no currency."""
    cur = currency_of(db, tenant_id) if field == "amount" else ""
    return lambda v: f"{cur} {v:,.0f}".strip()


def _be(noun: str, past: bool = False) -> str:
    if noun == "Spending":
        return "was" if past else "is"
    return "were" if past else "are"


def _usual(period_days: int) -> str:
    return "your usual week" if period_days == 7 else f"your usual {period_days} days"


def _size_of_change(change: float) -> str:
    """'down 59%' / 'up 30%'; past +100% a multiple reads better than a huge percentage ('15 times', not '1404%')."""
    if change >= 1:
        times = 1 + change
        return f"{times:.0f} times" if times >= 10 else f"{times:.1f} times".replace(".0 ", " ")
    return f"{'up' if change > 0 else 'down'} {abs(change) * 100:.0f}%"


def _headline(entity: str, field: str, change: float, period_days: int) -> str:
    """Says what it is compared with, so two alerts about one metric can never look like they disagree:
    'Sales are down 59% from your usual week', 'Spending is 15 times your usual week'."""
    return compare_headline(_noun(entity, field), change, _usual(period_days))


def compare_headline(noun: str, change: float, usual: str, past: bool = False) -> str:
    """'<Sales> are down 59% from <your usual week>'. `usual` is the thing it is compared with, always spelled out."""
    joiner = "" if change >= 1 else " from"
    return f"{noun} {_be(noun, past)} {_size_of_change(change)}{joiner} {usual}"


def comparison_text(entity: str, field: str, current: float, usual: float, period_days: int, periods_used: int, fmt=None) -> str:
    """The plain facts behind a headline: the last N days, a usual N days, and the gap between them."""
    noun = _noun(entity, field).lower()
    fmt = fmt or (lambda v: f"{v:,.0f}")
    gap = abs(current - usual)
    return (f"In the last {period_days} complete days {noun} came to {fmt(current)}. In a usual {period_days} days it is about {fmt(usual)} "
            f"(the average of the {periods_used} period{'s' if periods_used != 1 else ''} of {period_days} days before). "
            f"That is {fmt(gap)} {'more' if current > usual else 'less'} than usual.")


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


def resolve_alert(db: Session, alert: Alert, now: datetime) -> None:
    """Close an alert this system raised itself, because its condition no longer holds."""
    alert.state = AlertState.resolved
    alert.resolved_at = now
    alert.evidence = {**(alert.evidence or {}), "recovered_at": now.isoformat()}
    db.commit()


def close_if_cleared(db: Session, tenant_id: str, dedup_key: str, now: datetime) -> None:
    """The problem has clearly gone, so the alert is closed whatever state it was in (new, acknowledged, being worked
    on), and the list only shows what is still true. Otherwise 'Sales are down 59%' would sit there, with old numbers,
    after sales recovered. The record stays (state resolved, with when it recovered); nothing is deleted."""
    open_alert = _find_open_alert(db, tenant_id, dedup_key)
    if open_alert:
        resolve_alert(db, open_alert, now)


def _keep_lifecycle(existing, evidence: dict) -> dict:
    """A refresh replaces the measurements but must not erase lifecycle stamps (escalation)."""
    keep = {k: v for k, v in (existing.evidence or {}).items() if k == "escalated_at"}
    return {**evidence, **keep}


def _in_cooldown(db: Session, tenant_id: str, dedup_key: str, now: datetime) -> bool:
    """True if a person closed an alert with this dedup_key recently enough that it should stay quiet."""
    closed = (
        db.query(Alert.state, Alert.updated_at, Alert.resolved_at)
        .filter(Alert.tenant_id == tenant_id, Alert.state.in_(CLOSED_STATES),
                Alert.evidence["dedup_key"].as_string() == dedup_key,
                Alert.evidence["recovered_at"].as_string().is_(None))     # closed by the system when it cleared, not by a person
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


def _by_dimension(db: Session, tenant_id: str, entity: str, column: str, start: datetime, end: datetime) -> dict[str, float]:
    col = getattr(BusinessEvent, column)
    rows = (
        db.query(col, func.coalesce(func.sum(BusinessEvent.amount), 0))
        .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity,
                BusinessEvent.timestamp >= start, BusinessEvent.timestamp < end,
                BusinessEvent.validation_status == "validated", col.isnot(None))
        .group_by(col).all()
    )
    return {value: float(amount) for value, amount in rows}


def dimension_movers(
    db: Session, tenant_id: str, entity: str, column: str, current: tuple[datetime, datetime],
    baseline: tuple[datetime, datetime], rising: bool, notable: float = 0.10, limit: int = 3,
) -> list[dict]:
    """Which branches (column="location") or products (column="product_id") moved the total, biggest first.
    Only values that moved the same way as the total, by a notable amount, and that carry a real share (15%+)
    of all movement that way; a value with no earlier period cannot be compared and is left out."""
    now_t, before_t = _by_dimension(db, tenant_id, entity, column, *current), _by_dimension(db, tenant_id, entity, column, *baseline)
    values = set(now_t) | set(before_t)
    if len(values) < 2:
        return []                                       # one branch or product: nothing to compare against
    moved = {v: d for v in values if (d := now_t.get(v, 0.0) - before_t.get(v, 0.0)) and (d > 0) == rising}
    total = sum(abs(d) for d in moved.values())
    out = []
    for value, d in sorted(moved.items(), key=lambda kv: -abs(kv[1])):
        before = before_t.get(value, 0.0)
        if not before or abs(d) / before < notable or abs(d) / total < 0.15:
            continue
        out.append({"value": value, "before": before, "after": now_t.get(value, 0.0),
                    "change_pct": round(d / before * 100), "share_pct": round(abs(d) / total * 100)})
        if len(out) == limit:
            break
    return out


def _pct_change(current: float, baseline: float) -> Optional[float]:
    if not baseline:
        return None
    return (current - baseline) / baseline


def _one_off_driver(db: Session, tenant_id: str, entity: str, what: str, rising: bool,
                    cur_start: datetime, cur_end: datetime, base_start: datetime, base_end: datetime,
                    cur_total: float, base_total: float, fmt) -> Optional[dict]:
    """Did ONE record cause most of the move? A single big purchase (or one big sale last period) looks like a crisis
    (or a collapse) but may be a one-off, and the owner needs to know that before reacting. Rising: the largest record
    now; falling: the largest record in the earlier period that is no longer there. Needs a clearly out-of-pattern
    record (4x the average of the others, at least half of the whole change) so ordinary busy weeks never trigger it."""
    start, end = (cur_start, cur_end) if rising else (base_start, base_end)
    move = (cur_total - base_total) if rising else (base_total - cur_total)
    if move <= 0:
        return None
    count, total = db.query(func.count(BusinessEvent.id), func.coalesce(func.sum(BusinessEvent.amount), 0)).filter(
        BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity, BusinessEvent.timestamp >= start,
        BusinessEvent.timestamp < end, BusinessEvent.validation_status == "validated", BusinessEvent.amount.isnot(None)).one()
    top = (db.query(BusinessEvent.amount, BusinessEvent.timestamp).filter(
        BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity == entity, BusinessEvent.timestamp >= start,
        BusinessEvent.timestamp < end, BusinessEvent.validation_status == "validated", BusinessEvent.amount.isnot(None))
        .order_by(BusinessEvent.amount.desc()).first())
    if count < 3 or not top:
        return None
    amount, when = float(top[0]), top[1]
    others_avg = (float(total) - amount) / (count - 1)
    share = amount / move
    if amount < 4 * others_avg or share < 0.5:
        return None
    from datetime import timezone as _tz
    from app.core.tenant_time import tenant_zone
    when = when.replace(tzinfo=_tz.utc).astimezone(tenant_zone(db, tenant_id))  # stored as naive UTC; say the business's own day
    day = f"{when:%d %b}"
    share_text = "all" if share >= 1 else f"{share * 100:.0f}% of"
    if rising:
        evidence = (f"One {what} of {fmt(amount)} on {day} is {share_text} the increase. Without it, the total would be {fmt(cur_total - amount)} "
                    f"(before: {fmt(base_total)}). If it was a one-off, your normal level has not really changed.")
    else:
        evidence = (f"The earlier period included one {what} of {fmt(amount)} on {day}, which is {share_text} the fall. "
                    f"If that was unusual, the drop looks worse than it really is.")
    return {"driver": f"One large {what}", "evidence": evidence, "one_off": True, "record": {"amount": amount, "amount_text": fmt(amount), "date": when.date().isoformat()}}


def one_off_action(driver: dict, rising: bool) -> str:
    """The next step when one record explains the move: check that record first, and say what to tap if it was expected."""
    rec = driver["record"]
    if rising:
        return (f"Start with the large payment of {rec['amount_text']} on {rec['date']}. If it was planned (stock, equipment, a loan repayment), "
                f"tap 'Not relevant' and this alert stays quiet for 2 days. If you do not recognise it, treat it as a problem and find out who approved it.")
    return (f"Check the large sale of {rec['amount_text']} on {rec['date']} in the earlier period. If big sales like that are rare for you, "
            f"the real drop is smaller than shown; if they are normal, find out why this period had none.")


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
    fmt = money_formatter(db, tenant_id)
    what, count_word = ("payment", "payments") if entity == "expense" else ("sale", "sales")

    one_off = _one_off_driver(db, tenant_id, entity, what, rising, current_start, current_end, baseline_start, baseline_end,
                              current["total_amount"], baseline["total_amount"], fmt)
    if one_off:
        explanations.append(one_off)       # first: if one record explains the move, the owner should look at it before anything else

    # Where it happened comes next: a branch or product the owner can act on beats a generic "volume fell".
    product_label = ("Category", "categories") if entity == "expense" else ("Product", "products")      # what a cost was spent on
    for column, (label, plural) in (("location", ("Branch", "branches")), ("product_id", product_label)):
        for m in dimension_movers(db, tenant_id, entity, column, (current_start, current_end), (baseline_start, baseline_end), rising, NOTABLE):
            explanations.append({
                "driver": f"{label}: {m['value']} {word}",
                "evidence": f"{m['value']} went from {fmt(m['before'])} to {fmt(m['after'])} ({m['change_pct']:+d}%). "
                            f"That is {m['share_pct']}% of the {'rise' if rising else 'fall'} across the {plural} that {word}.",
            })

    if txn_change is not None and sign * txn_change <= -NOTABLE:
        explanations.append({
            "driver": f"More {count_word}" if rising else f"Fewer {count_word}",
            "evidence": f"The number of {count_word} {word} {abs(txn_change)*100:.0f}% "
                        f"(from {baseline['transaction_count']} to {current['transaction_count']}).",
        })
    if ticket_change is not None and sign * ticket_change <= -NOTABLE:
        explanations.append({
            "driver": f"Bigger average {what}" if rising else f"Smaller average {what}",
            "evidence": f"The average {what} {word} {abs(ticket_change)*100:.0f}% "
                        f"(from {fmt(baseline['avg_ticket'])} to {fmt(current['avg_ticket'])}).",
        })
    if customer_change is not None and sign * customer_change <= -NOTABLE:
        explanations.append({
            "driver": "More different customers" if rising else "Fewer different customers",
            "evidence": f"The number of different customers {word} {abs(customer_change)*100:.0f}% "
                        f"(from {baseline['distinct_customers']} to {current['distinct_customers']}).",
        })

    # Explicitly note what did NOT change — as informative as what did move.
    if ticket_change is not None and abs(ticket_change) < NOTABLE:
        explanations.append({
            "driver": f"Average {what} (ruled out)",
            "evidence": f"The average {what} stayed about the same ({fmt(baseline['avg_ticket'])} before, {fmt(current['avg_ticket'])} now), "
                        f"so prices and what you sell are probably not the cause.",
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

    dedup_key = f"historical_deviation:{entity}:{field}:{period_days}d"
    if bad_move < deterioration_threshold:
        # Flat, moved the good way, or within noise — no new alert. Well inside the line: the old one is over.
        if bad_move < deterioration_threshold / 2:
            close_if_cleared(db, tenant_id, dedup_key, now)
        return None

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
    what_happened = comparison_text(entity, field, current_value, baseline_avg, period_days, len(valid_baseline), money_formatter(db, tenant_id, field))

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

    one_off = next((e for e in possible_explanations if e.get("one_off")), None)
    noun_lower = _noun(entity, field).lower()
    if one_off:
        action = one_off_action(one_off, rising)
    elif rising:
        action = f"Check what drove the rise in {noun_lower}: which days, branches or suppliers grew the most."
    else:
        action = (f"Check what changed in {noun_lower}: which days, branches or products fell the most, and whether anything changed "
                  f"on your side (prices, stock, staff, opening hours).")

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
        existing.recommended_action = action
        db.commit()
        db.refresh(existing)
        return existing

    if _in_cooldown(db, tenant_id, dedup_key, now):
        return None   # a human closed this recently; stay quiet (Section 25 suppression)

    alert = Alert(
        tenant_id=tenant_id,
        severity=_severity_for_change(pct_change),
        state=AlertState.detected,
        title=_headline(entity, field, pct_change, period_days),
        what_happened=what_happened,
        magnitude=round(pct_change, 4),
        period_start=current_start,
        period_end=now,
        evidence=evidence,
        related_variables=related_variables,
        possible_explanations=possible_explanations,
        confidence=0.7 if len(valid_baseline) >= 2 else 0.4,  # more baseline periods = more confidence
        data_coverage_pct=coverage_pct_for_entity(db, tenant_id, entity, now=now),
        recommended_action=action,
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
        title=f"{_noun(entity, field)} {_be(_noun(entity, field))} {direction} the limit you set",
        what_happened=(f"In the last {period_days} day{'s' if period_days != 1 else ''} {_noun(entity, field).lower()} came to {money_formatter(db, tenant_id, field)(value)}. "
                       f"You asked to be warned when this goes {direction} {money_formatter(db, tenant_id, field)(threshold)}."),
        period_start=start,
        period_end=now,
        evidence=evidence,
        confidence=1.0,  # a fixed threshold breach is a fact, not an inference
        recommended_action=f"Look at this today: {_noun(entity, field).lower()} have crossed the limit you set. Change the limit in Settings if it no longer fits your business.",
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert
