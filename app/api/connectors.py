from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.connectors.file_connector import (
    ColumnMapping, validate_upload, parse_file_to_dataframe, dataframe_to_normalized_events,
)
from app.connectors.webhook_connector import generate_webhook_token
from app.connectors.database_connector import (
    DatabaseColumnMapping, fetch_rows, rows_to_normalized_events, _validate_read_only_query,
)
from app.connectors.sftp_connector import list_new_files, fetch_file
from app.connectors.email_connector import list_new_emails
from app.core.secrets import encrypt_config_secrets
from app.models.core import Connector

router = APIRouter()


class ConnectorCreate(BaseModel):
    tenant_id: str
    name: str
    connector_type: str  # api | webhook | database | file | sftp | email | local_agent
    config: dict = {}
    expected_frequency: Optional[str] = None  # realtime | near_realtime | periodic | slow


class ConnectorOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    connector_type: str
    is_active: bool
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    webhook_url: Optional[str] = None       # only populated once, at creation
    webhook_token: Optional[str] = None      # RAW token — shown exactly once, never again

    class Config:
        from_attributes = True


def _get_owned_connector(db: Session, connector_id: str, tenant_id: str) -> Connector:
    """Section 48: same ownership-check pattern used for alerts and entities."""
    connector = db.query(Connector).filter(Connector.id == connector_id, Connector.tenant_id == tenant_id).first()
    if not connector:
        raise HTTPException(404, "Connector not found")
    return connector


def _record_sync_failure(db: Session, connector: Connector, exc: HTTPException):
    """
    A connection-level failure (wrong credentials, unreachable host, missing
    directory) used to leave last_sync_status/last_sync_at untouched forever
    — identical to a connector that was simply never attempted. Section 51's
    whole point is catching "this is actively broken," so a failed attempt
    must be recorded, not just per-row outcomes from a successful connection.
    Called before re-raising; never swallows the original exception.
    """
    connector.last_sync_status = "failed"
    connector.last_sync_at = datetime.utcnow()
    db.commit()
    raise exc


from app.core.auth_users import authorize_tenant
from fastapi import Header
from typing import Optional as _Optional


@router.post("/", response_model=ConnectorOut)
def create_connector(payload: ConnectorCreate, request: Request, db: Session = Depends(get_db), authorization: _Optional[str] = Header(default=None)):
    # User-auth (Section 48) layered ON TOP of the existing service key —
    # not required (preserves every existing service-key-only caller,
    # tested exhaustively across five connector types already), but if a
    # token IS presented it must be valid and correct or the request is
    # rejected outright. A present-but-bad token is never silently
    # ignored and falling back to service-key-only — that would let a
    # malformed/expired/wrong-tenant token slip through unnoticed.
    # User-auth (Section 48) layered ON TOP of the existing service key —
    # not required (preserves every existing service-key-only caller,
    # tested exhaustively across five connector types already), but if a
    # token IS presented it must be valid and correct or the request is
    # rejected outright. Connectors hold credentials, so owner-role only.
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")

    connector = Connector(**payload.model_dump())

    raw_token = None
    if connector.connector_type == "webhook":
        raw_token, token_hash = generate_webhook_token()
        # Merge into config rather than overwrite — a webhook connector's
        # config also holds the WebhookMapping fields set by the caller.
        connector.config = {**(connector.config or {}), "webhook_token_hash": token_hash}
    elif connector.connector_type == "database":
        # Fail fast: reject a write query at creation time, not only when
        # someone eventually tries to sync it — a stored-but-never-usable
        # connector is confusing, not dangerous, but still worth catching
        # immediately rather than silently accepting.
        query = (connector.config or {}).get("query", "")
        _validate_read_only_query(query)

    # Encrypt any secret field UNCONDITIONALLY, not just for connector
    # types we've special-cased above — a no-op if no key in
    # SECRET_CONFIG_KEYS is present, but this means a future connector
    # type (email, local agent) automatically gets the same protection
    # without needing its own branch here to remember to add.
    connector.config = encrypt_config_secrets(connector.config or {})

    db.add(connector)
    db.commit()
    db.refresh(connector)
    audit.record(db, "connector.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id,
                 target=connector.id, detail={"type": connector.connector_type, "name": connector.name})

    out = ConnectorOut.model_validate(connector)
    if raw_token:
        out.webhook_url = f"/api/webhooks/{connector.id}/{raw_token}"
        out.webhook_token = raw_token  # last time this is ever returned — not stored in plaintext
    return out


