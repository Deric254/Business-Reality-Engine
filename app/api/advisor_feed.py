"""A short feed of serious alerts for the person who set a business up, readable by their CRM. It is deliberately narrow:

* Only businesses that were licensed from a lead in the owner's CRM (the licence carries that lead's reference) AND whose own owner switched
  "Tell my DericBI advisor about serious problems" on (off by default; the business can switch it off again at any moment).
* Only critical and high alerts that are still open, and only the headline: no records, no sales or customer data, no explanations.
* Read-only, behind a key set for the whole deployment (ADVISOR_FEED_KEY). If that is not set the feed does not exist (404).
"""
import hmac
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import get_db
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import Tenant
from app.models.licence import Licence

router = APIRouter()

SHARED_SEVERITIES = (AlertSeverity.critical, AlertSeverity.high)
CLOSED_STATES = (AlertState.resolved, AlertState.dismissed, AlertState.false_positive)
MAX_ITEMS = 100


@router.get("/alerts")
def shared_alerts(since: Optional[str] = None, db: Session = Depends(get_db), x_advisor_key: Optional[str] = Header(default=None)):
    """Alerts raised after `since` (an ISO time; omit for the last 7 days), oldest first, at most 100. Ask again with the last `at` you saw."""
    key = get_settings().ADVISOR_FEED_KEY
    if not key or not x_advisor_key or not hmac.compare_digest(x_advisor_key, key):
        raise HTTPException(404, "Not found")
    try:
        start = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None) if since else None
    except ValueError:
        raise HTTPException(400, "since must be an ISO date and time")
    start = start or (utcnow() - timedelta(days=7))
    rows = (db.query(Alert, Tenant, Licence)
            .join(Tenant, Tenant.id == Alert.tenant_id)
            .join(Licence, Licence.tenant_id == Tenant.id)
            .filter(Licence.status == "active", Licence.source_ref.isnot(None), Tenant.share_alerts_with_advisor.is_(True),
                    Alert.severity.in_(SHARED_SEVERITIES), Alert.state.notin_(CLOSED_STATES), Alert.created_at > start)
            .order_by(Alert.created_at).limit(MAX_ITEMS).all())
    return {"alerts": [{"id": a.id, "ref": lic.source_ref, "business": t.name, "severity": a.severity.value, "headline": a.title, "at": a.created_at.isoformat()}
                       for a, t, lic in rows]}
