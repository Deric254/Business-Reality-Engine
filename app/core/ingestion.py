"""
Shared event ingestion logic (Section 11-12).
Every connector type — JSON API, file upload, future DB/webhook connectors —
must go through this same function. This is what keeps validation and
tenant-isolation rules consistent across every ingestion path instead of
each connector re-implementing (and potentially drifting on) the checks.
"""
import uuid
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.models.core import BusinessEvent, Connector, Tenant
from app.core.fx import convert_event_fields
from app.core.entity_resolution import resolve_entities_bulk


class NormalizedEvent(BaseModel):
    """Internal representation — mirrors the Universal Data Contract (Section 10)."""
    tenant_id: str
    connector_id: str
    source: str
    entity: str
    entity_id: Optional[str] = None
    event_type: str
    timestamp: datetime
    amount: Optional[float] = None
    quantity: Optional[float] = None
    currency: Optional[str] = None
    location: Optional[str] = None
    customer_id: Optional[str] = None
    product_id: Optional[str] = None
    employee_id: Optional[str] = None
    supplier_id: Optional[str] = None
    metadata_json: dict = {}


# Maps a raw ID field on the event to (entity_type, resolved-column name).
# Section 13/6: each role resolves independently — a sale can reference a
# customer, a product, AND a supplier in the same row.
_RESOLVABLE_ROLES = [
    ("customer_id", "customer", "resolved_customer_id"),
    ("product_id", "product", "resolved_product_id"),
    ("employee_id", "employee", "resolved_employee_id"),
    ("supplier_id", "supplier", "resolved_supplier_id"),
]


def _validate_event(event) -> list[str]:
    """Section 12 minimal inline checks. Extend here, not per-connector.
    Accepts anything exposing entity/amount/timestamp/entity_id (payload or row)."""
    errors = []
    if event.amount is not None and event.amount < 0 and event.entity in {"sale", "expense"}:
        errors.append("negative amount on sale/expense — check source mapping")
    if event.timestamp > datetime.utcnow():
        errors.append("event timestamp is in the future")
    if event.entity_id and len(event.entity_id) > 255:
        errors.append("entity_id exceeds max length")
    return errors


def _naive_utc(ts: datetime) -> datetime:
    """Events are stored as naive UTC. A tz-aware timestamp is converted, not rejected."""
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _check_connectors(db: Session, payloads: list[NormalizedEvent]) -> dict[str, Connector]:
    """Tenant/connector boundary (Section 48), checked for EVERY connector in
    the batch before anything is written: a connector can only ingest events
    for the tenant it belongs to."""
    ids = {p.connector_id for p in payloads}
    connectors = {c.id: c for c in db.query(Connector).filter(Connector.id.in_(ids))}
    for p in payloads:
        connector = connectors.get(p.connector_id)
        if not connector:
            raise HTTPException(404, "Connector not found")
        if connector.tenant_id != p.tenant_id:
            # Do not leak *why* in detail — just refuse. Tenant-isolation boundary.
            raise HTTPException(403, "Connector does not belong to the specified tenant")
        if not connector.is_active:
            raise HTTPException(409, "Connector is not active")
    return connectors


