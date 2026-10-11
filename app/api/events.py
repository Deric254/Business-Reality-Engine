from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import authorize_tenant_or_service
from app.core.ingestion import NormalizedEvent, ingest_normalized_event, ingest_events_bulk

router = APIRouter()


@router.post("/ingest")
def ingest_event(payload: NormalizedEvent, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None), x_api_key: Optional[str] = Header(default=None)):
    authorize_tenant_or_service(authorization, x_api_key, payload.tenant_id)
    return ingest_normalized_event(db, payload)


@router.post("/ingest/batch")
def ingest_batch(payloads: list[NormalizedEvent], db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None), x_api_key: Optional[str] = Header(default=None)):
    # A token is only valid for one tenant, so a batch mixing tenant_ids
    # would fail the check for at least one of them anyway — checking
    # every distinct tenant_id present up front gives a clear error
    # instead of a confusing partial failure midway through the batch.
    for tenant_id in {p.tenant_id for p in payloads}:
        authorize_tenant_or_service(authorization, x_api_key, tenant_id)

    results = ingest_events_bulk(db, payloads)
    failed = sum(1 for r in results if r["errors"])
    return {"ingested": len(results), "failed": failed, "results": results}
