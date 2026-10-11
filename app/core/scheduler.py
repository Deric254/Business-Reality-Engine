"""
Scheduler (Sections 17-18, and the core promise in Section 1: "always-on").

Detection has only run on manual trigger up to this point — every test in
this build called /api/detection/run/historical-deviation by hand. That
contradicts the product's own premise. This wires it to a real interval.

Deliberately simple for now: BackgroundScheduler running in-process,
one interval for every tenant/metric. A real system would honor each
connector's own expected_frequency (Section 17) and let a business
configure which metrics to watch (Section 50) — that's not built yet.
This is the honest v1: every active tenant gets sale.amount and
expense.amount checked on the same fixed interval.
"""
import logging
import os
import uuid
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import distinct

from app.core.db import SessionLocal
from app.core.detectors import run_tenant_cycle
from app.core.notifications import dispatch_cycle
from app.models.config import MonitoredMetric
from app.models.core import Connector, SchedulerRun

logger = logging.getLogger("business_reality_engine")

_scheduler: BackgroundScheduler | None = None

# Multi-worker safety: with N workers each running an in-process scheduler,
# every cycle would run N times. A Redis lock (SET NX EX) lets exactly one
# proceed; the rest log "skipped". Without REDIS_URL there is no lock
# (single-process assumption). Release is atomic compare-and-delete (Lua)
# so a process never deletes a lock another process acquired after TTL expiry.
_LOCK_KEY = "scheduler:detection_cycle:lock"
_PROCESS_ID = str(uuid.uuid4())
_RELEASE_LUA = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


def _get_redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    try:
        import redis
        c = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        c.ping()
        return c
    except Exception as e:
        logger.error(f"Scheduler: REDIS_URL set but unreachable ({e}); running without lock")
        return None


def _acquire_cycle_lock(client, ttl_seconds: int) -> bool:
    if client is None:
        return True
    try:
        return bool(client.set(_LOCK_KEY, _PROCESS_ID, nx=True, ex=ttl_seconds))
    except Exception as e:
        logger.error(f"Scheduler lock error (proceeding without lock): {e}")
        return True


def _release_cycle_lock(client):
    if client is None:
        return
    try:
        client.eval(_RELEASE_LUA, 1, _LOCK_KEY, _PROCESS_ID)
    except Exception as e:
        logger.error(f"Scheduler lock release error: {e}")

# Metrics checked for every tenant on each cycle. Section 50 (per-business
# configuration of what to monitor) would replace this fixed list.
_DEFAULT_METRICS = [
    {"entity": "sale", "field": "amount", "direction": "drop"},
    {"entity": "expense", "field": "amount", "direction": "rise"},
]


def _metrics_for_tenant(db, tenant_id: str) -> list[dict]:
    """The tenant's own monitored metrics (Section 50). A tenant with no rows at
    all gets the defaults; once it has any row, only its ENABLED rows run."""
    rows = db.query(MonitoredMetric).filter(MonitoredMetric.tenant_id == tenant_id).all()
    if not rows:
        return list(_DEFAULT_METRICS)
    return [
        {"entity": r.entity, "field": r.field, "period_days": r.period_days,
         "baseline_periods": r.baseline_periods, "deterioration_threshold": float(r.threshold_pct) / 100,
         "direction": r.direction,
         "absolute_threshold": float(r.absolute_threshold) if r.absolute_threshold is not None else None}
        for r in rows if r.enabled
    ]


def _active_tenant_ids(db) -> list[str]:
    rows = db.query(distinct(Connector.tenant_id)).filter(Connector.is_active == True).all()
    return [r[0] for r in rows]


def run_detection_cycle():
    """One pass: every active tenant, every monitored metric. Errors on one
    tenant/metric must never abort the cycle for the rest — logged and
    skipped, not raised. Every run is recorded in scheduler_runs (Section 51)
    so 'is the scheduler actually running' is a query, not a log grep."""
    redis_client = _get_redis()
    if not _acquire_cycle_lock(redis_client, ttl_seconds=300):
        logger.info("Detection cycle skipped: another worker holds the lock")
        return
    db = SessionLocal()
    run = SchedulerRun(started_at=datetime.utcnow(), status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    errors = []
    try:
        tenant_ids = _active_tenant_ids(db)
        logger.info(f"Detection cycle starting: {len(tenant_ids)} active tenant(s)")
        checked = 0
        for tenant_id in tenant_ids:
            metrics = _metrics_for_tenant(db, tenant_id)
            try:
                result = run_tenant_cycle(db, tenant_id, metrics)
                checked += len(metrics)
                if result["alerts"]:
                    logger.info(f"Detection cycle: {len(result['alerts'])} alert(s) touched for tenant={tenant_id}; post={result['post']}")
                if result["errors"]:
                    errors.append({"tenant_id": tenant_id, "error": f"{result['errors']} detector step(s) failed"})
            except Exception as e:
                # One tenant's failure must not take down the cycle for the rest.
                db.rollback()
                logger.error(f"Detection cycle failed for tenant={tenant_id}: {e}")
                errors.append({"tenant_id": tenant_id, "error": str(e)})

        run.completed_at = datetime.utcnow()
        run.tenants_checked = str(len(tenant_ids))
        run.checks_run = str(checked)
        run.errors_count = str(len(errors))
        run.error_detail = errors or None
        run.status = "completed"
        db.commit()
        logger.info(f"Detection cycle complete: {checked} tenant/metric checks run, {len(errors)} error(s)")
    except Exception as e:
        # A failure OUTSIDE the per-tenant loop (e.g. can't even list active
        # tenants — a DB connectivity issue) is different from a per-tenant
        # error: the whole cycle genuinely failed, not just one check.
        run.completed_at = datetime.utcnow()
        run.status = "failed"
        run.error_detail = [{"error": str(e)}]
        db.commit()
        logger.error(f"Detection cycle failed entirely: {e}")
    finally:
        db.close()
        _release_cycle_lock(redis_client)


def run_dispatch_cycle():
    """Turn new alerts into deliveries and send what is due (Section 49).
    Safe on many workers at once (idempotent enqueue, SKIP LOCKED sends), so
    it deliberately does not use the single-runner detection lock."""
    db = SessionLocal()
    try:
        stats = dispatch_cycle(db)
        if any(stats.values()):
            logger.info(f"Notification dispatch: {stats}")
    except Exception as e:
        db.rollback()
        logger.error(f"Notification dispatch failed: {e}")
    finally:
        db.close()


def start_scheduler(interval_minutes: int):
    global _scheduler
    if _scheduler is not None:
        return  # already running — don't double-start on reload
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_detection_cycle, "interval", minutes=interval_minutes,
        id="detection_cycle", next_run_time=None,  # first run scheduled normally, not immediate
    )
    _scheduler.add_job(run_dispatch_cycle, "interval", minutes=1, id="notification_dispatch",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info(f"Scheduler started — detection cycle every {interval_minutes} minute(s)")


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
