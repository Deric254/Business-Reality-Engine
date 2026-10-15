"""
Rate limiting (SECURITY.md item 3).

Hand-rolled, not slowapi. slowapi's middleware relies on walking
app.routes and matching each request against route objects to decide
whether a route is "exempt" — but this FastAPI version resolves included
routers lazily via _IncludedRouter objects instead of eagerly flattening
them into plain Route objects at startup. slowapi's route lookup returns
None for every one of them, which its own code treats as "can't find the
route -> exempt from limiting." Confirmed directly: fired 310 requests
at a 300/minute default limit and got zero 429s; traced it to
_find_route_handler returning None for a real, live route. Not a config
mistake — a real incompatibility between this slowapi version and this
FastAPI version's routing internals.

Simple in-memory fixed-window counter instead. Auditable in a few lines,
no dependency on framework internals that can silently break again.

Two backends, chosen at import time:
  - REDIS_URL set   -> RedisFixedWindowLimiter (INCR+EXPIRE), one shared
                       count across every worker/machine.
  - REDIS_URL unset -> FixedWindowLimiter (in-memory), per-process only.
Redis runtime failure FAILS OPEN (request allowed, error logged): a Redis
outage must not take the whole API down. Stated tradeoff.
Behind a reverse proxy, request.client.host is the proxy's address; set
TRUSTED_PROXY_HOPS to read the real client from X-Forwarded-For.
"""
import logging
import os
import time
import threading
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class FixedWindowLimiter:
    """
    key -> (window_start_epoch_seconds, count). A new window starts the
    first time a key is seen after the previous window has fully elapsed
    — not a sliding window (a burst right at a window boundary can allow
    up to ~2x the limit over a short span), which is an accepted, stated
    tradeoff for simplicity. Thread-safe via a single lock; fine at this
    scale, would need something sharper under heavy concurrent load.
    """
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._state: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds). Increments on allow."""
        now = time.time()
        with self._lock:
            window_start, count = self._state.get(key, (now, 0))
            if now - window_start >= self.window_seconds:
                window_start, count = now, 0
            if count >= self.limit:
                retry_after = int(self.window_seconds - (now - window_start)) + 1
                return False, retry_after
            self._state[key] = (window_start, count + 1)
            return True, 0


logger = logging.getLogger("business_reality_engine")


class RedisFixedWindowLimiter:
    """Shared fixed-window counter. Key embeds the window number, so expiry
    is automatic and no cross-process clock agreement beyond NTP is needed."""
    def __init__(self, client, limit: int, window_seconds: int, prefix: str):
        self._redis = client
        self.limit = limit
        self.window_seconds = window_seconds
        self._prefix = prefix

    def check(self, key: str) -> tuple[bool, int]:
        try:
            redis_key = f"{self._prefix}:{key}:{int(time.time()) // self.window_seconds}"
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, self.window_seconds, nx=True)
            pipe.ttl(redis_key)
            count, _, ttl = pipe.execute()
            if count > self.limit:
                return False, max(int(ttl), 1)
            return True, 0
        except Exception as e:
            logger.error(f"Rate limiter Redis error (failing open): {e}")
            return True, 0


def _build_limiter(limit: int, window_seconds: int, prefix: str):
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            import redis
            client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1)
            client.ping()
            return RedisFixedWindowLimiter(client, limit, window_seconds, prefix)
        except Exception as e:
            logger.error(f"REDIS_URL set but unreachable ({e}); falling back to in-memory limiter")
    return FixedWindowLimiter(limit, window_seconds)


# General API: 300/minute per source IP. Not a business-tier quota — every
# tenant currently shares one service key (SECURITY.md item 1), so IP is
# the most meaningful axis available today; protects against one runaway
# client, not meant as a per-tenant limit.
general_limiter = _build_limiter(int(os.environ.get("RATE_LIMIT_GENERAL_PER_MIN", "300")), 60, "ratelimit:general")

# Webhooks: 100/minute per CONNECTOR, not per IP — one external system's
# traffic must never affect another's, even from behind the same NAT.
webhook_limiter = _build_limiter(int(os.environ.get("RATE_LIMIT_WEBHOOK_PER_MIN", "100")), 60, "ratelimit:webhook")


auth_limiter = _build_limiter(int(os.environ.get("RATE_LIMIT_AUTH_PER_MIN", "10")), 60, "ratelimit:auth")


def _client_ip(request: Request) -> str:
    hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0") or 0)
    if hops > 0:
        fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(fwd) >= hops:
            return fwd[-hops]  # Nth-from-right: the entry our own proxy appended
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies general_limiter to every request by source IP."""
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api"):      # web app files and the liveness probe are not API traffic
            return await call_next(request)
        key = _client_ip(request)
        allowed, retry_after = general_limiter.check(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


def check_webhook_rate_limit(connector_id: str):
    """Called explicitly inside the webhook handler, where connector_id is
    already available as a resolved path param — no fragile framework
    introspection needed."""
    allowed, retry_after = webhook_limiter.check(connector_id)
    if not allowed:
        raise HTTPException(
            status_code=429, detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
