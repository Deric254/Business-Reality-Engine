"""
"Why does my dashboard show this?" answered from the facts, per source, in words an owner can act on.

An empty or zero dashboard has a handful of real causes: nothing connected, a source that never synced or failed (and
why), a source that returned nothing (private sheet, wrong tab), records left out (and why), records that are all
older than the period shown, a mapping that found no amounts, a database not yet migrated, or no AI connected for the
questions that need it. This checks each and says which one it is.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import schema, sources
from app.core.tenant_time import tenant_zone
from app.models.core import BusinessEvent as E, Connector

VIEW_CHOICES = (7, 30, 90, 180)            # the periods the Overview can show
_PULL = ("url", "database", "sftp", "email", "shared_folder")
_NOTHING_RETURNED = {
    "url": "It returned no records. The sheet may be empty, shared privately (it must be shared as 'Anyone with the link'), or the link may point at another tab.",
    "file": "No file has been uploaded to it yet.",
    "webhook": "Nothing has been sent to its address yet.",
    "database": "Its query returned no rows.",
}


def _finding(level: str, text: str, source: Optional[str] = None, action: Optional[str] = None) -> dict:
    return {"level": level, "source": source, "text": text, "action": action}


def _day(ts: datetime, tz) -> str:
    return ts.replace(tzinfo=timezone.utc).astimezone(tz).strftime("%d %b %Y")


def _ai_connected(db: Session, tenant_id: str) -> bool:
    from app.reasoning.llm_providers import get_provider
    try:
        get_provider(db, tenant_id)
        return True
    except Exception:
        return False


def run(db: Session, tenant_id: str, period_days: int = 30, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    tz = tenant_zone(db, tenant_id)
    since = now - timedelta(days=period_days)
    counted = E.validation_status == "validated"
    rows = {r[0]: r[1:] for r in db.query(
        E.connector_id,
        func.count().filter(counted), func.count().filter(E.validation_status == "failed"),
        func.count().filter(E.validation_status.in_(("duplicate", "matched"))),
        func.min(E.timestamp).filter(counted), func.max(E.timestamp).filter(counted),
        func.count().filter(counted, E.timestamp >= since, E.timestamp < now), func.count().filter(counted, E.amount.is_(None)),
    ).filter(E.tenant_id == tenant_id).group_by(E.connector_id)}

    findings, out = [], []
    connectors = db.query(Connector).filter(Connector.tenant_id == tenant_id).order_by(Connector.created_at).all()
    for c in connectors:
        n, failed, matched, first, last, in_view, no_amount = rows.get(c.id, (0, 0, 0, None, None, 0, 0))
        out.append({"id": c.id, "name": c.name, "type": c.connector_type, "active": bool(c.is_active), "counted": n, "rejected": failed,
                    "matched": matched, "first_record": _day(first, tz) if first else None, "last_record": _day(last, tz) if last else None,
                    "in_view": in_view, "last_checked": c.last_sync_at.isoformat() if c.last_sync_at else None,
                    "status": c.last_sync_status, "error": c.last_sync_error})
        if not c.is_active:
            findings.append(_finding("warning", "It is paused, so it will not update.", c.name, "Resume it on the Data page."))
        if c.last_sync_status == "failed":
            findings.append(_finding("problem", f"The last sync failed: {c.last_sync_error or 'no reason was recorded'}", c.name, "Fix that, then press Sync now."))
        elif c.connector_type in _PULL and c.last_attempt_at is None and not n:
            findings.append(_finding("problem", "It has never been synced.", c.name, "Press Sync now."))
        elif not n and not failed:
            findings.append(_finding("problem", _NOTHING_RETURNED.get(c.connector_type, "It holds no records."), c.name))
        if failed:
            reasons = {}
            for r in sources.rejected(db, c, 50):
                for why in r["reasons"]:
                    reasons[why] = reasons.get(why, 0) + 1
            top = ", ".join(f"{why} ({k})" for why, k in sorted(reasons.items(), key=lambda kv: -kv[1])[:2])
            findings.append(_finding("problem" if not n else "warning", f"{failed:,} record{'s' if failed != 1 else ''} left out of your totals: {top}.", c.name,
                                     "Open the list under the source, fix them there and sync again."))
        if n and not in_view:
            findings.append(_finding("warning", f"Its records run from {_day(first, tz)} to {_day(last, tz)}, so none fall in the last {period_days} days.", c.name))
        if n and no_amount > 0.5 * n:
            findings.append(_finding("warning", f"Most of its records ({no_amount:,} of {n:,}) have no amount, so they add nothing to sales or expenses.", c.name,
                                     "Check that the Amount column is mapped correctly."))

    total = sum(r["counted"] for r in out)
    in_view_total = sum(r["in_view"] for r in out)
    oldest, newest = db.query(func.min(E.timestamp), func.max(E.timestamp)).filter(E.tenant_id == tenant_id, counted).one()
    suggest = next((d for d in VIEW_CHOICES if d > period_days and newest and newest >= now - timedelta(days=d)), None) if not in_view_total else None
    if not connectors:
        findings.insert(0, _finding("problem", "No source is connected yet.", None, "Connect a sheet or file on the Data page, or add entries by hand."))
    elif total and not in_view_total:
        findings.insert(0, _finding("warning", f"Your records run from {_day(oldest, tz)} to {_day(newest, tz)}, but the dashboard shows the last {period_days} days, and none of your records are in it.",
                                    None, "Add recent records, or widen the period." if suggest else "Add recent records."))
    schema_state = schema.status(db)
    if not schema_state["current"]:
        findings.insert(0, _finding("problem", "The database is behind this version of the app, which can make syncs fail.", None,
                                    "Redeploy with RUN_MIGRATIONS=1 or run `alembic upgrade head`."))
    ai = _ai_connected(db, tenant_id)
    if not ai:
        findings.append(_finding("info", "The AI assistant is not connected, so Ask answers only a fixed set of questions.", None,
                                 "Connect it in Settings, or set ANTHROPIC_API_KEY on the server."))

    problems, warnings = any(f["level"] == "problem" for f in findings), any(f["level"] == "warning" for f in findings)
    headline = ("Something is stopping your data from showing." if problems else
                "Your data is there, but not in the period shown." if total and not in_view_total else
                "A few things are worth a look." if warnings else "Your data looks healthy.")
    return {"verdict": "problem" if problems else "attention" if warnings else "ok", "headline": headline, "findings": findings, "sources": out,
            "range": {"counted": total, "first": _day(oldest, tz) if oldest else None, "last": _day(newest, tz) if newest else None,
                      "in_view": in_view_total, "period_days": period_days, "suggest_days": suggest},
            "ai_connected": ai, "schema": schema_state}
