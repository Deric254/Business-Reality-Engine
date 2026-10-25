"""
Small TTL cache for expensive, read-mostly results (dashboard KPIs).

Redis when REDIS_URL is set (shared by every worker/machine), otherwise a
per-process dict. Keys always include the tenant id, so one tenant can never
be served another's cached value. Any Redis error degrades to "no cache"
(compute normally) — a cache must never take a request down. Staleness is
bounded by the TTL, and ingestion drops the tenant's entries (invalidate_prefix) so new data shows at once.
"""
import json
import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("business_reality_engine")

_redis = None
_redis_checked = False
_local: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()
_LOCAL_MAX = 5000


def _client():
    global _redis, _redis_checked
    if not _redis_checked:
        _redis_checked = True
        url = os.environ.get("REDIS_URL")
        if url:
            try:
                import redis
                c = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
                c.ping()
                _redis = c
            except Exception as e:
                logger.error(f"Cache: REDIS_URL set but unreachable ({e}); using in-process cache")
    return _redis


def cached(key: str, ttl_seconds: int, compute: Callable[[], Any]) -> Any:
    """Return the cached JSON-serializable value for `key`, computing and storing it on a miss."""
    if ttl_seconds <= 0:
        return compute()
    r = _client()
    if r is not None:
        try:
            hit = r.get(key)
            if hit is not None:
                return json.loads(hit)
        except Exception as e:
            logger.error(f"Cache read failed (computing instead): {e}")
    else:
        with _lock:
            entry = _local.get(key)
            if entry and entry[0] > time.time():
                return json.loads(entry[1])
    value = compute()
    try:
        payload = json.dumps(value, default=str)
        if r is not None:
            r.set(key, payload, ex=ttl_seconds)
        else:
            with _lock:
                if len(_local) >= _LOCAL_MAX:
                    now = time.time()
                    for k in [k for k, (exp, _) in _local.items() if exp <= now] or list(_local)[:_LOCAL_MAX // 10]:
                        _local.pop(k, None)
                _local[key] = (time.time() + ttl_seconds, payload)
    except Exception as e:
        logger.error(f"Cache write failed: {e}")
    return value


def clear_local():
    with _lock:
        _local.clear()


def invalidate_tenant(tenant_id: str) -> None:
    """Drop everything cached from a business's records: its numbers, merge suggestions and name lists."""
    for prefix in (f"kpis:{tenant_id}:", f"analytics:{tenant_id}:", f"overlaps:{tenant_id}", f"names:{tenant_id}"):
        invalidate_prefix(prefix)


def invalidate_prefix(prefix: str) -> None:
    """Drop every cached entry whose key starts with `prefix` (e.g. f"kpis:{tenant_id}:"). Never raises."""
    try:
        with _lock:
            for k in [k for k in _local if k.startswith(prefix)]:
                _local.pop(k, None)
        r = _client()
        if r is not None:
            for k in r.scan_iter(match=prefix + "*", count=200):
                r.delete(k)
    except Exception as e:
        logger.error(f"Cache invalidate failed: {e}")
