"""
Business Memory (Sections 36-41).

Section 37: the system should not depend on manual recording. A case is
created from an alert's own evidence — the person only supplies a decision
and, optionally, what they're doing about it. Nothing is retyped.

Section 38-39: outcome measurement compares a real before/after window on
the same metric the alert was about. It NEVER claims the intervention
caused the change — other things could have moved in the same window.
attribution_confidence is capped at "medium", never "high": a single
before/after comparison with no control group cannot support high
confidence in causation, only in the fact that something changed.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.models.alerts import Alert, Case
from app.core.detection import _sum_metric
from app.core.clock import utcnow

logger = logging.getLogger("business_reality_engine")


def create_case_from_alert(
    db: Session, tenant_id: str, alert_id: str,
    decision: str, intervention: Optional[str] = None,
    expected_outcome: Optional[str] = None, measurement_period_days: Optional[int] = None,
) -> Case:
    """
    Section 37: pulls problem/evidence/context straight from the alert —
    the human only supplies the decision and (optionally) what they did.
    """
    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.tenant_id == tenant_id).first()
    if not alert:
        raise ValueError("Alert not found for this tenant")

    period_days = measurement_period_days or (alert.evidence or {}).get("period_days", 7)

    case = Case(
        tenant_id=tenant_id,
        alert_id=alert.id,
        problem=f"{alert.title}: {alert.what_happened}",
        evidence=alert.evidence or {},
        context={
            "related_variables": alert.related_variables,
            "possible_explanations": alert.possible_explanations,
        },
        recommendation=alert.ai_recommended_action or alert.recommended_action,
        decision=decision,
        intervention=intervention,
        expected_outcome=expected_outcome,
        measurement_period_days=period_days,
        detected_at=alert.created_at,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def measure_case_outcome(db: Session, tenant_id: str, case_id: str, now: Optional[datetime] = None) -> dict:
    """
    Section 38: before vs. after on the same metric, over the case's own
    measurement_period_days. Returns status='pending' if not enough time
    has elapsed since the case was opened — never fabricates a result from
    an incomplete window.
    """
    now = now or utcnow()
    case = db.query(Case).filter(Case.id == case_id, Case.tenant_id == tenant_id).first()
    if not case:
        raise ValueError("Case not found for this tenant")

    period_days = int(case.measurement_period_days or 7)
    metric = (case.evidence or {}).get("metric")  # "entity.field", e.g. "sale.amount"
    if not metric or "." not in metric:
        raise ValueError("Case evidence has no resolvable metric — cannot measure outcome")
    entity, field = metric.split(".", 1)

    elapsed_days = (now - case.detected_at).days
    if elapsed_days < period_days:
        return {
            "status": "pending",
            "message": f"Not enough time elapsed to measure outcome yet "
                       f"({elapsed_days} of {period_days} days since the case was opened).",
        }

    before_end = case.detected_at
    before_start = before_end - timedelta(days=period_days)
    after_start = case.detected_at
    after_end = case.detected_at + timedelta(days=period_days)

    before_value = _sum_metric(db, tenant_id, entity, field, before_start, before_end)
    after_value = _sum_metric(db, tenant_id, entity, field, after_start, after_end)

    pct_change = ((after_value - before_value) / before_value) if before_value else None

    # Section 39: describe what changed, never assert what caused it.
    if pct_change is None:
        outcome_text = f"{entity.capitalize()} {field} was {after_value:.2f} in the {period_days} days " \
                       f"following the case, compared to {before_value:.2f} before (baseline was zero, so " \
                       f"percentage change is not meaningful)."
        confidence = "uncertain"
    else:
        direction = "improved" if pct_change > 0 else ("declined further" if pct_change < 0 else "stayed flat")
        change_phrase = f"by {abs(pct_change)*100:.1f}%" if pct_change != 0 else "(no meaningful change)"
        outcome_text = (
            f"{entity.capitalize()} {field} {direction} {change_phrase} "
            f"({before_value:.2f} -> {after_value:.2f}) in the {period_days} days following the case. "
            f"Other factors may also have changed in this window, so this does not confirm the "
            f"intervention caused the change — only that the metric moved."
        )
        # Capped at "medium" deliberately — see module docstring. A single
        # before/after with no control group cannot support "high".
        if pct_change > 0.10:
            confidence = "medium"
        elif pct_change < -0.10:
            confidence = "low"
        else:
            confidence = "uncertain"

    case.actual_outcome = outcome_text
    case.attribution_confidence = confidence
    case.outcome_measured_at = now
    db.commit()
    db.refresh(case)

    return {
        "status": "measured", "actual_outcome": outcome_text, "attribution_confidence": confidence,
        "before_value": before_value, "after_value": after_value,
        "pct_change": round(pct_change * 100, 1) if pct_change is not None else None,
    }


def find_similar_cases(db: Session, tenant_id: str, alert: Alert, limit: int = 3) -> list[Case]:
    """
    Section 40: historical case retrieval. "Same kind of problem" = the same detector, metric
    and period (the alert's dedup_key); alerts without one fall back to the same title. Only
    this tenant's cases, never the alert's own, only those with a measured outcome (an
    unmeasured case has nothing to compare to), most recently measured first.
    """
    key = (alert.evidence or {}).get("dedup_key")
    q = (
        db.query(Case)
        .join(Alert, Alert.id == Case.alert_id)
        .filter(Case.tenant_id == tenant_id, Alert.tenant_id == tenant_id, Alert.id != alert.id,
                Case.actual_outcome.isnot(None))
    )
    if key:
        q = q.filter(Alert.evidence["dedup_key"].as_string() == key)
    else:
        q = q.filter(Alert.title == alert.title)
    return q.order_by(Case.outcome_measured_at.desc()).limit(limit).all()


def historical_experience(db: Session, tenant_id: str, alert: Alert, limit: int = 3) -> list[dict]:
    """Section 35/36: what happened the last times this business met this problem."""
    return [
        {
            "case_id": c.id, "when": c.detected_at, "decision": c.decision, "intervention": c.intervention,
            "outcome": c.actual_outcome, "attribution_confidence": c.attribution_confidence,
        }
        for c in find_similar_cases(db, tenant_id, alert, limit)
    ]


def measure_due_cases(db: Session, tenant_id: str, now: Optional[datetime] = None, limit: int = 100) -> int:
    """Section 37: nobody should have to remember to measure. Every cycle, measure the cases whose
    window has fully elapsed. Cases that cannot be measured (no metric) are skipped, never retried noisily."""
    now = now or utcnow()
    due = (
        db.query(Case)
        .filter(Case.tenant_id == tenant_id, Case.actual_outcome.is_(None), Case.outcome_measured_at.is_(None))
        .order_by(Case.detected_at).limit(limit).all()
    )
    done = 0
    for c in due:
        metric = (c.evidence or {}).get("metric")
        if not metric or "." not in metric:
            continue
        if now - c.detected_at < timedelta(days=int(c.measurement_period_days or 7)):
            continue
        try:
            if measure_case_outcome(db, tenant_id, c.id, now)["status"] == "measured":
                done += 1
        except Exception:
            logger.warning("Could not measure the outcome of case %s", c.id, exc_info=True)
            db.rollback()
    return done
