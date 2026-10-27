"""
A tracked source is a mirror of what is in it NOW, not a stream of arrivals. Someone edits a figure in their sheet,
adds a row, deletes one: the next sync must show exactly that. So each sync compares the source with what is stored:

- a record not stored yet is added;
- a record whose fingerprint changed (edited at the source) is replaced;
- a record that is stored but no longer in the source is removed (only when the source returns its WHOLE content);
- everything else is left alone, and is not even re-sent to the database.

Safeguard: if a sync would change more than half of a sizeable source (the link now points at another tab, someone
emptied the sheet) nothing is touched and the owner is asked to confirm, because deleting a business's history on a
wrong link is the worse mistake.
"""
from fastapi import HTTPException
from sqlalchemy import bindparam, update
from sqlalchemy.orm import Session

from app.core import sources
from app.core.cache import invalidate_tenant
from app.core.ingestion import NormalizedEvent, ingest_events_bulk, localize, source_fingerprint
from app.models.core import BusinessEvent as E, Connector

BIG_CHANGE_MIN_RECORDS, BIG_CHANGE_SHARE = 20, 0.5
CONFIRM_MARKER = "This source changed a lot"       # the page looks for this to offer "replace"


def apply(db: Session, connector: Connector, payloads: list[NormalizedEvent], *, remove_missing: bool, confirmed: bool = False) -> dict:
    """Bring the connector's stored records in line with `payloads`, the source's current content."""
    localize(db, payloads)
    incoming: dict[tuple, str] = {}
    for p in payloads:
        if p.entity_id:
            incoming.setdefault((p.entity, p.entity_id), source_fingerprint(p))
    stored = db.query(E.id, E.entity, E.entity_id, E.source_hash, E.validation_status, E.validation_errors).filter(E.connector_id == connector.id, E.entity_id.isnot(None))
    if not remove_missing:          # only some records were sent (a push): compare just those, not the whole source
        stored = stored.filter(E.entity_id.in_([eid for _, eid in incoming]))
    existing = {(entity, eid): (rid, h, status, errs) for rid, entity, eid, h, status, errs in stored}
    changed = [k for k, h in incoming.items() if k in existing and existing[k][1] not in (None, h)]
    unlabeled = [{"b_id": existing[k][0], "b_hash": h} for k, h in incoming.items() if k in existing and existing[k][1] is None]
    removed = [v[0] for k, v in existing.items() if k not in incoming] if remove_missing else []

    if remove_missing and not confirmed and len(existing) >= BIG_CHANGE_MIN_RECORDS \
            and len(removed) + len(changed) > BIG_CHANGE_SHARE * len(existing):
        raise HTTPException(409, f"{CONFIRM_MARKER}: {len(removed)} of its {len(existing):,} records are no longer there and {len(changed)} were edited. "
                                 "Nothing was changed. If the link points at the right place, replace the data with what it holds now.")

    sources.delete_events(db, connector.tenant_id, [existing[k][0] for k in changed] + removed)
    if unlabeled:                                          # records stored before fingerprints existed: label them, do not replace them
        table = E.__table__
        db.execute(update(table).where(table.c.id == bindparam("b_id")).values(source_hash=bindparam("b_hash")), unlabeled)
    replaced = set(changed)
    unchanged = lambda p: bool(p.entity_id) and (p.entity, p.entity_id) in existing and (p.entity, p.entity_id) not in replaced
    arrived = iter(ingest_events_bulk(db, [p for p in payloads if not unchanged(p)]))
    results = [({"id": existing[(p.entity, p.entity_id)][0], "validation_status": existing[(p.entity, p.entity_id)][2],
                 "errors": existing[(p.entity, p.entity_id)][3] or [], "entity_review_flags": [], "duplicate": True, "merged": False}
                if unchanged(p) else next(arrived)) for p in payloads]               # one result per row, in the order given
    db.commit()
    invalidate_tenant(connector.tenant_id)
    return {"results": results, "new_rows": sum(1 for r in results if not r["duplicate"]) - len(changed), "updated_rows": len(changed),
            "removed_rows": len(removed), "duplicates": sum(1 for r in results if r["duplicate"]),
            "failed": sum(1 for r in results if r["errors"]), **sources.rejected_summary(db, connector)}
