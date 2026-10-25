"""One cheap call that open screens poll, so what is on screen never trails the data behind it."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth_users import authorize_tenant
from app.core.db import get_db

router = APIRouter()

_PULSE_SQL = text("""
SELECT
  (SELECT data_version FROM tenants WHERE id = :t),
  (SELECT count(*) FROM alerts WHERE tenant_id = :t),
  (SELECT max(updated_at) FROM alerts WHERE tenant_id = :t),
  (SELECT count(*) FROM goals WHERE tenant_id = :t),
  (SELECT max(updated_at) FROM goals WHERE tenant_id = :t),
  (SELECT count(*) FROM connectors WHERE tenant_id = :t),
  (SELECT max(last_sync_at) FROM connectors WHERE tenant_id = :t),
  (SELECT max(last_sync_at) FROM connectors WHERE tenant_id = :t AND last_sync_status = 'success'),
  (SELECT string_agg(coalesce(last_sync_status, ''), ',' ORDER BY id) FROM connectors WHERE tenant_id = :t),
  (SELECT count(*) FROM notification_channels WHERE tenant_id = :t),
  (SELECT count(*) FROM monitored_metrics WHERE tenant_id = :t)
""")


@router.get("/pulse")
def pulse(tenant_id: str, response: Response, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """`token` changes whenever anything a screen shows has changed: the records (data_version), the alerts, the targets, the
    sources and their sync status. Screens compare it with the last one they saw and reload when it differs.
    `last_synced_at` (UTC) is the newest successful check of any source, for the 'Updated ... ago' line."""
    authorize_tenant(authorization, tenant_id)
    row = db.execute(_PULSE_SQL, {"t": tenant_id}).one()
    response.headers["Cache-Control"] = "no-store"
    last = row[7]
    return {"token": "|".join("" if v is None else str(v) for v in row),
            "last_synced_at": None if last is None else last.isoformat() + "Z"}
