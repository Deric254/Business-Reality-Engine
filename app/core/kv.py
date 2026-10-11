"""
Tiny key-value store with TTL for security state (revocation, lockout counters).

Redis when REDIS_URL is set (shared by every worker/machine); otherwise a per-process dict
(correct for one process; with several processes and no Redis each keeps its own state, which
is stated in SECURITY.md). A Redis error never takes a request down: reads fall back to the
local copy, which every write also updates, so a single process still enforces its own state.
"""
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("business_reality_engine")

_redis = None
_checked = False
_local: dict[str, tuple[float, str]] = {}      # key -> (expires_at_epoch, value)
_lock = threading.Lock()
_LOCAL_MAX = 50_000


def _client():
    global _redis, _checked
    if not _checked:
        _checked = True
        url = os.environ.get("REDIS_URL")
        if url:
            try:
                import redis
                c = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
                c.ping()
                _redis = c
            except Exception as e:
                logger.error(f"KV: REDIS_URL set but unreachable ({e}); using in-process state")
    return _redis


def _reset_for_tests():
    global _redis, _checked
    with _lock:
        _local.clear()
    _redis, _checked = None, False


def _purge(now: float):
    if len(_local) > _LOCAL_MAX:
        for k in [k for k, (exp, _) in _local.items() if exp <= now][: _LOCAL_MAX // 2]:
            _local.pop(k, None)
        if len(_local) > _LOCAL_MAX:           # still full of live keys: drop the oldest-expiring half
            for k, _ in sorted(_local.items(), key=lambda kv: kv[1][0])[: _LOCAL_MAX // 2]:
                _local.pop(k, None)


def set_ex(key: str, value: str, ttl_seconds: int) -> None:
    ttl = max(1, int(ttl_seconds))
    now = time.time()
    with _lock:
        _purge(now)
        _local[key] = (now + ttl, value)
    c = _client()
    if c:
        try:
            c.set(key, value, ex=ttl)
        except Exception as e:
            logger.error(f"KV set failed ({e}); kept in-process only")


def get(key: str) -> Optional[str]:
    c = _client()
    if c:
        try:
            v = c.get(key)
            if v is not None:
                return v
        except Exception as e:
            logger.error(f"KV get failed ({e}); using in-process state")
    now = time.time()
    with _lock:
        item = _local.get(key)
        if not item:
            return None
        if item[0] <= now:
            _local.pop(key, None)
            return None
        return item[1]


def delete(key: str) -> None:
    with _lock:
        _local.pop(key, None)
    c = _client()
    if c:
        try:
            c.delete(key)
        except Exception as e:
            logger.error(f"KV delete failed ({e})")


def incr_ex(key: str, ttl_seconds: int) -> int:
    """Atomic increment; the TTL starts at the first increment of a window (fixed window)."""
    ttl = max(1, int(ttl_seconds))
    c = _client()
    if c:
        try:
            p = c.pipeline()
            p.incr(key); p.expire(key, ttl, nx=True)
            return int(p.execute()[0])
        except Exception as e:
            logger.error(f"KV incr failed ({e}); using in-process counter")
    now = time.time()
    with _lock:
        _purge(now)
        exp, val = _local.get(key, (0.0, "0"))
        if exp <= now:
            exp, val = now + ttl, "0"
        n = int(val) + 1
        _local[key] = (exp, str(n))
        return n


def ttl(key: str) -> int:
    c = _client()
    if c:
        try:
            t = c.ttl(key)
            if t and t > 0:
                return int(t)
        except Exception:
            pass
    with _lock:
        item = _local.get(key)
    return max(0, int(item[0] - time.time())) if item else 0
