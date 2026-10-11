from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.ingestion import NormalizedEvent, ingest_normalized_event
from app.connectors.webhook_connector import verify_webhook_token, WebhookMapping, webhook_payload_to_event_dict
from app.core.rate_limit import check_webhook_rate_limit
from app.models.core import Connector

router = APIRouter()


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
        if not connector or connector.connector_type != "webhook":
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
