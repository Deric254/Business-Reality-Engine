"""
Pull-type connector syncs (Sections 8, 17): database, SFTP, email, shared folder.

One implementation used by both the manual API endpoints and the scheduler, so a scheduled sync behaves exactly like a
manual one. A per-connector advisory lock stops two workers (or a click during a scheduled run) from syncing the same
connector at once. Failures are recorded on the connector (a broken source must be visible, Section 51), never swallowed.
"""
import logging
from datetime import datetime, timedelta
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.connectors.database_connector import DatabaseColumnMapping, fetch_rows, rows_to_normalized_events
from app.connectors.email_connector import list_new_emails
from app.connectors.file_connector import (ColumnMapping, add_row_ids, dataframe_to_normalized_events, parse_file_to_dataframe, validate_upload, with_default_id)
from app.connectors.url_connector import fetch_dataframe
from app.connectors.folder_connector import MAX_TRACKED, list_new_files as list_folder_files, read_file
from app.connectors.sftp_connector import fetch_file, list_new_files as list_sftp_files
from app.core import snapshot
from app.core.locks import try_advisory_lock
from app.core.ingestion import NormalizedEvent
from app.models.core import Connector

logger = logging.getLogger("business_reality_engine")

SYNCABLE = {"database", "url", "sftp", "email", "shared_folder"}
MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES = 5, 10080


def record_sync_failure(db: Session, connector: Connector, exc: HTTPException):
    """A connection-level failure must be visible: otherwise it looks identical to a connector never attempted."""
    connector.last_sync_status = "failed"
    connector.last_sync_at = datetime.utcnow()
    db.commit()
    raise exc


def _mapping(connector: Connector, cls):
    try:
        return cls(**{k: v for k, v in (connector.config or {}).items() if k in cls.model_fields})
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")


def _ingest_frame(db, connector, mapping, content, filename):
    ext = validate_upload(filename, content)
    df = add_row_ids(parse_file_to_dataframe(content, ext))
    mapping = with_default_id(df, mapping)
    parsed = dataframe_to_normalized_events(df, mapping, tenant_id=connector.tenant_id, connector_id=connector.id, source=connector.name)
    done = snapshot.apply(db, connector, [NormalizedEvent(**e) for e in parsed.events], remove_missing=False)
    return {**{k: v for k, v in done.items() if k != "results"}, "rows_processed": len(done["results"]),
            "skipped": len(parsed.skipped), "skipped_examples": parsed.skipped[:3]}


def sync_database(db: Session, connector: Connector) -> dict:
    mapping = _mapping(connector, DatabaseColumnMapping)
    try:
        rows = fetch_rows(connector.config, connector.tenant_id)
    except HTTPException as e:
        record_sync_failure(db, connector, e)
    raw = rows_to_normalized_events(rows, mapping, tenant_id=connector.tenant_id, connector_id=connector.id, source=connector.name)
    done = snapshot.apply(db, connector, [NormalizedEvent(**e) for e in raw], remove_missing=False)   # a query may show only recent rows
    return {"rows_fetched": len(rows), "rows_processed": len(done["results"]), **done}


def sync_sftp(db: Session, connector: Connector) -> dict:
    mapping = _mapping(connector, ColumnMapping)
    try:
        new_files = list_sftp_files(connector.config)
    except HTTPException as e:
        record_sync_failure(db, connector, e)
    tracked = list(connector.config.get("processed_files", []))
    file_results = []
    for f in new_files:
        try:
            content = fetch_file(connector.config, f["filename"])
            file_results.append({"filename": f["filename"], **_ingest_frame(db, connector, mapping, content, f["filename"])})
        except HTTPException as e:
            file_results.append({"filename": f["filename"], "error": e.detail})
        finally:
            # Marked processed on EITHER outcome so a permanently broken file does not retry forever; a corrected
            # file has a new mtime and is picked up automatically.
            tracked.append([f["filename"], f["mtime"]])
    connector.config = {**connector.config, "processed_files": tracked[-MAX_TRACKED:]}
    db.commit()
    return {"files_found": len(new_files), "files": file_results}


def sync_email(db: Session, connector: Connector) -> dict:
    mapping = _mapping(connector, ColumnMapping)
    try:
        new_emails = list_new_emails(connector.config)
    except HTTPException as e:
        record_sync_failure(db, connector, e)
    tracked = list(connector.config.get("processed_message_ids", []))
    out = []
    for msg in new_emails:
        item = {"message_id": msg["message_id"], "subject": msg["subject"], "attachments": []}
        for filename, content in msg["attachments"]:
            try:
                item["attachments"].append({"filename": filename, **_ingest_frame(db, connector, mapping, content, filename)})
            except HTTPException as e:
                item["attachments"].append({"filename": filename, "error": e.detail})
        out.append(item)
        tracked.append(msg["message_id"])
    connector.config = {**connector.config, "processed_message_ids": tracked[-MAX_TRACKED:]}
    db.commit()
    return {"emails_found": len(new_emails), "emails": out}


