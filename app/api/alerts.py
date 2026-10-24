from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.auth_users import authorize_tenant
from app.core.detection import ACTIVE_STATES, CLOSED_STATES
from app.models.alerts import Alert, AlertState
from app.core.business_memory import historical_experience
from app.reasoning.llm_reasoning import reason_about_alert

from app.core.advisory import advisory_text
from app.models.core import Tenant

router = APIRouter()


def _get_owned_alert(db: Session, alert_id: str, tenant_id: str) -> Alert:
    """
    Section 48: every alert lookup must verify tenant ownership, not just
    existence. A 404 (not 403) on mismatch — confirming an alert exists
    for a DIFFERENT tenant is itself a data leak.
    """
    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.tenant_id == tenant_id).first()
    if not alert:
        raise HTTPException(404, "Alert not found")
    return alert


class AlertOut(BaseModel):
    id: str
    severity: str
    state: str
    title: str
    what_happened: str
    confidence: Optional[float]
    data_coverage_pct: Optional[float]
    recommended_action: Optional[str]
    priority_rank: Optional[float] = None
    correlated_alert_ids: Optional[list[str]] = None
    escalated: bool = False
    kind: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None   # when the check last confirmed this alert still holds
    period_label: Optional[str] = None      # the exact days the figures cover, on the business's calendar

    class Config:
        from_attributes = True

    @model_validator(mode="wrap")
    @classmethod
    def _derive(cls, data, handler):
        out = handler(data)
        ev = getattr(data, "evidence", None) if not isinstance(data, dict) else data.get("evidence")
        out.escalated = bool(isinstance(ev, dict) and ev.get("escalated_at"))
        out.period_label = getattr(data, "period_label", None) if not isinstance(data, dict) else data.get("period_label")
        return out


def _label_periods(db: Session, tenant_id: str, alerts: list) -> list:
    """Say which days each alert is about ("16 Oct to 22 Oct 2026"), so it can be checked against any other screen.
    Detection compares whole days up to the end of yesterday, so this is the evidence window, not 'now'."""
    from datetime import timedelta, timezone as _tz
    from app.core.tenant_time import tenant_zone
    tz = tenant_zone(db, tenant_id)
    day = lambda d: d.replace(tzinfo=_tz.utc).astimezone(tz)
    for a in alerts:
        if a.period_start and a.period_end and a.period_end > a.period_start:
            s, e = day(a.period_start), day(a.period_end - timedelta(microseconds=1))
            a.period_label = f"{s:%-d %b} to {e:%-d %b %Y}" if (s.date() != e.date()) else f"{s:%-d %b %Y}"
    return alerts


class AlertDetailOut(BaseModel):
    """Section 35: full alert detail page shape."""
    id: str
    severity: str
    state: str
    title: str

    what_happened: str
    when: dict                      # period_start / period_end
    where: Optional[dict]
    who_what_affected: Optional[dict]

    evidence: dict
    related_variables: Optional[dict]
    possible_drivers: list[dict]    # possible_explanations, excluding ruled-out entries
    ruled_out: list[dict]           # possible_explanations entries explicitly ruled out

    ai_summary: Optional[str]        # Section 28-30 natural-language narrative, if generated
    recommended_action: Optional[str]  # prefers AI recommendation, falls back to the deterministic one

    confidence: Optional[float]
    data_coverage_pct: Optional[float]

    historical_experience: list[dict]  # Section 36: past cases of the same kind of problem
    priority_rank: Optional[float] = None
    correlated_alert_ids: list[str] = []
    related: list[dict] = []            # id + title of each related open alert, so the page can name them
    escalated: bool = False
    kind: Optional[str] = None
    external_context: list[dict] = []   # Section 42: holidays/promotions overlapping the period
    advisory: str = ""                  # Section 56

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StateUpdate(BaseModel):
    state: AlertState


