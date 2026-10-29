from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ValidationError, ConfigDict
from sqlalchemy.orm import Session

from app.core import audit, reconcile, snapshot, sources
from app.core.cache import invalidate_tenant
from app.core.db import get_db
from app.core.ingestion import NormalizedEvent
from app.core.tenant_time import tenant_zone
from app.connectors.file_connector import (
    ColumnMapping, import_notes, read_upload, validate_upload, parse_file_to_dataframe, dataframe_to_normalized_events, add_row_ids, with_default_id,
    extra_sheet_events, other_sheet_names,
)
from app.connectors.inspect import describe, mapping_provider
from app.connectors import url_connector
from app.connectors.mpesa import expand_preset, sample_c2b_payload
from app.connectors.webhook_connector import WebhookMapping, generate_webhook_token, webhook_payload_to_event_dict
from app.connectors.database_connector import DRIVERS, _validate_read_only_query, driver_catalog
from app.core.netguard import check_host_allowed, tenant_path
from app.core.sync import MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES, SYNCABLE, run_sync
from app.core.secrets import encrypt_config_secrets
from app.models.core import Connector

router = APIRouter()

CONNECTOR_TYPES = {"url", "api", "webhook", "database", "file", "sftp", "email", "local_agent", "shared_folder", "manual", "system"}
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
    elif connector_type == "file" and config:           # an empty config is allowed: the mapping can be set before the first upload
        try:
            ColumnMapping(**config)
        except ValidationError as e:
            first = e.errors()[0]
            raise HTTPException(400, f"The column mapping is not valid ({'.'.join(str(x) for x in first['loc']) or 'config'}: {first['msg']})")


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
    last_sync_error: Optional[str] = None
    expected_frequency: Optional[str] = None
    sync_interval_minutes: Optional[int] = None
    last_attempt_at: Optional[datetime] = None
    merge_into: Optional[str] = None        # set when the owner confirmed it records the same things as that source
    webhook_url: Optional[str] = None       # only populated once, at creation
    webhook_token: Optional[str] = None      # RAW token — shown exactly once, never again

    model_config = ConfigDict(from_attributes=True)


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
    if payload.connector_type == "webhook" and "preset" in (payload.config or {}):
        payload.config = expand_preset(payload.config)         # e.g. {"preset": "mpesa_c2b"} becomes its full mapping
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


