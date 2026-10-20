"""What each connected source contributes to the one shared dataset, and removing a source together with its data."""
from datetime import timezone

from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.core.cache import invalidate_tenant
from app.core.tenant_time import tenant_zone
from app.models.core import BusinessEvent as E, Connector


_PLAIN = {
    "event timestamp is in the future": "the date is in the future",
    "negative amount on sale/expense — check source mapping": "the amount is negative (check the column mapping, or whether this is a refund)",
}


def rejected(db: Session, connector: Connector, limit: int = 50) -> list[dict]:
    """The records a source sent that were left out of every total, newest first, with the reason in plain words."""
    tz = tenant_zone(db, connector.tenant_id)
    rows = (db.query(E.timestamp, E.amount, E.location, E.product_id, E.entity, E.validation_errors)
            .filter(E.connector_id == connector.id, E.validation_status == "failed").order_by(E.timestamp.desc()).limit(limit))
    return [{"when": t.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M"), "amount": float(a) if a is not None else None,
             "branch": loc, "product": prod, "entity": entity, "reasons": [_PLAIN.get(e, e) for e in (errs or [])]}
            for t, a, loc, prod, entity, errs in rows]


def rejected_summary(db: Session, connector: Connector) -> dict:
    """How many records of a source were left out, and the first few with the reason, for a sync message."""
    count = db.query(func.count(E.id)).filter(E.connector_id == connector.id, E.validation_status == "failed").scalar()
    return {"rejected_total": count, "rejected_examples": rejected(db, connector, 3) if count else []}


def contributions(db: Session, tenant_id: str) -> dict[str, dict[str, int]]:
    """Per source: records counted in the totals, records matched to one already counted from another source
    (kept for provenance, not double counted), and records rejected as invalid."""
    rows = (db.query(E.connector_id,
                     func.count(case((E.validation_status == "validated", 1))),
                     func.count(case((E.validation_status.in_(("duplicate", "matched")), 1))),
                     func.count(case((E.validation_status == "failed", 1))))
            .filter(E.tenant_id == tenant_id).group_by(E.connector_id).all())
    return {cid: {"counted": counted, "matched": matched, "rejected": rejected} for cid, counted, matched, rejected in rows}


# A record another source also had takes over as the counted copy (one per record, the rest follow it), so removing
# records never makes the business's totals lose one that a remaining source still holds. `doomed` selects the ids.
_PROMOTE = """
WITH firsts AS (
    SELECT DISTINCT ON (duplicate_of) id AS new_id, duplicate_of AS old_id FROM business_events
    WHERE duplicate_of IN ({doomed})
    ORDER BY duplicate_of, imported_at, id),
promoted AS (
    UPDATE business_events SET validation_status = 'validated', duplicate_of = NULL
    WHERE id IN (SELECT new_id FROM firsts) RETURNING id)
UPDATE business_events d SET duplicate_of = f.new_id FROM firsts f WHERE d.duplicate_of = f.old_id AND d.id <> f.new_id
"""
_DROP_ENTITIES = (
    "DELETE FROM entity_aliases WHERE tenant_id = :t AND source = :s "
    "AND NOT EXISTS (SELECT 1 FROM business_events WHERE tenant_id = :t AND source = :s)",
    "DELETE FROM resolved_entities r WHERE r.tenant_id = :t AND NOT EXISTS (SELECT 1 FROM entity_aliases a WHERE a.entity_id = r.id) "
    "AND NOT EXISTS (SELECT 1 FROM business_events e WHERE e.resolved_customer_id = r.id) "
    "AND NOT EXISTS (SELECT 1 FROM business_events e WHERE e.resolved_product_id = r.id) "
    "AND NOT EXISTS (SELECT 1 FROM business_events e WHERE e.resolved_employee_id = r.id) "
    "AND NOT EXISTS (SELECT 1 FROM business_events e WHERE e.resolved_supplier_id = r.id)",
)
# With no data left, what was derived from it (alerts and their follow-ups, coverage) has nothing to stand on.
_CLEAR_DERIVED = (
    "DELETE FROM notification_deliveries WHERE tenant_id = :t",
    "DELETE FROM cases WHERE tenant_id = :t",
    "DELETE FROM alerts WHERE tenant_id = :t",
    "DELETE FROM data_coverage WHERE tenant_id = :t",
)


def delete_events(db: Session, tenant_id: str, event_ids: list[str], batch: int = 5000) -> None:
    """Delete records that left their source or were edited there. Records another source also held take over as
    the counted copy first. Does not commit: the caller commits together with what replaces them."""
    for i in range(0, len(event_ids), batch):
        part = {"ids": event_ids[i:i + batch], "t": tenant_id}
        db.execute(text(_PROMOTE.format(doomed="SELECT unnest(CAST(:ids AS uuid[]))")), part)
        db.execute(text("DELETE FROM business_events WHERE tenant_id = :t AND id = ANY(CAST(:ids AS uuid[]))"), part)


def remove_source(db: Session, connector: Connector) -> dict:
    """Disconnect: delete the source and every record it brought in. (To only stop it for a while, pause it instead.)"""
    tenant_id = connector.tenant_id
    p = {"t": tenant_id, "c": connector.id, "s": connector.name}
    removed = db.query(func.count(E.id)).filter(E.connector_id == connector.id).scalar()
    db.execute(text(_PROMOTE.format(doomed="SELECT id FROM business_events WHERE connector_id = :c")), p)
    db.execute(text("DELETE FROM business_events WHERE connector_id = :c"), p)
    for sql in _DROP_ENTITIES:
        db.execute(text(sql), p)
    if not db.query(E.id).filter(E.tenant_id == tenant_id).first():
        for sql in _CLEAR_DERIVED:
            db.execute(text(sql), p)
    db.execute(text("DELETE FROM connectors WHERE id = :c"), p)
    db.expunge(connector)                  # its row is gone; the session must not try to refresh it
    db.commit()
    invalidate_tenant(tenant_id)
    return {"removed_records": removed}