@router.get("/", response_model=list[AlertOut])
def list_alerts(
    tenant_id: str,
    severity: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    authorize_tenant(authorization, tenant_id)
    q = db.query(Alert).filter(Alert.tenant_id == tenant_id)
    if severity:
        q = q.filter(Alert.severity == severity)
    if state == "open":                    # still needs a person: not resolved, dismissed or marked a false alarm
        q = q.filter(Alert.state.in_(ACTIVE_STATES))
    elif state == "closed":
        q = q.filter(Alert.state.in_(CLOSED_STATES))
    elif state:
        q = q.filter(Alert.state == state)
    return _label_periods(db, tenant_id, q.order_by(Alert.priority_rank.desc().nullslast(), Alert.created_at.desc()).limit(limit).all())


@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return _label_periods(db, tenant_id, [_get_owned_alert(db, alert_id, tenant_id)])[0]


@router.get("/{alert_id}/detail", response_model=AlertDetailOut)
def get_alert_detail(alert_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Section 35: the full alert detail page — everything built across
    detection, explanation, coverage, and reasoning, in the structured
    shape a Control Room frontend renders directly.
    """
    authorize_tenant(authorization, tenant_id)
    alert = _get_owned_alert(db, alert_id, tenant_id)

    related = []
    if alert.correlated_alert_ids:
        rows = db.query(Alert.id, Alert.title).filter(Alert.tenant_id == tenant_id, Alert.id.in_(alert.correlated_alert_ids)).all()
        related = [{"id": i, "title": t} for i, t in rows]
    explanations = alert.possible_explanations or []
    possible_drivers = [e for e in explanations if not e.get("driver", "").endswith("(ruled out)")]
    ruled_out = [e for e in explanations if e.get("driver", "").endswith("(ruled out)")]

    return AlertDetailOut(
        id=alert.id,
        severity=alert.severity.value,
        state=alert.state.value,
        title=alert.title,
        what_happened=alert.what_happened,
        when={"period_start": alert.period_start, "period_end": alert.period_end},
        where=alert.where,
        who_what_affected=alert.who_what_affected,
        evidence=alert.evidence or {},
        related_variables=alert.related_variables,
        possible_drivers=possible_drivers,
        ruled_out=ruled_out,
        ai_summary=alert.ai_summary,
        recommended_action=alert.ai_recommended_action or alert.recommended_action,
        confidence=float(alert.confidence) if alert.confidence is not None else None,
        data_coverage_pct=float(alert.data_coverage_pct) if alert.data_coverage_pct is not None else None,
        priority_rank=float(alert.priority_rank) if alert.priority_rank is not None else None,
        correlated_alert_ids=alert.correlated_alert_ids or [],
        related=related,
        escalated=bool((alert.evidence or {}).get("escalated_at")),
        historical_experience=historical_experience(db, tenant_id, alert),
        kind=alert.kind,
        external_context=(alert.evidence or {}).get("external_context") or [],
        advisory=advisory_text(getattr(db.get(Tenant, tenant_id), "language", "en")),
        created_at=alert.created_at,
        updated_at=alert.updated_at,
    )


@router.patch("/{alert_id}/state", response_model=AlertOut)
def update_alert_state(
    alert_id: str, tenant_id: str, payload: StateUpdate, request: Request, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Section 35 action buttons map to this: Investigate / We're doing this / Already handled / Not doing this."""
    me = authorize_tenant(authorization, tenant_id)
    alert = _get_owned_alert(db, alert_id, tenant_id)
    before = alert.state.value
    alert.state = payload.state
    db.commit()
    db.refresh(alert)
    audit.record(db, "alert.state", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=alert_id,
                 detail={"from": before, "to": alert.state.value})
    return alert


@router.post("/{alert_id}/reason")
def reason_alert(
    alert_id: str, tenant_id: str, force: bool = False, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Section 28-30: generate AI narrative + recommendation. Severity-gated
    and cached (Section 54) — see app/reasoning/llm_reasoning.py.
    """
    authorize_tenant(authorization, tenant_id)
    alert = _get_owned_alert(db, alert_id, tenant_id)
    try:
        return reason_about_alert(db, alert, force=force)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
