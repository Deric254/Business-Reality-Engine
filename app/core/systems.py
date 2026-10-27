"""
Local systems (smePro, Imara, Pharmacy ERP) reported by the agent on the business's own computer.

The engine cannot see a computer's disk, so the agent pushes. This module is the engine's side of that:
- a registry of the systems that can be connected;
- the exact copy: apply a delta, remove what left, sweep a range, and check the agent's control totals (count and sum)
  against what is stored, so a copy that differs from the source is noticed at once and repaired;
- the link's health: derived from heartbeats, so "not found", "needs a key" or "computer is off" are stated, and a link
  that stays down raises an alert that clears itself when the link is back.
"""
import hashlib
import hmac
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.core import snapshot, sources
from app.core.clock import utcnow
from app.core.ingestion import NormalizedEvent
from app.models.agent import AgentStatus
from app.models.core import BusinessEvent as E, Connector

SYSTEMS = {
    "smepro": {"label": "smePro", "what": "sales, refunds, purchases and bookkeeping expenses"},
    "imara": {"label": "Imara", "what": "receipts and payments"},
    "pharmacy": {"label": "Pharmacy ERP", "what": "sales, refunds and purchases"},
}
ENTITIES = {"sale", "expense", "refund"}
ALIVE_SECONDS = 150            # the agent beats every ~30 s: missing 5 in a row means it is not running
ALERT_AFTER = timedelta(minutes=10)
MAX_EVENTS = 1000
MAX_DELETES = 5000
OK_STATES = {"connected"}

# Words an owner can act on, per state the agent can report.
STATE_TEXT = {
    "connected": "Connected and synced",
    "not_found": "Not found on this computer",
    "not_this_system": "A file was found but it is not this system's",
    "needs_driver": "Needs a one-time reader installed",
    "needs_key": "Needs the system's key file",
    "locked": "System is busy; retrying",
    "unreadable": "Cannot be read",
    "schema_changed": "The system has changed; needs updating",
    "needs_choice": "Choose which business to read",
    "needs_confirmation": "Large change waiting for your confirmation",
    "refused": "Connection no longer accepted",
    "waiting": "Waiting for the agent to report",
    "offline": "Agent not reporting (computer off or agent stopped)",
}


def token_ok(connector: Connector, token: str) -> bool:
    stored = (connector.config or {}).get("webhook_token_hash")
    return bool(stored) and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), stored)


def status_row(db: Session, connector: Connector) -> AgentStatus:
    row = db.get(AgentStatus, connector.id)
    if row is None:
        row = AgentStatus(connector_id=connector.id, tenant_id=connector.tenant_id, state="waiting", status={}, last_pass={}, agent={}, verify={}, commands={})
        db.add(row)
        db.flush()
    return row


def effective_state(row: Optional[AgentStatus], now: datetime) -> str:
    if row is None or row.last_seen_at is None:
        return "waiting"
    if (now - row.last_seen_at).total_seconds() > ALIVE_SECONDS:
        return "offline"
    return row.state


def stored_totals(db: Session, connector_id: str) -> dict[str, dict]:
    """Count and sum per entity as the SOURCE gave them (the original amount when the engine converted currency)."""
    original = func.coalesce(text("(metadata_json->'fx'->>'original_amount')::numeric"), E.amount)
    rows = db.query(E.entity, func.count(), func.coalesce(func.sum(original), 0)).filter(E.connector_id == connector_id).group_by(E.entity).all()
    return {e: {"count": n, "sum": Decimal(str(s))} for e, n, s in rows}


def compare(stored: dict, manifest: dict) -> list[dict]:
    """Differences between what is stored and what the agent says the system holds. Empty = identical."""
    diffs = []
    for entity in sorted(set(stored) | set(manifest)):
        s = stored.get(entity, {"count": 0, "sum": Decimal(0)})
        m = manifest.get(entity, {"count": 0, "sum": "0"})
        if s["count"] != int(m["count"]) or abs(s["sum"] - Decimal(str(m["sum"]))) > Decimal("0.005"):
            diffs.append({"entity": entity, "engine_count": s["count"], "system_count": int(m["count"]),
                          "engine_sum": str(s["sum"]), "system_sum": str(m["sum"])})
    return diffs


def build_events(connector: Connector, items: list) -> list[NormalizedEvent]:
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise HTTPException(422, f"event {i} is not an object")
        if it.get("entity") not in ENTITIES or not it.get("entity_id"):
            raise HTTPException(422, f"event {i}: unknown entity or no id")
        try:
            out.append(NormalizedEvent(tenant_id=connector.tenant_id, connector_id=connector.id, source=connector.name,
                                       **{k: v for k, v in it.items() if k not in ("tenant_id", "connector_id", "source")}))
        except Exception:
            raise HTTPException(422, f"event {i} ({it.get('entity_id')}) does not match the record format")
    return out