@router.get("/", response_model=list[ConnectorOut])
def list_connectors(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return db.query(Connector).filter(Connector.tenant_id == tenant_id).all()


@router.get("/{connector_id}", response_model=ConnectorOut)
def get_connector(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return _get_owned_connector(db, connector_id, tenant_id)


@router.post("/{connector_id}/upload")
async def upload_file(
    connector_id: str,
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Section 8-E: file ingestion (CSV/XLSX).
    Column mapping must already be configured on the connector (Connector.config)
    at setup time (Section 50) — we don't infer mapping from arbitrary uploads.
    """
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)  # 404 on mismatch — don't reveal existence
    if connector.connector_type != "file":
        raise HTTPException(400, "This connector is not configured as a file connector")
    if not connector.config:
        raise HTTPException(400, "Connector has no column mapping configured — set config before uploading")

    try:
        mapping = ColumnMapping(**connector.config)
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")

    content = await file.read()
    ext = validate_upload(file.filename, content)
    df = parse_file_to_dataframe(content, ext)
    raw_events = dataframe_to_normalized_events(
        df, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name
    )

    results = ingest_events_bulk(db, [NormalizedEvent(**e) for e in raw_events])
    failed = sum(1 for r in results if r["errors"])
    return {"rows_processed": len(results), "failed": failed, "results": results}


@router.post("/{connector_id}/sync-database")
def sync_database(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Section 8-C: pulls from the configured external database using the
    connector's fixed, pre-configured query (never a caller-supplied one —
    see app/connectors/database_connector.py's module docstring for why).
    Manual trigger for now — would move to the scheduler (Section 17-18)
    alongside detection once a business's sync frequency is configurable.
    """
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    if connector.connector_type != "database":
        raise HTTPException(400, "This connector is not configured as a database connector")
    if not connector.is_active:
        raise HTTPException(409, "Connector is not active")

    mapping_keys = {"entity", "event_type", "timestamp_col", "amount_col", "quantity_col",
                     "currency_col", "location_col", "customer_id_col", "product_id_col",
                     "entity_id_col", "default_currency"}
    try:
        mapping = DatabaseColumnMapping(**{k: v for k, v in connector.config.items() if k in mapping_keys})
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")

    try:
        rows = fetch_rows(connector.config)
    except HTTPException as e:
        _record_sync_failure(db, connector, e)
    raw_events = rows_to_normalized_events(rows, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name)

    results = ingest_events_bulk(db, [NormalizedEvent(**e) for e in raw_events])
    failed = sum(1 for r in results if r["errors"])
    return {"rows_fetched": len(rows), "rows_processed": len(results), "failed": failed, "results": results}


@router.post("/{connector_id}/sync-sftp")
def sync_sftp(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Section 8-F. Pulls every new (not previously processed) file matching
    the connector's configured pattern, parses each with the same logic
    file uploads use, and records what was processed so a repeat sync
    doesn't re-ingest the same file (Section 11).
    """
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    if connector.connector_type != "sftp":
        raise HTTPException(400, "This connector is not configured as an SFTP connector")
    if not connector.is_active:
        raise HTTPException(409, "Connector is not active")

    mapping_keys = {"entity", "event_type", "timestamp_col", "amount_col", "quantity_col",
                     "currency_col", "location_col", "customer_id_col", "product_id_col",
                     "entity_id_col", "default_currency"}
    try:
        mapping = ColumnMapping(**{k: v for k, v in connector.config.items() if k in mapping_keys})
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")

    try:
        new_files = list_new_files(connector.config)
    except HTTPException as e:
        _record_sync_failure(db, connector, e)
    file_results = []
    newly_processed = list(connector.config.get("processed_files", []))

    for f in new_files:
        try:
            content = fetch_file(connector.config, f["filename"])
            ext = validate_upload(f["filename"], content)
            df = parse_file_to_dataframe(content, ext)
            raw_events = dataframe_to_normalized_events(
                df, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name
            )
            results = ingest_events_bulk(db, [NormalizedEvent(**e) for e in raw_events])
            failed = sum(1 for r in results if r["errors"])
            file_results.append({"filename": f["filename"], "rows_processed": len(results), "failed": failed})
        except HTTPException as e:
            # One bad file must not abort the whole sync — record and continue.
            file_results.append({"filename": f["filename"], "error": e.detail})
        finally:
            # Mark processed on EITHER outcome, not just success. A file
            # that's permanently broken would otherwise retry on every
            # single sync forever (caught by testing repeat syncs, not
            # obvious from the happy-path code). If the file is later
            # fixed/replaced, its mtime changes, so this key naturally
            # stops matching and the corrected version is picked up
            # automatically — nothing is permanently lost by marking a
            # failed attempt as processed.
            newly_processed.append([f["filename"], f["mtime"]])

    connector.config = {**connector.config, "processed_files": newly_processed}
    db.commit()

    return {"files_found": len(new_files), "files": file_results}


@router.post("/{connector_id}/sync-email")
def sync_email(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """
    Section 8-H. Same processed-tracking principle as SFTP: a message
    that fails to parse is still marked processed (by Message-ID) so it
    doesn't retry forever — learned directly from the SFTP connector bug.
    """
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    if connector.connector_type != "email":
        raise HTTPException(400, "This connector is not configured as an email connector")
    if not connector.is_active:
        raise HTTPException(409, "Connector is not active")

    mapping_keys = {"entity", "event_type", "timestamp_col", "amount_col", "quantity_col",
                     "currency_col", "location_col", "customer_id_col", "product_id_col",
                     "entity_id_col", "default_currency"}
    try:
        mapping = ColumnMapping(**{k: v for k, v in connector.config.items() if k in mapping_keys})
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")

    try:
        new_emails = list_new_emails(connector.config)
    except HTTPException as e:
        _record_sync_failure(db, connector, e)
    email_results = []
    newly_processed = list(connector.config.get("processed_message_ids", []))

    for msg in new_emails:
        msg_result = {"message_id": msg["message_id"], "subject": msg["subject"], "attachments": []}
        for filename, content in msg["attachments"]:
            try:
                ext = validate_upload(filename, content)
                df = parse_file_to_dataframe(content, ext)
                raw_events = dataframe_to_normalized_events(
                    df, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name
                )
                results = ingest_events_bulk(db, [NormalizedEvent(**e) for e in raw_events])
                failed = sum(1 for r in results if r["errors"])
                msg_result["attachments"].append({"filename": filename, "rows_processed": len(results), "failed": failed})
            except HTTPException as e:
                msg_result["attachments"].append({"filename": filename, "error": e.detail})
        email_results.append(msg_result)
        newly_processed.append(msg["message_id"])  # marked regardless of per-attachment outcome — same lesson as SFTP

    connector.config = {**connector.config, "processed_message_ids": newly_processed}
    db.commit()

    return {"emails_found": len(new_emails), "emails": email_results}