def sync_folder(db: Session, connector: Connector) -> dict:
    mapping = _mapping(connector, ColumnMapping)
    try:
        items = list_folder_files(connector.tenant_id, connector.config)
    except HTTPException as e:
        record_sync_failure(db, connector, e)
    tracked = list(connector.config.get("processed_hashes", []))
    results = []
    for it in items:
        if it["skip"]:
            results.append({"filename": it["filename"], "error": f"skipped: {it['skip']}"})
            continue
        try:
            results.append({"filename": it["filename"], **_ingest_frame(db, connector, mapping, read_file(it), it["filename"])})
        except HTTPException as e:
            results.append({"filename": it["filename"], "error": e.detail})
        tracked.append(it["hash"])           # a broken file is not retried until its content changes
    connector.config = {**connector.config, "processed_hashes": tracked[-MAX_TRACKED:]}
    db.commit()
    return {"files_found": len(items), "files": results}


def sync_url(db: Session, connector: Connector, confirmed: bool = False) -> dict:
    """A sheet or file link is mirrored: what is edited, added or deleted there is edited, added or deleted here."""
    mapping = _mapping(connector, ColumnMapping)
    try:
        df = add_row_ids(fetch_dataframe(connector.config))
    except HTTPException as e:
        record_sync_failure(db, connector, e)
    mapping = with_default_id(df, mapping)
    parsed = dataframe_to_normalized_events(df, mapping, tenant_id=connector.tenant_id, connector_id=connector.id, source=connector.name)
    done = snapshot.apply(db, connector, [NormalizedEvent(**e) for e in parsed.events],
                          remove_missing=bool(df.attrs.get("whole_file")), confirmed=confirmed)
    return {**{k: v for k, v in done.items() if k != "results"}, "rows_fetched": len(df), "rows_processed": len(done["results"]),
            "skipped": len(parsed.skipped), "skipped_examples": parsed.skipped[:3]}


SYNCERS: dict[str, Callable[..., dict]] = {
    "database": sync_database, "url": sync_url, "sftp": sync_sftp, "email": sync_email, "shared_folder": sync_folder,
}


STALE_SYNC_SECONDS = 600


def run_sync(db: Session, connector: Connector, confirmed: bool = False) -> dict:
    """Locked, recorded sync of one connector. 409 if another sync of it is already running. `confirmed` lets a mirrored
    source apply a very large change (see app.core.snapshot)."""
    fn = SYNCERS.get(connector.connector_type)
    if fn is None:
        raise HTTPException(400, f"Connector type '{connector.connector_type}' cannot be synced by pulling")
    if not connector.is_active:
        raise HTTPException(409, "Connector is not active")
    if confirmed and connector.connector_type != "url":
        raise HTTPException(400, "Only a web link or sheet can be replaced this way")
    with try_advisory_lock(f"sync:{connector.id}") as got:
        running = connector.last_attempt_at is None or (datetime.utcnow() - connector.last_attempt_at).total_seconds() < STALE_SYNC_SECONDS
        if not got and running:
            raise HTTPException(409, "A sync of this connector is already running")
        if not got:           # the last sync started long ago, so the lock is left over (a pooled database connection can keep one): carry on
            logger.warning(f"Ignoring a stale sync lock for connector={connector.id}")
        connector.last_attempt_at = datetime.utcnow()
        db.commit()
        result = fn(db, connector, confirmed=True) if confirmed else fn(db, connector)
        connector.last_sync_at = datetime.utcnow()           # checking and finding nothing new still counts as a successful check
        connector.last_sync_status = "partial" if result.get("failed") else "success"
        db.commit()
        return result


def due_connectors(db: Session, now: datetime) -> list[Connector]:
    rows = db.query(Connector).filter(Connector.is_active == True, Connector.sync_interval_minutes.isnot(None),  # noqa: E712
                                      Connector.connector_type.in_(SYNCABLE)).all()
    return [c for c in rows if c.last_attempt_at is None or now - c.last_attempt_at >= timedelta(minutes=c.sync_interval_minutes)]


def run_due_syncs(db: Session, now: datetime | None = None) -> dict:
    """Scheduler entry: sync every due connector; one failing connector never stops the others."""
    now = now or datetime.utcnow()
    stats = {"due": 0, "ok": 0, "failed": 0, "busy": 0}
    for c in due_connectors(db, now):
        stats["due"] += 1
        try:
            run_sync(db, c)
            stats["ok"] += 1
        except HTTPException as e:
            db.rollback()
            if e.status_code == 409 and "already running" in str(e.detail):
                stats["busy"] += 1
                continue
            stats["failed"] += 1
            logger.warning(f"Scheduled sync failed for connector={c.id}: {e.status_code} {e.detail}")
            try:
                c2 = db.get(Connector, c.id)
                c2.last_sync_status = "failed"; c2.last_sync_at = datetime.utcnow(); db.commit()
            except Exception:
                db.rollback()
        except Exception as e:
            db.rollback()
            stats["failed"] += 1
            logger.error(f"Scheduled sync crashed for connector={c.id}: {type(e).__name__}: {e}")
    return stats
