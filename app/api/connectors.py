from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.connectors.file_connector import (
    ColumnMapping, validate_upload, parse_file_to_dataframe, dataframe_to_normalized_events, add_row_ids, with_default_id,
)
from app.connectors.inspect import describe, mapping_provider
from app.connectors import url_connector
from app.connectors.webhook_connector import generate_webhook_token
from app.connectors.database_connector import DRIVERS, _validate_read_only_query, driver_catalog
from app.core.netguard import check_host_allowed, tenant_path
from app.core.sync import MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES, SYNCABLE, run_sync
from app.core.secrets import encrypt_config_secrets
from app.models.core import Connector

router = APIRouter()

CONNECTOR_TYPES = {"url", "api", "webhook", "database", "file", "sftp", "email", "local_agent", "shared_folder", "manual"}
FREQUENCIES = {"realtime", "near_realtime", "periodic", "slow"}


def _check_interval(connector_type: str, minutes: int):
    if connector_type not in SYNCABLE:
        raise HTTPException(400, "Only database, web link, sftp, email and shared_folder connectors can sync on a schedule")
    if not (MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES):
        raise HTTPException(400, f"sync_interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}")


def _validate_config(connector_type: str, config: dict, tenant_id: str):
    """Fail at setup time, not at the first sync: a stored-but-unusable connector is just confusing."""
    if connector_type == "database":
        _validate_read_only_query(config.get("query", ""))
        if config.get("db_type") not in DRIVERS:
            raise HTTPException(400, f"db_type must be one of {sorted(DRIVERS)}")
        if config["db_type"] == "sqlite":
            tenant_path(tenant_id, config.get("database", ""))
        elif config["db_type"] != "odbc" or config.get("host"):
            check_host_allowed(config.get("host", ""))
    elif connector_type == "url":
        url_connector.validate_url(config.get("url", ""))
        url_connector._auth(config)
        if not config.get("timestamp_col") and not config.get("timestamp_path"):
            raise HTTPException(400, "Say which column holds the date (timestamp_col)")
        if not config.get("entity") or not config.get("event_type"):
            raise HTTPException(400, "Say whether each row is a sale or an expense (entity, event_type)")
    elif connector_type == "sftp":
        check_host_allowed(config.get("host", ""))
    elif connector_type == "email":
        check_host_allowed(config.get("imap_host", ""))
    elif connector_type == "shared_folder":
        tenant_path(tenant_id, config.get("path", ""))


class ConnectorCreate(BaseModel):
    tenant_id: str
    name: str
    connector_type: str  # api | webhook | database | file | sftp | email | local_agent
    config: dict = {}
    expected_frequency: Optional[str] = None  # realtime | near_realtime | periodic | slow
    sync_interval_minutes: Optional[int] = None   # pull connectors only (database/sftp/email/shared_folder)


class ConnectorOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    connector_type: str
    is_active: bool
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    expected_frequency: Optional[str] = None
    sync_interval_minutes: Optional[int] = None
    last_attempt_at: Optional[datetime] = None
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


from app.core.auth_users import authorize_tenant
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

    if payload.connector_type not in CONNECTOR_TYPES:
        raise HTTPException(400, f"connector_type must be one of {sorted(CONNECTOR_TYPES)}")
    if payload.expected_frequency is not None and payload.expected_frequency not in FREQUENCIES:
        raise HTTPException(400, f"expected_frequency must be one of {sorted(FREQUENCIES)}")
    if not payload.name.strip():
        raise HTTPException(400, "name cannot be empty")
    _validate_config(payload.connector_type, payload.config or {}, payload.tenant_id)
    if payload.sync_interval_minutes is not None:
        _check_interval(payload.connector_type, payload.sync_interval_minutes)
    connector = Connector(**{**payload.model_dump(), "name": payload.name.strip()})

    raw_token = None
    if connector.connector_type in ("webhook", "local_agent"):
        raw_token, token_hash = generate_webhook_token()
        # Merge into config rather than overwrite — a webhook connector's
        # config also holds the WebhookMapping fields set by the caller.
        connector.config = {**(connector.config or {}), "webhook_token_hash": token_hash}

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
    df = add_row_ids(parse_file_to_dataframe(content, ext))
    mapping = with_default_id(df, mapping)
    raw_events = dataframe_to_normalized_events(
        df, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name
    )

    results = ingest_events_bulk(db, [NormalizedEvent(**e) for e in raw_events])
    failed = sum(1 for r in results if r["errors"])
    return {"rows_processed": len(results), "failed": failed, "results": results}


