"""Money at stake: what an open alert is costing the owner right now, in the business's own currency.

Only alerts whose evidence already holds two honest figures (what it is now, what it usually is) get an amount, and
the amount is simply the gap between them:
  sales or spending against its usual      -> how far it is off usual, in money
  a category's spending against its usual  -> how much more was spent
  a branch's sales against its usual       -> how much less it sold
  regular customers who have gone quiet    -> what they usually spend in a month
Forecasts, trends, silence, concentration and payments get no amount: a prediction or a risk is not a loss, and adding
them in would inflate the total. An amount is a size of the problem, never a promise of what can be won back.
Nothing here writes anything; it only reads alerts, so it can never change what an alert says or when it closes."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.detection import ACTIVE_STATES
from app.models.alerts import Alert, AlertState


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def at_stake(kind: Optional[str], evidence: Optional[dict]) -> Optional[float]:
    """The money gap behind an alert, or None when the alert is not about a measurable gap. Never raises."""
    ev = evidence if isinstance(evidence, dict) else {}
    if kind == "customers":
        gap = _num(ev.get("monthly_value"))
    elif kind in ("deviation", "category", "branch"):
        metric = str(ev.get("metric") or "")
        if not metric.endswith(".amount"):                      # a count or a quantity is not money
            return None
        now, usual = _num(ev.get("current")), _num(ev.get("usual") if kind != "deviation" else ev.get("baseline"))
        if now is None or usual is None:
            return None
        if kind == "category":
            gap = now - usual                                    # spent more than usual
        elif kind == "branch":
            gap = usual - now                                    # sold less than usual
        else:
            gap = (now - usual) if ev.get("direction") == "rise" else (usual - now)
    else:
        return None
    return round(gap, 2) if gap is not None and gap > 0 else None


def _when(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def summarize(db: Session, tenant_id: str, days: int = 30, now: Optional[datetime] = None) -> dict:
    """What is open right now, and what the last `days` days looked like. Counts only; the page words it."""
    now = now or utcnow()
    since = now - timedelta(days=days)
    open_rows = db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE_STATES)).all()
    amounts = [a for a in (at_stake(r.kind, r.evidence) for r in open_rows) if a is not None]
    raised = db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.created_at >= since).all()
    raised_amounts = [a for a in (at_stake(r.kind, r.evidence) for r in raised) if a is not None]
    closed = db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state == AlertState.resolved, Alert.updated_at >= since).all()
    back = sum(1 for r in closed if (w := _when((r.evidence or {}).get("recovered_at"))) is not None and w >= since)    # closed by the system because it stopped being true
    return {
        "days": days,
        "open_count": len(open_rows),
        "open_measured": len(amounts),
        "open_at_stake": round(sum(amounts), 2),
        "raised": len(raised),
        "raised_at_stake": round(sum(raised_amounts), 2),
        "back_to_normal": back,
    }