def _sweep_ids(db: Session, connector: Connector, sweep: dict, keep: set[str]) -> list[str]:
    """Stored records of this entity in (after, upto] by id (byte order, same as the source) that the source no longer has."""
    col = E.entity_id.collate("C")
    q = db.query(E.id, E.entity_id).filter(E.connector_id == connector.id, E.entity == sweep["entity"],
                                           and_(col > sweep["after"], col <= sweep["upto"]))
    return [rid for rid, eid in q if eid not in keep]


def guard_removals(db: Session, connector: Connector, row: AgentStatus, doomed: int, now: datetime) -> None:
    total = db.query(func.count(E.id)).filter(E.connector_id == connector.id).scalar() or 0
    if doomed and total >= snapshot.BIG_CHANGE_MIN_RECORDS and doomed > snapshot.BIG_CHANGE_SHARE * total:
        if not (row.confirm_until and row.confirm_until > now):
            raise HTTPException(409, f"{snapshot.CONFIRM_MARKER}: {doomed} of its {total:,} records are no longer in it. Nothing was removed. "
                                     "If the system's data was really cleared or replaced, confirm and it will be mirrored.")


def apply_sync(db: Session, connector: Connector, body: dict, now: datetime) -> dict:
    row = status_row(db, connector)
    events = body.get("events") or []
    deletes = body.get("deletes") or []
    sweep = body.get("sweep")
    if not isinstance(events, list) or not isinstance(deletes, list):
        raise HTTPException(400, "events and deletes must be lists")
    if len(events) > MAX_EVENTS or len(deletes) > MAX_DELETES:
        raise HTTPException(413, "Too many records in one request")
    if sweep is not None and (not isinstance(sweep, dict) or sweep.get("entity") not in ENTITIES or not {"after", "upto"} <= set(sweep)):
        raise HTTPException(400, "sweep is malformed")
    payloads = build_events(connector, events)
    keep = {p.entity_id for p in payloads}

    doomed: list[str] = []
    if sweep:
        doomed += _sweep_ids(db, connector, sweep, keep)
    if deletes:
        pairs = {(d.get("entity"), d.get("entity_id")) for d in deletes if isinstance(d, dict)}
        for entity in {p[0] for p in pairs}:
            ids = [i for e, i in pairs if e == entity]
            doomed += [r[0] for r in db.query(E.id).filter(E.connector_id == connector.id, E.entity == entity, E.entity_id.in_(ids))]
    guard_removals(db, connector, row, len(set(doomed)), now)

    result = snapshot.apply(db, connector, payloads, remove_missing=False, confirmed=True) if payloads else {"new_rows": 0, "updated_rows": 0}
    if doomed:
        sources.delete_events(db, connector.tenant_id, sorted(set(doomed)))
        db.commit()
        from app.core.cache import invalidate_tenant
        invalidate_tenant(connector.tenant_id)

    out = {"accepted": len(payloads), "new": result["new_rows"], "updated": result["updated_rows"], "removed": len(set(doomed)), "action": None, "verify": None}
    if body.get("final"):
        manifest = (body.get("manifest") or {})
        diffs = compare(stored_totals(db, connector.id), manifest.get("entities") or {})
        row = status_row(db, connector)
        row.verify = {"checked_at": now.isoformat(), "match": not diffs, "differences": diffs, "mode": body.get("mode"), "currency": manifest.get("currency"),
                      "skipped": manifest.get("skipped") or {}}
        if not diffs:
            row.last_synced_at = now
            row.confirm_until = None
            row.commands = {k: v for k, v in (row.commands or {}).items() if k != "resync"}
        else:
            out["action"] = "resync"
            if body.get("mode") == "full":                       # a full copy that still differs is not repairable by repeating it
                row.verify = {**row.verify, "unresolved": True}
        out["verify"] = row.verify
        connector.last_sync_at, connector.last_attempt_at = now, now
        connector.last_sync_status = "success" if not diffs or body.get("mode") != "full" else "partial"
        connector.last_sync_error = None if not diffs else "The copy differs from the system; it is being repaired."
    db.commit()
    return out


def take_heartbeat(db: Session, connector: Connector, body: dict, now: datetime) -> dict:
    row = status_row(db, connector)
    reported = (body.get("status") or {})
    state = str(reported.get("state") or "waiting")[:40]
    if state != row.state or (state in OK_STATES) != (row.state in OK_STATES):
        row.problem_since = None if state in OK_STATES else now
    elif state not in OK_STATES and row.problem_since is None:
        row.problem_since = now
    row.state, row.detail = state, str(reported.get("detail") or "")[:600] or None
    row.notice = (str(reported["notice"])[:600] if reported.get("notice") else None)
    row.status = {k: reported[k] for k in ("path", "path_source", "searched", "candidates", "scopes", "scope_id", "scope_name", "schema_version", "db") if k in reported}
    row.last_pass = body.get("last_pass") or {}
    row.agent = {**(body.get("agent") or {}), "profile": body.get("profile") or {}}
    row.last_seen_at = now
    ack = body.get("ack_check")
    if ack and row.check_requested_at and ack == row.check_requested_at.isoformat() and (row.check_done_at is None or row.check_done_at < row.check_requested_at):
        row.check_done_at = now
    connector.last_attempt_at = now
    if state in OK_STATES and connector.last_sync_status == "failed":
        connector.last_sync_status, connector.last_sync_error = "success", None
    db.commit()
    return commands_for(row, now)


