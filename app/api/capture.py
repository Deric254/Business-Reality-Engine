"""
Human-assisted capture (Section 8-K): the last resort where no machine-readable access exists. A person types what the
paper ledger / till tape / old screen shows. Entries go through the SAME ingestion and validation as every other source,
are marked as human-entered (so confidence in them can be judged), and carry who entered them.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.models.core import Connector
from app.core.clock import utcnow

router = APIRouter()
MAX_ENTRIES = 100
ENTITIES = {"sale", "expense"}
CONNECTOR_NAME = "Manual capture"


class Entry(BaseModel):
    entity: str
    amount: float = Field(gt=0, lt=1e12)
    timestamp: Optional[datetime] = None          # when it happened; default: now
    quantity: Optional[float] = Field(default=None, gt=0, lt=1e12)
    currency: Optional[str] = Field(default=None, max_length=3)
    location: Optional[str] = Field(default=None, max_length=200)
    customer: Optional[str] = Field(default=None, max_length=200)
    product: Optional[str] = Field(default=None, max_length=200)
    supplier: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = Field(default=None, max_length=500)
    client_ref: Optional[str] = Field(default=None, max_length=100)   # lets a retry of the same entry be recognised


class CaptureIn(BaseModel):
    tenant_id: str
    entries: list[Entry] = Field(min_length=1, max_length=MAX_ENTRIES)


def _connector(db: Session, tenant_id: str) -> Connector:
    c = db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.connector_type == "manual").first()
    if not c:
        c = Connector(tenant_id=tenant_id, name=CONNECTOR_NAME, connector_type="manual", expected_frequency="slow", is_active=True, config={})
        db.add(c); db.commit(); db.refresh(c)
    return c


@router.post("/")
def capture(payload: CaptureIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id)          # owner or staff may enter data
    conn = _connector(db, payload.tenant_id)
    if not conn.is_active:
        raise HTTPException(409, "Manual capture is switched off for this business")
    now = utcnow()
    events = []
    for e in payload.entries:
        if e.entity not in ENTITIES:
            raise HTTPException(400, f"entity must be one of {sorted(ENTITIES)}")
        ts = e.timestamp
        if ts is not None and ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        events.append(NormalizedEvent(
            tenant_id=payload.tenant_id, connector_id=conn.id, source=conn.name, entity=e.entity, event_type=f"{e.entity}.captured",
            entity_id=f"manual:{me.user_id}:{e.client_ref}" if e.client_ref else None, timestamp=ts or now, amount=e.amount,
            quantity=e.quantity, currency=(e.currency.upper() if e.currency else None), location=(e.location or "").strip() or None,
            customer_id=(e.customer or "").strip() or None, product_id=(e.product or "").strip() or None,
            supplier_id=(e.supplier or "").strip() or None,
            metadata_json={"human_entered": True, "entered_by": me.user_id, "note": e.note}))
    results = ingest_events_bulk(db, events)
    audit.record(db, "capture.entries", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id, target=conn.id,
                 detail={"count": len(results)})
    return {"entered": len(results), "duplicates": sum(1 for r in results if r["duplicate"]),
            "failed_validation": sum(1 for r in results if r["errors"]), "results": results}
