"""
Alert lifecycle over a tenant's open alerts (Sections 25-26): priority ranking,
correlation (grouping related alerts) and escalation of ignored ones.
Suppression (cooldown after a human closes an alert) lives with dedup in detection.py.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.alerts import Alert, AlertSeverity, AlertState
from app.core.clock import utcnow

SEVERITY_BASE = {
    AlertSeverity.critical: 100, AlertSeverity.high: 70, AlertSeverity.medium: 40,
    AlertSeverity.opportunity: 25, AlertSeverity.informational: 15,
}
# An alert nobody has touched (still "detected") is escalated after this long.
ESCALATE_AFTER = {AlertSeverity.critical: timedelta(hours=4), AlertSeverity.high: timedelta(hours=24)}
ESCALATION_BONUS = 25.0
CORRELATION_WINDOW = timedelta(hours=72)
MAX_RELATED = 10
ACTIVE = [AlertState.detected, AlertState.investigating, AlertState.acknowledged,
          AlertState.action_planned, AlertState.action_taken, AlertState.monitoring]


def priority_score(alert: Alert, now: datetime) -> float:
    """Higher = look at it first. Severity dominates; within a severity, bigger moves, more
    confident findings and better-covered data rank higher; escalated alerts jump the queue.
      base(severity) + min(20, |magnitude|*50) + confidence*10 - (100-coverage%)*0.1 + 25 if escalated"""
    score = SEVERITY_BASE[alert.severity]
    if alert.magnitude is not None:
        score += min(20.0, abs(float(alert.magnitude)) * 50)
    score += float(alert.confidence if alert.confidence is not None else 0.5) * 10
    if alert.data_coverage_pct is not None:
        score -= (100 - float(alert.data_coverage_pct)) * 0.1
    if (alert.evidence or {}).get("escalated_at"):
        score += ESCALATION_BONUS
    return round(max(0.0, min(999.99, score)), 2)


def _entity_of(alert: Alert) -> Optional[str]:
    ev = alert.evidence or {}
    metric = ev.get("metric")
    if metric:
        return str(metric).split(".")[0]
    return None


_KIND_BY_PREFIX = {"historical_deviation": "deviation", "opportunity": "opportunity", "trend": "trend", "daily_anomaly": "daily",
                   "data_silence": "silence", "threshold": "threshold", "forecast_miss": "forecast", "risk": "risk", "unknown": "unknown"}


def kind_of(alert: Alert) -> Optional[str]:
    key = (alert.evidence or {}).get("dedup_key")
    return _KIND_BY_PREFIX.get(str(key).split(":")[0]) if key else None


def _attach_model_changes(db: Session, a: Alert) -> None:
    from app.core.model_versions import changes_near
    ch = changes_near(db, a.tenant_id, a.period_start, a.period_end)
    if (a.evidence or {}).get("model_changes", []) != ch and (ch or (a.evidence or {}).get("model_changes")):
        a.evidence = {**(a.evidence or {}), "model_changes": ch}


_DRIVER_KINDS = {"deviation", "forecast", "daily"}


def _attach_drivers(db: Session, a: Alert) -> None:
    """Section 6: name the customer/product/location behind a change, when one clearly is."""
    from app.core.relationships import change_drivers
    ent = _entity_of(a)
    if kind_of(a) not in _DRIVER_KINDS or ent not in ("sale", "expense") or not (a.period_start and a.period_end):
        return
    d = change_drivers(db, a.tenant_id, ent, a.period_start, a.period_end)
    new = {"drivers": d["drivers"], "statements": d["statements"]}
    if (a.evidence or {}).get("change_drivers") != new:
        a.evidence = {**(a.evidence or {}), "change_drivers": new}


def post_process_tenant(db: Session, tenant_id: str, now: Optional[datetime] = None) -> dict:
    now = now or utcnow()
    open_alerts = (
        db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE))
        .order_by(Alert.created_at.desc()).limit(500).all()
    )
    escalated = 0
    from app.core.context import attach_context
    for a in open_alerts:
        if a.kind is None and kind_of(a):
            a.kind = kind_of(a)
        try:
            with db.begin_nested():
                attach_context(db, a)
                _attach_drivers(db, a)
                _attach_model_changes(db, a)
        except Exception as e:      # enrichment must never stop ranking/escalation
            logging.getLogger("business_reality_engine").warning(f"alert enrichment failed for {a.id}: {e}")
        after = ESCALATE_AFTER.get(a.severity)
        if after and a.state == AlertState.detected and a.created_at and now - a.created_at >= after \
                and not (a.evidence or {}).get("escalated_at"):
            a.evidence = {**(a.evidence or {}), "escalated_at": now.isoformat()}   # new dict: JSON change is tracked
            escalated += 1

    # Correlation: open alerts from the last 72h about the same kind of record belong together.
    groups: dict[str, list[Alert]] = {}
    for a in open_alerts:
        ent = _entity_of(a)
        if ent and a.created_at and now - a.created_at <= CORRELATION_WINDOW:
            groups.setdefault(ent, []).append(a)
    correlated = 0
    in_group = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        for a in members:
            others = sorted(m.id for m in members if m.id != a.id)[:MAX_RELATED]
            in_group.add(a.id)
            if (a.correlated_alert_ids or []) != others:
                a.correlated_alert_ids = others
                correlated += 1
    for a in open_alerts:                       # no longer part of any group: clear stale links
        if a.id not in in_group and a.correlated_alert_ids:
            a.correlated_alert_ids = None

    ranked = 0
    for a in open_alerts:
        score = priority_score(a, now)
        if a.priority_rank is None or float(a.priority_rank) != score:
            a.priority_rank = score
            ranked += 1
    db.commit()
    return {"open": len(open_alerts), "escalated": escalated, "correlated": correlated, "ranked": ranked}
