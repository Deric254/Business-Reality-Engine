import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.core.config import get_settings
from app.core.auth import require_admin_key
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.rate_limit import RateLimitMiddleware
from app.api import health, connectors, events, alerts, entities, detection, coverage, cases, questions, tenants, forecasting, simulation, webhooks, system, auth, notifications, monitoring, audit as audit_api

# Without this, every logger.info() call in the app is silently dropped —
# Python's root logger defaults to WARNING with no handler configured.
# Only logger.error()/warning() calls were ever visible before this;
# caught while trying to confirm the scheduler actually started and
# seeing nothing in the log despite the code running correctly.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

logger = logging.getLogger("business_reality_engine")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.SCHEDULER_ENABLED:
        start_scheduler(settings.DETECTION_INTERVAL_MINUTES)
    else:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false")
    yield
    stop_scheduler()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# Rate limiting (SECURITY.md item 3) — hand-rolled, not slowapi (see
# app/core/rate_limit.py for why). Applies to every route via this
# middleware; the webhook route adds a stricter per-connector check on
# top (see app/api/webhooks.py).
app.add_middleware(RateLimitMiddleware)

# Without this, a real browser silently blocks every request from the
# frontend's origin (localhost:5173 in dev) — curl doesn't enforce CORS,
# so this gap doesn't show up in any of the API testing done via curl
# throughout this build; only a real browser hits it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Defence-in-depth headers on every response. The API serves JSON only, so the CSP forbids everything
    (the interactive docs pages need their own scripts and are exempted); credentials-bearing responses are never cached."""
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    h.setdefault("Cross-Origin-Resource-Policy", "same-site")
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    if request.url.path.startswith("/api/auth"):
        h.setdefault("Cache-Control", "no-store")
    if settings.ENV == "production":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.exception_handler(DataError)
async def handle_data_error(request: Request, exc: DataError):
    # e.g. malformed UUID/type sent by the client — client's fault, not a server failure.
    # Full detail goes to the log, never to the response body.
    logger.warning(f"DataError on {request.url.path}: {exc}")
    return JSONResponse(status_code=400, content={"detail": "Invalid request data"})


@app.exception_handler(IntegrityError)
async def handle_integrity_error(request: Request, exc: IntegrityError):
    logger.warning(f"IntegrityError on {request.url.path}: {exc}")
    return JSONResponse(status_code=409, content={"detail": "Request conflicts with existing data"})


@app.exception_handler(SQLAlchemyError)
async def handle_db_error(request: Request, exc: SQLAlchemyError):
    # Catch-all for anything else DB-related — never leak internals (Section 48).
    logger.error(f"Unhandled DB error on {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal error"})

# Auth model: user-facing routers enforce a per-tenant JWT inside each
# handler (authorize_tenant). The shared service key is accepted ONLY by
# /api/events (machine ingestion). /api/auth/login+refresh are public
# (throttled); register needs the admin key or a tenant owner token.
# /api/system needs the admin key. Webhooks use per-connector tokens.
app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(
    connectors.router, prefix="/api/connectors", tags=["connectors"],
)
app.include_router(
    events.router, prefix="/api/events", tags=["events"],
)
app.include_router(
    alerts.router, prefix="/api/alerts", tags=["alerts"],
)
app.include_router(
    entities.router, prefix="/api/entities", tags=["entities"],
)
app.include_router(
    detection.router, prefix="/api/detection", tags=["detection"],
)
app.include_router(
    coverage.router, prefix="/api/coverage", tags=["coverage"],
)
app.include_router(
    cases.router, prefix="/api/cases", tags=["cases"],
)
app.include_router(
    questions.router, prefix="/api/questions", tags=["questions"],
)
app.include_router(
    tenants.router, prefix="/api/tenants", tags=["tenants"],
)
app.include_router(
    forecasting.router, prefix="/api/forecast", tags=["forecast"],
)
app.include_router(
    simulation.router, prefix="/api/simulate", tags=["simulate"],
)
# Deliberately NOT behind require_api_key — external systems calling this
# don't have our service key. Auth is the per-connector token in the URL
# itself, verified inside the handler (see app/api/webhooks.py).
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(monitoring.router, prefix="/api/monitoring", tags=["monitoring"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])
app.include_router(
    system.router, prefix="/api/system", tags=["system"],
    dependencies=[Depends(require_admin_key)],
)
app.include_router(
    auth.router, prefix="/api/auth", tags=["auth"],
)
app.include_router(audit_api.router, prefix="/api/audit", tags=["audit"])


@app.get("/")
def root():
    return {"service": settings.APP_NAME, "status": "running"}
