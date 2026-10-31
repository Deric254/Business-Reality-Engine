"""A short feed of serious alerts for the person who set a business up, readable by their CRM. It is deliberately narrow:

* Only businesses that were licensed from a lead in the owner's CRM (the licence carries that lead's reference) AND whose own owner switched
  "Tell my DericBI advisor about serious problems" on (off by default; the business can switch it off again at any moment).
* Only critical and high alerts that are still open, and only the headline: no records, no sales or customer data, no explanations.
* Read-only, behind a key the owner's CRM made up itself and handed over when the owner opened the licence page from it (see register_key, called by
  the platform owner's signed-in session only). Nothing to copy or configure. Until a CRM has paired, the feed does not exist (404).
"""
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import get_db
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import Tenant
from app.models.licence import AdvisorKey, Licence

router = APIRouter()

SHARED_SEVERITIES = (AlertSeverity.critical, AlertSeverity.high)
CLOSED_STATES = (AlertState.resolved, AlertState.dismissed, AlertState.false_positive)
MAX_ITEMS = 100
MAX_KEYS = 10


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def register_key(db: Session, key: str) -> None:
    """Remember a CRM's key (as a hash). The same key again changes nothing; only the newest few are kept."""
    h = key_hash(key)
    if db.query(AdvisorKey.id).filter(AdvisorKey.key_hash == h).first():
        return
    db.add(AdvisorKey(key_hash=h)); db.flush()
    for old in db.query(AdvisorKey).order_by(AdvisorKey.created_at.desc()).offset(MAX_KEYS).all():
        db.delete(old)
    db.commit()


@router.get("/alerts")
def shared_alerts(since: Optional[str] = None, db: Session = Depends(get_db), x_advisor_key: Optional[str] = Header(default=None)):
    """Alerts raised after `since` (an ISO time; omit for the last 7 days), oldest first, at most 100. Ask again with the last `at` you saw."""
    if not x_advisor_key or not db.query(AdvisorKey.id).filter(AdvisorKey.key_hash == key_hash(x_advisor_key)).first():
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