def _sync_endpoint(connector_id, tenant_id, db, authorization, expected_type):
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    if connector.connector_type != expected_type:
        raise HTTPException(400, f"This connector is not configured as a {expected_type.replace('_', ' ')} connector")
    return run_sync(db, connector)


@router.post("/{connector_id}/sync-database")
def sync_database_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 8-C: pulls with the connector's FIXED, pre-configured query (never a caller-supplied one)."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "database")


@router.post("/{connector_id}/sync-url")
def sync_url_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Pulls the connector's web link / API now; rows already seen are skipped, so repeating it is safe."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "url")


@router.post("/inspect-file")
async def inspect_file(tenant_id: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db),
                       authorization: Optional[str] = Header(default=None)):
    """Read an uploaded spreadsheet WITHOUT storing anything: returns its columns, a few rows and a suggested mapping."""
    authorize_tenant(authorization, tenant_id)
    content = await file.read()
    ext = validate_upload(file.filename or "", content)
    df = parse_file_to_dataframe(content, ext)
    return await run_in_threadpool(lambda: describe(df, mapping_provider(db, tenant_id)))     # the AI call is slow and blocking


class InspectUrl(BaseModel):
    tenant_id: str
    config: dict


@router.post("/inspect-url")
def inspect_url(payload: InspectUrl, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Test a web link / API (including its login) and show what it returns: columns, sample rows, suggested mapping.
    Nothing is saved, so this is the 'Test connection' button."""
    authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    df = url_connector.fetch_dataframe(payload.config, max_pages=1)
    return describe(df, mapping_provider(db, payload.tenant_id))


@router.post("/{connector_id}/sync-sftp")
def sync_sftp_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 8-F: every new file matching the pattern; repeat syncs do not re-ingest processed files."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "sftp")


@router.post("/{connector_id}/sync-email")
def sync_email_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 8-H: new report emails' attachments; processed by Message-ID."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "email")


@router.post("/{connector_id}/sync-folder")
def sync_folder_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 8-G: new files in the business's shared folder; tracked by content hash."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "shared_folder")


@router.get("/meta/database-types")
def database_types(tenant_id: str, authorization: Optional[str] = Header(default=None)):
    """Which database engines this server can connect to, and which have actually been verified against a live server."""
    authorize_tenant(authorization, tenant_id)
    return driver_catalog()


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    expected_frequency: Optional[str] = None
    sync_interval_minutes: Optional[int] = None
    clear_sync_interval: bool = False
    config: Optional[dict] = None          # replaces the config (secrets re-encrypted); owner only


@router.patch("/{connector_id}", response_model=ConnectorOut)
def update_connector(connector_id: str, tenant_id: str, payload: ConnectorUpdate, request: Request, db: Session = Depends(get_db),
                     authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _get_owned_connector(db, connector_id, tenant_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        if not (data["name"] or "").strip():
            raise HTTPException(400, "name cannot be empty")
        c.name = data["name"].strip()
    if data.get("expected_frequency") is not None:
        if data["expected_frequency"] not in FREQUENCIES:
            raise HTTPException(400, f"expected_frequency must be one of {sorted(FREQUENCIES)}")
        c.expected_frequency = data["expected_frequency"]
    if "is_active" in data and data["is_active"] is not None:
        c.is_active = data["is_active"]
    if data.get("clear_sync_interval"):
        c.sync_interval_minutes = None
    elif data.get("sync_interval_minutes") is not None:
        _check_interval(c.connector_type, data["sync_interval_minutes"])
        c.sync_interval_minutes = data["sync_interval_minutes"]
    if data.get("config") is not None:
        new_cfg = dict(data["config"])
        # Keep stored webhook/agent token hash and processed-tracking unless the caller deliberately replaces them.
        for keep in ("webhook_token_hash", "processed_files", "processed_message_ids", "processed_hashes"):
            if keep in (c.config or {}) and keep not in new_cfg:
                new_cfg[keep] = c.config[keep]
        _validate_config(c.connector_type, new_cfg, tenant_id)
        c.config = encrypt_config_secrets(new_cfg)
    db.commit(); db.refresh(c)
    audit.record(db, "connector.update", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=c.id,
                 detail={"fields": sorted(k for k in data if k != "config") + (["config"] if "config" in data else [])})
    return c
