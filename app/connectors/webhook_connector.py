"""
Webhook connector (Section 8-B).

External systems posting to a webhook won't have our service API key —
they need their own credential, scoped to exactly one connector, not the
whole API. Generated once at connector creation, shown in plaintext
exactly once (like an API key), stored only as a hash afterward. Losing
it means regenerating, not recovering — same pattern as any API secret.

Real webhook payloads rarely match our Universal Data Contract shape
directly (a payment provider sends its own JSON structure). WebhookMapping
extracts fields via dot-path lookups into whatever the external system
actually sends, the same conceptual role file_connector.py's ColumnMapping
plays for spreadsheet columns.
"""
import hashlib
import hmac
import secrets
from typing import Optional
from fastapi import HTTPException
from pydantic import BaseModel


def generate_webhook_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Raw token is shown to the caller
    exactly once; only the hash is ever stored."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def verify_webhook_token(raw_token: str, stored_hash: str) -> bool:
    """Constant-time comparison — a naive == would leak timing information
    about how many leading characters matched, letting an attacker guess
    the token byte-by-byte over many requests."""
    candidate_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return hmac.compare_digest(candidate_hash, stored_hash)


class WebhookMapping(BaseModel):
    """
    Stored in Connector.config alongside webhook_token_hash. Dot-path
    lookups into the external system's own JSON shape — e.g. a payment
    webhook might need amount_col="data.object.amount".
    """
    entity: str
    event_type: str
    timestamp_path: Optional[str] = None   # dot path; None = use receipt time
    amount_path: Optional[str] = None
    quantity_path: Optional[str] = None
    currency_path: Optional[str] = None
    location_path: Optional[str] = None
    customer_id_path: Optional[str] = None
    product_id_path: Optional[str] = None
    entity_id_path: Optional[str] = None
    default_currency: str = "KES"


def _extract(payload: dict, path: Optional[str]):
    """Dot-notation lookup, e.g. 'data.object.amount' -> payload['data']['object']['amount'].
    Returns None on any missing key rather than raising — a webhook field
    that's sometimes absent shouldn't crash ingestion, just leave that
    field null on the event."""
    if not path:
        return None
    current = payload
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def webhook_payload_to_event_dict(
    payload: dict, mapping: WebhookMapping, tenant_id: str, connector_id: str, source: str, received_at
) -> dict:
    from datetime import datetime

    timestamp = None
    if mapping.timestamp_path:
        raw_ts = _extract(payload, mapping.timestamp_path)
        if raw_ts:
            try:
                timestamp = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                raise HTTPException(400, f"Could not parse timestamp at '{mapping.timestamp_path}': {raw_ts!r}")
    if timestamp is None:
        timestamp = received_at  # Section 11: fall back to receipt time if the payload has none

    def _num(path):
        val = _extract(payload, path)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _str(path):
        val = _extract(payload, path)
        return str(val) if val is not None else None

    return {
        "tenant_id": tenant_id,
        "connector_id": connector_id,
        "source": source,
        "entity": mapping.entity,
        "entity_id": _str(mapping.entity_id_path),
        "event_type": mapping.event_type,
        "timestamp": timestamp,
        "amount": _num(mapping.amount_path),
        "quantity": _num(mapping.quantity_path),
        "currency": _str(mapping.currency_path) or mapping.default_currency,
        "location": _str(mapping.location_path),
        "customer_id": _str(mapping.customer_id_path),
        "product_id": _str(mapping.product_id_path),
        "metadata_json": {},
    }