@router.get("/contributions")
def source_contributions(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """What each source adds to the shared dataset: counted, matched to another source's copy, rejected."""
    authorize_tenant(authorization, tenant_id)
    return sources.contributions(db, tenant_id)


@router.get("/overlaps")
def source_overlaps(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Sources that look like the same records (to confirm) and, for confirmed merges, what exists in only one source."""
    authorize_tenant(authorization, tenant_id)
    return reconcile.overview(db, tenant_id)


@router.post("/{other_id}/merge-into/{keep_id}")
def merge_sources(other_id: str, keep_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                  authorization: Optional[str] = Header(default=None)):
    """Owner confirms two sources record the same things: matching records are counted once, in `keep_id`."""
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    if other_id == keep_id:
        raise HTTPException(400, "A source cannot be merged into itself")
    other, keep = _get_owned_connector(db, other_id, tenant_id), _get_owned_connector(db, keep_id, tenant_id)
    if keep.merge_into or db.query(Connector.id).filter(Connector.merge_into == other.id).first():
        raise HTTPException(400, "One of these sources is already merged with another. Undo that first.")
    paired = reconcile.merge(db, other, keep)
    invalidate_tenant(tenant_id)
    audit.record(db, "connector.merge", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=other_id,
                 detail={"into": keep_id, "counted_once": paired})
    return {"counted_once": paired}


@router.delete("/{other_id}/merge")
def unmerge_source(other_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                   authorization: Optional[str] = Header(default=None)):
    """Undo a merge: every record it hid counts again."""
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    other = _get_owned_connector(db, other_id, tenant_id)
    if not other.merge_into:
        raise HTTPException(400, "This source is not merged with another")
    restored = reconcile.unmerge(db, other)
    invalidate_tenant(tenant_id)
    audit.record(db, "connector.unmerge", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=other_id,
                 detail={"restored": restored})
    return {"restored": restored}


@router.get("/{connector_id}/rejected")
def rejected_records(connector_id: str, tenant_id: str, limit: int = 50, db: Session = Depends(get_db),
                     authorization: Optional[str] = Header(default=None)):
    """The records this source sent that were left out of every total, with why, so each can be fixed at the source."""
    authorize_tenant(authorization, tenant_id)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    return {"total": sources.rejected_summary(db, connector)["rejected_total"], "records": sources.rejected(db, connector, max(1, min(limit, 200)))}


@router.get("/{connector_id}", response_model=ConnectorOut)
def get_connector(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return _get_owned_connector(db, connector_id, tenant_id)


@router.post("/{connector_id}/upload")
async def upload_file(
    connector_id: str,
    tenant_id: str = Form(...),
    file: UploadFile = File(...),
    replace: bool = Form(False),
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Section 8-E: file ingestion (CSV/XLSX).
    Column mapping must already be configured on the connector (Connector.config)
    at setup time (Section 50) — we don't infer mapping from arbitrary uploads.
    """
    authorize_tenant(authorization, tenant_id, require_role="owner" if replace else None)       # replacing deletes records
    connector = _get_owned_connector(db, connector_id, tenant_id)  # 404 on mismatch — don't reveal existence
    if connector.connector_type != "file":
        raise HTTPException(400, "This connector is not configured as a file connector")
    if not connector.config:
        raise HTTPException(400, "Connector has no column mapping configured — set config before uploading")

    try:
        mapping = ColumnMapping(**connector.config)
    except Exception as e:
        raise HTTPException(400, f"Connector column mapping is invalid: {e}")

    content = await read_upload(file)
    ext = validate_upload(file.filename, content)
    df = add_row_ids(parse_file_to_dataframe(content, ext))
    mapping = with_default_id(df, mapping)
    parsed = dataframe_to_normalized_events(
        df, mapping, tenant_id=tenant_id, connector_id=connector_id, source=connector.name
    )

    # Adds new records and updates edited ones (same id). With `replace`, the file is the whole truth: records it no
    # longer has are removed. Without it, files are batches that add up (a month at a time), so nothing is removed.
    extra_events, extra_skipped, sheet_notes = extra_sheet_events(content, ext, tenant_id, connector_id, connector.name)   # the other sheets of a workbook
    done = snapshot.apply(db, connector, [NormalizedEvent(**e) for e in parsed.events + extra_events], remove_missing=replace, confirmed=True)
    skipped_all = parsed.skipped + extra_skipped
    return {**done, "rows_processed": len(done["results"]), "skipped": len(skipped_all), "skipped_examples": skipped_all[:3],
            **import_notes(parsed), **({"other_sheets": sheet_notes} if sheet_notes else {})}


def _sync_endpoint(connector_id, tenant_id, db, authorization, expected_type, confirmed=False):
    authorize_tenant(authorization, tenant_id, require_role="owner" if confirmed else None)
    connector = _get_owned_connector(db, connector_id, tenant_id)
    if connector.connector_type != expected_type:
        raise HTTPException(400, f"This connector is not configured as a {expected_type.replace('_', ' ')} connector")
    return run_sync(db, connector, confirmed=confirmed)


@router.post("/{connector_id}/sync-database")
def sync_database_ep(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Section 8-C: pulls with the connector's FIXED, pre-configured query (never a caller-supplied one)."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "database")


@router.post("/{connector_id}/sync-url")
def sync_url_ep(connector_id: str, tenant_id: str, confirm_replace: bool = False, db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    """Re-reads the connector's web link or sheet now and mirrors it: added, edited and deleted rows follow. Repeating it
    is safe. A change to over half of a sizeable source needs `confirm_replace` (owner)."""
    return _sync_endpoint(connector_id, tenant_id, db, authorization, "url", confirmed=confirm_replace)


@router.post("/inspect-file")
async def inspect_file(tenant_id: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db),
                       authorization: Optional[str] = Header(default=None)):
    """Read an uploaded spreadsheet WITHOUT storing anything: returns its columns, a few rows and a suggested mapping."""
    authorize_tenant(authorization, tenant_id)
    content = await read_upload(file)
    ext = validate_upload(file.filename or "", content)
    df = parse_file_to_dataframe(content, ext)
    others = other_sheet_names(content, ext)
    out = await run_in_threadpool(lambda: describe(df, mapping_provider(db, tenant_id)))
    return {**out, "other_sheets": others} if others else out     # the AI call is slow and blocking


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


class TestEventIn(BaseModel):
    payload: Optional[dict] = None         # an example the sender would post; an M-Pesa source has a built-in one


@router.post("/{connector_id}/test-event")
def test_event(connector_id: str, tenant_id: str, body: Optional[TestEventIn] = None, db: Session = Depends(get_db),
               authorization: _Optional[str] = Header(default=None)):
    """Shows what a sample payment WOULD become, using the source's own mapping, and stores nothing. It proves the mapping
    and the time and amount handling before any real payment arrives."""
    authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _get_owned_connector(db, connector_id, tenant_id)
    if c.connector_type != "webhook":
        raise HTTPException(400, "Only a live feed can be tested this way")
    try:
        mapping = WebhookMapping(**{k: v for k, v in (c.config or {}).items() if k != "webhook_token_hash"})
    except Exception as e:
        raise HTTPException(400, f"This live feed's mapping is not valid: {e}")
    payload = body.payload if body and body.payload else (sample_c2b_payload() if mapping.ack == "daraja" else None)
    if payload is None:
        raise HTTPException(400, "Send an example of what your system posts (a JSON object) to test it")
    event = NormalizedEvent(**webhook_payload_to_event_dict(payload, mapping, tenant_id=tenant_id, connector_id=c.id, source=c.name,
                                                            received_at=datetime.now(timezone.utc)))
    zone = tenant_zone(db, tenant_id)
    when = event.timestamp.replace(tzinfo=zone) if event.timestamp.tzinfo is None else event.timestamp
    return {"would_create": {"entity": event.entity, "entity_id": event.entity_id, "amount": event.amount, "currency": event.currency,
                             "customer_id": event.customer_id, "time": when.astimezone(zone).isoformat(timespec="seconds")}}


@router.post("/{connector_id}/new-link")
def new_link(connector_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
             authorization: _Optional[str] = Header(default=None)):
    """A live feed's link is shown once, so a lost one is replaced, not recovered: the old link stops working at once."""
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _get_owned_connector(db, connector_id, tenant_id)
    if c.connector_type != "webhook":
        raise HTTPException(400, "Only a live feed has a link")
    raw, token_hash = generate_webhook_token()
    c.config = {**(c.config or {}), "webhook_token_hash": token_hash}
    db.commit()
    audit.record(db, "connector.new_link", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=c.id)
    return {"webhook_url": f"/api/webhooks/{c.id}/{raw}"}


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


@router.delete("/{connector_id}")
def disconnect_connector(connector_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                         authorization: Optional[str] = Header(default=None)):
    """Disconnect a source and delete everything it brought in. Pause (PATCH is_active) stops it without deleting."""
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _get_owned_connector(db, connector_id, tenant_id)
    name, kind = c.name, c.connector_type
    result = sources.remove_source(db, c)
    audit.record(db, "connector.disconnect", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=connector_id,
                 detail={"name": name, "type": kind, **result})
    return result
