from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.ingestion import NormalizedEvent, ingest_events_bulk, ingest_normalized_event
from app.connectors.webhook_connector import verify_webhook_token, WebhookMapping, webhook_payload_to_event_dict
from app.core.rate_limit import check_webhook_rate_limit
from app.models.core import Connector

router = APIRouter()
PUSH_TYPES = ('webhook', 'local_agent')
MAX_BATCH = 500
MAX_BODY_BYTES = 5 * 1024 * 1024


@router.post("/{connector_id}/{token}")
async def receive_webhook(connector_id: str, token: str, request: Request):
    """
    Deliberately NOT behind require_api_key (see app/main.py — this router
    is mounted without that dependency). An external system posting a
    webhook has no way to know our service API key; its credential is the
    per-connector token in the URL itself, verified below.

    Rate limited by connector_id, not source IP (SECURITY.md item 3) — one
    external system's traffic never affects another's even from behind
    the same NAT/hosting provider. Checked explicitly here rather than via
    a route decorator — see app/core/rate_limit.py for why the decorator-
    based approach doesn't reliably work in this environment.
    """
    check_webhook_rate_limit(connector_id)
    db: Session = SessionLocal()
    try:
        connector = db.query(Connector).filter(Connector.id == connector_id).first()
        if not connector or connector.connector_type not in PUSH_TYPES:
            # Same non-existent-vs-wrong-type response either way — don't
            # help an attacker distinguish "no such connector" from
            # "wrong type", same principle as the 404-not-403 pattern
            # used everywhere else in this app.
            raise HTTPException(404, "Not found")

        stored_hash = (connector.config or {}).get("webhook_token_hash")
        if not stored_hash or not verify_webhook_token(token, stored_hash):
            raise HTTPException(404, "Not found")

        if not connector.is_active:
            raise HTTPException(409, "Connector is not active")

        try:
            mapping = WebhookMapping(**{k: v for k, v in connector.config.items() if k != "webhook_token_hash"})
        except Exception as e:
            raise HTTPException(500, f"Webhook mapping is misconfigured: {e}")

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "Payload must be a JSON object")

        event_dict = webhook_payload_to_event_dict(
            payload, mapping, tenant_id=connector.tenant_id, connector_id=connector.id,
            source=connector.name, received_at=datetime.utcnow(),
        )
        result = ingest_normalized_event(db, NormalizedEvent(**event_dict))
        return result
    finally:
        db.close()


@router.post("/{connector_id}/{token}/batch")
async def receive_batch(connector_id: str, token: str, request: Request):
    """Local agent / bulk senders: {"events": [<payload in the source system's own shape>, ...]}, up to 500 per call.
    Each item is mapped with the connector's mapping exactly like a single webhook. Items that cannot be mapped are
    reported by index and do not stop the rest; duplicates (same entity_id) are skipped idempotently, so resending is safe."""
    check_webhook_rate_limit(connector_id)
    db: Session = SessionLocal()
    try:
        connector = db.query(Connector).filter(Connector.id == connector_id).first()
        if not connector or connector.connector_type not in PUSH_TYPES:
            raise HTTPException(404, "Not found")
        stored_hash = (connector.config or {}).get("webhook_token_hash")
        if not stored_hash or not verify_webhook_token(token, stored_hash):
            raise HTTPException(404, "Not found")
        if not connector.is_active:
            raise HTTPException(409, "Connector is not active")
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "Batch too large")
        try:
            body = __import__("json").loads(raw)
        except ValueError:
            raise HTTPException(400, "Body must be JSON")
        items = body.get("events") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise HTTPException(400, 'Body must be {"events": [...]}')
        if len(items) > MAX_BATCH:
            raise HTTPException(413, f"At most {MAX_BATCH} events per batch")
        try:
            mapping = WebhookMapping(**{k: v for k, v in connector.config.items() if k != "webhook_token_hash"})
        except Exception as e:
            raise HTTPException(500, f"Mapping is misconfigured: {e}")
        events, rejected, index_map = [], [], []
        now = datetime.utcnow()
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                rejected.append({"index": i, "error": "each event must be a JSON object"})
                continue
            try:
                events.append(NormalizedEvent(**webhook_payload_to_event_dict(
                    item, mapping, tenant_id=connector.tenant_id, connector_id=connector.id, source=connector.name, received_at=now)))
                index_map.append(i)
            except HTTPException as e:
                rejected.append({"index": i, "error": e.detail})
            except Exception as e:
                rejected.append({"index": i, "error": f"invalid event: {type(e).__name__}"})
        results = ingest_events_bulk(db, events) if events else []
        connector.last_attempt_at = now
        db.commit()
        return {"received": len(items), "accepted": len(results), "duplicates": sum(1 for r in results if r["duplicate"]),
                "failed_validation": sum(1 for r in results if r["errors"] and not r["duplicate"]), "rejected": rejected}
    finally:
        db.close()
