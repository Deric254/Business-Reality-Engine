"""
Alerts should follow the data, not the clock. After new records arrive for a business, its detectors run shortly after
(a few minutes at most), instead of waiting for the next scheduled tick that a sleeping free-tier server may never get.

Runs happen in a background thread, at most one at a time per business and not more often than MIN_GAP_SECONDS;
records that arrive while one is running or waiting are covered by one more run, so nothing is missed and a busy
webhook cannot start a run per event.
"""
import logging
import os
import threading
import time

from app.core.db import SessionLocal

logger = logging.getLogger("business_reality_engine")

MIN_GAP_SECONDS = 120
_lock = threading.Lock()
_state: dict[str, dict] = {}                 # tenant_id -> {running, waiting, pending, last}


def kick(tenant_ids: set[str]) -> None:
    if os.getenv("DETECT_AFTER_INGEST", "true").lower() != "true":
        return
    for tenant_id in tenant_ids:
        _schedule(tenant_id)


def _schedule(tenant_id: str) -> None:
    with _lock:
        st = _state.setdefault(tenant_id, {"running": False, "waiting": False, "pending": False, "last": 0.0})
        if st["running"] or st["waiting"]:
            st["pending"] = True                       # one more run will cover what arrived meanwhile
            return
        st["waiting"] = True
        wait = max(0.0, MIN_GAP_SECONDS - (time.monotonic() - st["last"]))
    timer = threading.Timer(wait, _run, args=(tenant_id,))
    timer.daemon = True
    timer.start()


def _run(tenant_id: str) -> None:
    with _lock:
        st = _state[tenant_id]
        st["waiting"], st["running"], st["pending"] = False, True, False
    try:
        _execute(tenant_id)
    except Exception as e:
        logger.error(f"Detection after new data failed for tenant={tenant_id}: {e}")
    finally:
        with _lock:
            st["running"], st["last"] = False, time.monotonic()
            again, st["pending"] = st["pending"], False
        if again:
            _schedule(tenant_id)


def _execute(tenant_id: str) -> None:
    from app.core.detectors import run_tenant_cycle          # imported here: detectors import ingestion, which calls kick
    from app.core.scheduler import _metrics_for_tenant
    db = SessionLocal()
    try:
        run_tenant_cycle(db, tenant_id, _metrics_for_tenant(db, tenant_id))
    finally:
        db.close()