def commands_for(row: AgentStatus, now: datetime) -> dict:
    cmd = dict(row.commands or {})
    if row.check_requested_at and (row.check_done_at is None or row.check_done_at < row.check_requested_at):
        cmd["check_now"] = row.check_requested_at.isoformat()
    if row.confirm_until and row.confirm_until > now:
        cmd["confirm"] = True
    return cmd


def summary(db: Session, connector: Connector, now: Optional[datetime] = None) -> dict:
    now = now or utcnow()
    row = db.get(AgentStatus, connector.id)
    state = effective_state(row, now)
    cfg = SYSTEMS.get((connector.config or {}).get("system"), {})
    checking = bool(row and row.check_requested_at and (row.check_done_at is None or row.check_done_at < row.check_requested_at)
                    and (now - row.check_requested_at).total_seconds() < 90)
    out = {"connector_id": connector.id, "system": (connector.config or {}).get("system"), "label": cfg.get("label", connector.name), "what": cfg.get("what"),
           "state": state, "headline": STATE_TEXT.get(state, state), "detail": row.detail if row and state == row.state else None,
           "notice": row.notice if row else None, "is_active": connector.is_active, "paused": bool((row.commands or {}).get("paused")) if row else False,
           "checking": checking, "last_seen_at": row.last_seen_at if row else None, "last_synced_at": row.last_synced_at if row else None,
           "status": row.status if row else {}, "last_pass": row.last_pass if row else {}, "verify": row.verify if row else {},
           "agent": row.agent if row else {}, "records": 0}
    out["records"] = db.query(func.count(E.id)).filter(E.connector_id == connector.id).scalar() or 0
    v = out["verify"] or {}
    out["in_sync"] = state == "connected" and bool(v.get("match")) and not v.get("unresolved")
    if state == "connected" and v and not v.get("match"):
        out["headline"] = "Connected; repairing the copy"
    return out


# ---- alerts -----------------------------------------------------------------------------------------------------

def detect_system_links(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list:
    """Raise an alert when a system's link has been down for more than ALERT_AFTER; close it the moment the link is back.
    A system that has never reported is being set up, not broken, so it raises nothing."""
    from app.core.detection import resolve_alert, _find_open_alert
    from app.core.detectors import _upsert
    from app.models.alerts import AlertSeverity
    now = now or utcnow()
    out = []
    for c in db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.connector_type == "system", Connector.is_active == True).all():  # noqa: E712
        row = db.get(AgentStatus, c.id)
        if row is None or row.last_seen_at is None:
            continue
        key = f"system_link:{c.id}"
        state = effective_state(row, now)
        paused = bool((row.commands or {}).get("paused"))
        down_since = row.last_seen_at + timedelta(seconds=ALIVE_SECONDS) if state == "offline" else row.problem_since
        healthy = state == "connected" or paused
        if healthy:
            open_alert = _find_open_alert(db, tenant_id, key)
            if open_alert:
                resolve_alert(db, open_alert, now)
            continue
        if down_since is None or now - down_since < ALERT_AFTER:
            continue
        hours = (now - down_since).total_seconds() / 3600
        reason = STATE_TEXT.get(state, state)
        label = SYSTEMS.get((c.config or {}).get("system"), {}).get("label", c.name)
        a = _upsert(
            db, tenant_id, key, now,
            {"connector_id": c.id, "connector": c.name, "state": state, "down_since": down_since.isoformat(), "last_seen_at": row.last_seen_at.isoformat()},
            dict(severity=AlertSeverity.high if hours >= 2 else AlertSeverity.medium, kind="silence",
                 title=f"{label} is not reaching the engine",
                 what_happened=(f"{label}'s link has been down since {down_since:%d %b %H:%M} UTC: {reason.lower()}. "
                                + (row.detail + " " if state == row.state and row.detail else "")
                                + "Until it is back, sales and spending figures from it may be missing recent records, so other alerts may look better or worse than reality."),
                 period_start=down_since, period_end=now, confidence=0.95, data_coverage_pct=0.0,
                 recommended_action=f"Open Data sources and check {label}. If the computer is off, switch it on; the missing records are picked up automatically and this alert closes itself."),
        )
        if a:
            out.append(a)
    return out


def watch_all_links() -> None:
    """Every minute: links that went down are noticed within minutes, links that came back are closed at once."""
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        tenants = [r[0] for r in db.query(Connector.tenant_id).filter(Connector.connector_type == "system", Connector.is_active == True).distinct()]  # noqa: E712
        for t in tenants:
            try:
                detect_system_links(db, t)
            except Exception:
                db.rollback()
    finally:
        db.close()
