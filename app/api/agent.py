"""
Local systems: the screens' side (connect, check now, set the location, confirm, disconnect) and the agent's side
(heartbeat and sync). The agent authenticates with its connector's own token, like a webhook; people use their sign-in.
"""
import base64
import json
import os
from datetime import timedelta

from app.core.clock import utcnow
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.connectors.webhook_connector import generate_webhook_token
from app.core import audit, sources, systems
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.core.rate_limit import check_webhook_rate_limit
from app.models.core import Connector

router = APIRouter()
MAX_BODY_BYTES = 8 * 1024 * 1024
CONFIRM_WINDOW = timedelta(minutes=15)


# ---- agent side ---------------------------------------------------------------------------------------------------

def _agent_connector(db: Session, connector_id: str, authorization: Optional[str]) -> Connector:
    check_webhook_rate_limit(connector_id)
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    c = db.query(Connector).filter(Connector.id == connector_id).first()
    # Same answer for "no such connector" and "wrong token": nothing to learn by guessing.
    if not c or c.connector_type != "system" or not token or not systems.token_ok(c, token):
        raise HTTPException(404, "Not found")
    if not c.is_active:
        raise HTTPException(409, "Connector is not active")
    return c


async def _json(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return body


@router.post("/{connector_id}/heartbeat")
async def heartbeat(connector_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    c = _agent_connector(db, connector_id, authorization)
    body = await _json(request)
    return {"ok": True, "commands": systems.take_heartbeat(db, c, body, utcnow())}


@router.post("/{connector_id}/sync")
async def sync(connector_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    c = _agent_connector(db, connector_id, authorization)
    body = await _json(request)
    now = utcnow()
    row = systems.status_row(db, c)
    if body.get("first") and isinstance(body.get("manifest"), dict):       # a full copy announces its totals first, so a truncated source is caught before anything is removed
        stored = systems.stored_totals(db, c.id)
        theirs = body["manifest"].get("entities") or {}
        doomed = sum(max(0, s["count"] - int((theirs.get(e) or {}).get("count", 0))) for e, s in stored.items())
        if body.get("mode") == "full":
            systems.guard_removals(db, c, row, doomed, now)
    out = systems.apply_sync(db, c, body, now)
    row = systems.status_row(db, c)
    row.last_seen_at = now
    db.commit()
    out["commands"] = systems.commands_for(row, now)
    return out


# ---- screens' side ------------------------------------------------------------------------------------------------

def _owned(db: Session, connector_id: str, tenant_id: str) -> Connector:
    c = db.query(Connector).filter(Connector.id == connector_id, Connector.tenant_id == tenant_id, Connector.connector_type == "system").first()
    if not c:
        raise HTTPException(404, "Connector not found")
    return c


def _public_url(request: Request) -> str:
    base = os.getenv("API_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or str(request.base_url)
    base = base.rstrip("/")
    if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.0.0.1")):
        base = "https://" + base.removeprefix("http://")
    return base


def _code(base: str, issued: list[tuple[str, str, str]]) -> str:
    """One code for one or several systems. `issued` is (connector id, token, system) per system. A single system keeps the
    original 'bre1' form (agents already out there read it); several share one 'bre2' code, so the owner pastes once."""
    if len(issued) == 1:
        cid, token, system = issued[0]
        raw, prefix = {"u": base, "c": cid, "t": token, "s": system}, "bre1."
    else:
        raw, prefix = {"u": base, "e": [{"c": c, "t": t, "s": s} for c, t, s in issued]}, "bre2."
    return prefix + base64.urlsafe_b64encode(json.dumps(raw, separators=(",", ":")).encode()).decode().rstrip("=")


@router.get("/")
def list_systems(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """The three systems, each connected or not, with the live state of its link."""
    authorize_tenant(authorization, tenant_id)
    have = {(c.config or {}).get("system"): c for c in db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.connector_type == "system")}
    now = utcnow()
    download = os.getenv("AGENT_DOWNLOAD_URL") or None       # where the one-file Windows agent can be downloaded (set once on the server)
    return [{"system": key, "label": meta["label"], "what": meta["what"], "connected": key in have, "download_url": download,
             **(systems.summary(db, have[key], now) if key in have else {})} for key, meta in systems.SYSTEMS.items()]


class ConnectIn(BaseModel):
    tenant_id: str
    system: Optional[str] = None             # one system, or ...
    systems: Optional[list[str]] = None      # ... several at once: one code connects them all


def _issue(db: Session, tenant_id: str, system: str) -> tuple[Connector, str]:
    """Adds the system, or replaces the code of one already added (the old code stops working at once)."""
    raw, token_hash = generate_webhook_token()
    c = next((x for x in db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.connector_type == "system")
              if (x.config or {}).get("system") == system), None)
    if c is None:
        c = Connector(tenant_id=tenant_id, name=systems.SYSTEMS[system]["label"], connector_type="system", expected_frequency="realtime",
                      config={"system": system, "webhook_token_hash": token_hash})
        db.add(c)
    else:
        c.config = {**(c.config or {}), "webhook_token_hash": token_hash}
        c.is_active = True
    return c, raw


@router.post("/connect")
def connect(payload: ConnectIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Adds the system(s), or issues a fresh code for ones already added. The code is shown once; only its hash is stored."""
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    wanted = list(dict.fromkeys(payload.systems or ([payload.system] if payload.system else [])))
    if not wanted:
        raise HTTPException(400, "Say which system to connect")
    if any(w not in systems.SYSTEMS for w in wanted):
        raise HTTPException(400, f"system must be one of {sorted(systems.SYSTEMS)}")
    issued = [(*_issue(db, payload.tenant_id, w), w) for w in wanted]
    db.commit()
    for c, _, w in issued:
        db.refresh(c)
        audit.record(db, "connector.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id, target=c.id, detail={"type": "system", "system": w})
    return {"connector_id": issued[0][0].id, "connector_ids": [c.id for c, _, _ in issued],
            "code": _code(_public_url(request), [(c.id, raw, w) for c, raw, w in issued])}


@router.post("/{connector_id}/check")
def check_now(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """'Is it connected and synced?' Asks the agent to look now; the screen polls the list until it has answered."""
    authorize_tenant(authorization, tenant_id)
    c = _owned(db, connector_id, tenant_id)
    row = systems.status_row(db, c)
    row.check_requested_at = utcnow()
    db.commit()
    return systems.summary(db, c)


class SettingsIn(BaseModel):
    path: Optional[str] = None            # "" clears it (back to finding it automatically)
    scope_id: Optional[str] = None
    paused: Optional[bool] = None


@router.post("/{connector_id}/settings")
def settings(connector_id: str, tenant_id: str, payload: SettingsIn, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _owned(db, connector_id, tenant_id)
    row = systems.status_row(db, c)
    cmd = dict(row.commands or {})
    if payload.path is not None:
        p = payload.path.strip()
        if len(p) > 1000 or "\x00" in p:
            raise HTTPException(400, "That location is not valid")
        cmd["path"] = p or None
        cmd["resync"] = True
    if payload.scope_id is not None:
        cmd["scope_id"] = payload.scope_id.strip() or None
        cmd["resync"] = True
    if payload.paused is not None:
        cmd["paused"] = payload.paused
    row.commands = {k: v for k, v in cmd.items() if v is not None}
    row.check_requested_at = utcnow()
    db.commit()
    return systems.summary(db, c)


@router.post("/{connector_id}/confirm")
def confirm_change(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """The owner confirms that a large removal is real (the system's data was cleared or replaced)."""
    authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _owned(db, connector_id, tenant_id)
    row = systems.status_row(db, c)
    row.confirm_until = utcnow() + CONFIRM_WINDOW
    row.commands = {**(row.commands or {}), "resync": True}
    row.check_requested_at = utcnow()
    db.commit()
    return systems.summary(db, c)


@router.post("/{connector_id}/resync")
def resync(connector_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _owned(db, connector_id, tenant_id)
    row = systems.status_row(db, c)
    row.commands = {**(row.commands or {}), "resync": True}
    row.check_requested_at = utcnow()
    db.commit()
    return systems.summary(db, c)


@router.delete("/{connector_id}")
def disconnect(connector_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    c = _owned(db, connector_id, tenant_id)
    audit.record(db, "connector.delete", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=c.id, detail={"type": "system"})
    return sources.remove_source(db, c)