def _ingest_chunk(db: Session, chunk: list[NormalizedEvent], connectors: dict[str, Connector]) -> list[dict]:
    """One transaction: resolve entities (each distinct value once), bulk
    INSERT ... ON CONFLICT DO NOTHING, commit. Duplicates are detected by the
    database's unique index — correct under concurrency, with no per-row
    pre-check query."""
    # Section 13: resolve every distinct (type, source, raw value) in the chunk
    # in ONE batched pass; a review flag attaches to the first event that used the value.
    wanted: list[tuple] = []
    for p in chunk:
        for raw_field, entity_type, _ in _RESOLVABLE_ROLES:
            raw_value = getattr(p, raw_field)
            if raw_value and raw_value.strip():
                wanted.append((p.tenant_id, entity_type, p.source, raw_value.strip()))
    resolved = resolve_entities_bulk(db, wanted)
    flagged: set[tuple] = set()
    flags_by_idx: list[list[dict]] = [[] for _ in chunk]
    resolved_cols: list[dict] = [{} for _ in chunk]
    for i, p in enumerate(chunk):
        for raw_field, entity_type, resolved_field in _RESOLVABLE_ROLES:
            raw_value = getattr(p, raw_field)
            if not raw_value or not raw_value.strip():
                continue
            key = (p.tenant_id, entity_type, p.source, raw_value.strip())
            res = resolved[key]
            if res.needs_review and key not in flagged:
                flagged.add(key)
                flags_by_idx[i].append({"field": raw_field, "value": raw_value, "suggestions": res.suggestions})
            resolved_cols[i][resolved_field] = res.resolved_entity_id

    rows, errors_by_idx = [], []
    bases = {t.id: t.base_currency for t in db.query(Tenant).filter(Tenant.id.in_({p.tenant_id for p in chunk}))}
    fx_cache: dict = {}
    for i, p in enumerate(chunk):
        data = p.model_dump()
        data["timestamp"] = _naive_utc(p.timestamp)
        errors = _validate_event(SimpleNamespace(**data))
        # Section 57: convert to the tenant's base currency (or flag when no rate exists).
        amt, cur, meta, fx_err = convert_event_fields(
            db, bases.get(p.tenant_id, "KES"), p.tenant_id, data["amount"], data["currency"], data["timestamp"], fx_cache)
        data["amount"], data["currency"] = amt, cur
        if meta:
            data["metadata_json"] = {**(data.get("metadata_json") or {}), **meta}
        if fx_err:
            errors.append(fx_err)
        errors_by_idx.append(errors)
        rows.append({
            **data, **resolved_cols[i], "id": str(uuid.uuid4()),
            "validation_status": "failed" if errors else "validated",
            "validation_errors": errors or None,
        })
    # Uniform keys across rows (required for executemany).
    all_keys = {k for r in rows for k in r}
    for r in rows:
        for k in all_keys:
            r.setdefault(k, None)

    stmt = (
        pg_insert(BusinessEvent)
        .on_conflict_do_nothing(
            index_elements=[BusinessEvent.connector_id, BusinessEvent.entity, BusinessEvent.entity_id],
            index_where=BusinessEvent.entity_id.isnot(None),
        )
        .returning(BusinessEvent.id)
    )
    inserted = {r[0] for r in db.execute(stmt, rows)}

    # Duplicates: fetch the rows that already existed (one query for the chunk).
    dup_idx = [i for i, r in enumerate(rows) if r["id"] not in inserted]
    existing = {}
    if dup_idx:
        keys = {(chunk[i].connector_id, chunk[i].entity, chunk[i].entity_id) for i in dup_idx}
        for ev in db.query(BusinessEvent).filter(
            tuple_(BusinessEvent.connector_id, BusinessEvent.entity, BusinessEvent.entity_id).in_(list(keys))
        ):
            existing[(ev.connector_id, ev.entity, ev.entity_id)] = ev

    results: list[dict] = []
    touched: dict[str, bool] = {}   # connector_id -> any validation error among inserted rows
    for i, p in enumerate(chunk):
        if rows[i]["id"] in inserted:
            results.append({
                "id": rows[i]["id"], "validation_status": rows[i]["validation_status"],
                "errors": errors_by_idx[i], "entity_review_flags": flags_by_idx[i], "duplicate": False,
            })
            touched[p.connector_id] = touched.get(p.connector_id, False) or bool(errors_by_idx[i])
        else:
            ev = existing.get((p.connector_id, p.entity, p.entity_id))
            if ev is None:  # conflict that wasn't our dedup key: surface it, don't mask it
                raise RuntimeError("insert skipped but no existing row found")
            results.append({
                "id": ev.id, "validation_status": ev.validation_status,
                "errors": ev.validation_errors or [], "entity_review_flags": [], "duplicate": True,
            })

    now = datetime.utcnow()
    for cid, had_errors in touched.items():
        connectors[cid].last_sync_at = now
        connectors[cid].last_sync_status = "partial" if had_errors else "success"
    db.commit()
    return results


def ingest_events_bulk(db: Session, payloads: list[NormalizedEvent], chunk_size: int = 1000) -> list[dict]:
    """
    THE ingestion entry point (Section 11-12): every connector, the events
    API and the webhook go through it, so validation and tenant isolation
    cannot drift between paths. Results are returned in input order.

    Idempotent when entity_id is supplied: duplicates (same connector +
    entity + entity_id) are skipped by the database's unique index and
    reported with duplicate=True — safe for retries and concurrent senders.
    Not idempotent without entity_id (no natural key exists).

    All connectors are checked before anything is written. Each chunk of
    `chunk_size` rows is one transaction; if a chunk fails, earlier chunks
    stay committed and re-sending the whole batch is safe (idempotent).
    """
    if not payloads:
        return []
    connectors = _check_connectors(db, payloads)
    results: list[dict] = []
    for start in range(0, len(payloads), chunk_size):
        try:
            results.extend(_ingest_chunk(db, payloads[start:start + chunk_size], connectors))
        except Exception:
            db.rollback()
            raise
    return results


def ingest_normalized_event(db: Session, payload: NormalizedEvent) -> dict:
    """Single-event convenience wrapper over the bulk path (one code path)."""
    return ingest_events_bulk(db, [payload])[0]
