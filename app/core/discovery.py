"""'Your own system': the agent describes the databases on the business's computer (names only), the engine proposes how to read one,
the owner reviews and confirms, and the confirmed reading goes back to the agent as its profile.

Nothing here reads business data. The agent sends table and column names, types and row counts; the profile that comes back
is built from names that were checked against that description (see schema_mapper), never from text the AI wrote."""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import schema_mapper
from app.core.systems import status_row
from app.models.agent import AgentStatus
from app.models.core import Connector

KINDS = ("postgres", "mysql", "mssql", "odbc")        # the database servers the agent can read (agent/dbconn.py)
MAX_DATABASES, MAX_TABLES, MAX_COLUMNS, MAX_NAME = 25, 200, 80, 120


def _name(v) -> str:
    return str(v)[:MAX_NAME]


def clean_found(databases) -> list[dict]:
    """What the agent reported, cut down to names, types and counts. Anything else it sent is dropped."""
    out = []
    for d in (databases if isinstance(databases, list) else [])[:MAX_DATABASES]:
        if not isinstance(d, dict) or not d.get("path"):
            continue
        tables = []
        for t in (d.get("tables") or [])[:MAX_TABLES]:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            cols = [{"name": _name(c["name"]), "type": _name(c.get("type", ""))[:30], "pk": bool(c.get("pk"))}
                    for c in (t.get("columns") or [])[:MAX_COLUMNS] if isinstance(c, dict) and c.get("name")]
            rows = t.get("rows")
            tables.append({"name": _name(t["name"]), "schema": _name(t["schema"]) if t.get("schema") else None, "table": _name(t.get("table") or t["name"]), "rows": rows if isinstance(rows, int) and rows >= 0 else None, "columns": cols})
        src = d.get("source") if isinstance(d.get("source"), dict) else None
        out.append({"path": str(d["path"])[:500], "source": {"kind": str(src.get("kind"))[:20], "name": _name(src.get("name") or "")} if src and src.get("kind") in KINDS else None, "size": d.get("size") if isinstance(d.get("size"), int) else None,
                    "modified": d.get("modified") if isinstance(d.get("modified"), int) else None,
                    "error": str(d["error"])[:200] if d.get("error") else None, "tables": tables})
    return out


def take_found(db: Session, connector: Connector, databases, now: datetime) -> dict:
    row = status_row(db, connector)
    cur = dict(row.discovery or {})
    found = clean_found(databases)
    paths = {d["path"] for d in found}
    cur.update(found=found, scanned_at=now.isoformat(), proposals={p: v for p, v in (cur.get("proposals") or {}).items() if p in paths})
    row.discovery = cur
    cmd = {k: v for k, v in (row.commands or {}).items() if k != "discover"}      # the rescan that was asked for has been done
    row.commands = cmd
    db.commit()
    return {"ok": True, "found": len(found)}


def _database(row: AgentStatus, path: str) -> dict:
    d = next((x for x in (row.discovery or {}).get("found", []) if x["path"] == path), None)
    if d is None or d.get("error"):
        raise HTTPException(404, "That database was not among the ones the agent found. Scan again.")
    return d


def view(row: AgentStatus | None) -> dict:
    d = (row.discovery if row else None) or {}
    return {"roles": {k: {"label": v["label"], "required": list(v["required"]), "amount_label": v.get("amount_label", "Amount")} for k, v in schema_mapper.ROLE_INFO.items()}, "scanned_at": d.get("scanned_at"), "found": d.get("found", []), "proposals": d.get("proposals", {}), "chosen": d.get("chosen"),
            "scan_requested": bool(row and (row.commands or {}).get("discover"))}


def request_scan(db: Session, connector: Connector, now: datetime) -> None:
    row = status_row(db, connector)
    row.commands = {**(row.commands or {}), "discover": now.isoformat()}
    row.check_requested_at = now
    db.commit()


def propose(db: Session, connector: Connector, path: str, provider) -> dict:
    row = status_row(db, connector)
    summary = {**_database(row, path), "path": path}
    out = schema_mapper.propose(summary, provider)
    cur = dict(row.discovery or {})
    cur["proposals"] = {**(cur.get("proposals") or {}), path: {k: out[k] for k in ("business", "currency", "tables", "notes", "used_ai")}}
    row.discovery = cur
    db.commit()
    return cur["proposals"][path]


def confirm(db: Session, connector: Connector, path: str, tables: list, business: dict, currency, now: datetime) -> dict:
    """The owner's reading (possibly edited) becomes the agent's profile, after every name is checked against the real schema."""
    row = status_row(db, connector)
    summary = {**_database(row, path), "path": path}
    good, notes = schema_mapper.checked({"tables": tables}, summary)
    if not good:
        raise HTTPException(400, "Nothing here can be read as sales, expenses or refunds yet: each table needs a date column and an amount column. " + " ".join(notes[:3]))
    biz = {"kind": str((business or {}).get("kind") or "")[:80], "description": str((business or {}).get("description") or "")[:600]}
    ccy = str(currency or "").upper()
    profile = schema_mapper.build_profile(good, summary, biz, ccy if len(ccy) == 3 and ccy.isalpha() else None)
    cur = dict(row.discovery or {})
    cur["chosen"] = {"path": path, "business": biz, "tables": good, "confirmed_at": now.isoformat()}
    row.discovery = cur
    row.commands = {**(row.commands or {}), "profile": profile, "resync": True}
    row.check_requested_at = now
    db.commit()
    return {"chosen": cur["chosen"], "notes": notes}
