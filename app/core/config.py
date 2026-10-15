import base64
import hashlib
import hmac
import logging
import os
from functools import lru_cache

logger = logging.getLogger("business_reality_engine")


def normalize_database_url(url: str) -> str:
    """Hosts (Render, Neon, Heroku, Supabase) hand out `postgres://...`; SQLAlchemy 2 only accepts `postgresql://`.
    That one mismatch crashes the app at import time, so the service boots, dies and restarts in a loop."""
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _derive(master: str, purpose: str) -> bytes:
    return hmac.new(master.encode(), purpose.encode(), hashlib.sha256).digest()


def derive_missing_secrets() -> None:
    """One master secret is enough to run. If JWT_SECRET_KEY (or APP_SECRET) is set, any of API_KEY and
    CONNECTOR_ENCRYPTION_KEY that was not set is derived from it, so a forgotten variable no longer stops the service
    from booting. Set them explicitly if you want to rotate them independently. ADMIN_API_KEY is never derived."""
    master = os.getenv("APP_SECRET") or os.getenv("JWT_SECRET_KEY") or ""
    if len(master) < 32:
        return
    if not os.getenv("JWT_SECRET_KEY"):
        os.environ["JWT_SECRET_KEY"] = base64.urlsafe_b64encode(_derive(master, "jwt")).decode().rstrip("=")
    if not os.getenv("API_KEY"):
        os.environ["API_KEY"] = base64.urlsafe_b64encode(_derive(master, "api-key")).decode().rstrip("=")
        logger.warning("API_KEY was not set; derived from APP_SECRET/JWT_SECRET_KEY")
    if not os.getenv("CONNECTOR_ENCRYPTION_KEY"):
        os.environ["CONNECTOR_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(_derive(master, "connector-encryption")).decode()
        logger.warning("CONNECTOR_ENCRYPTION_KEY was not set; derived from APP_SECRET/JWT_SECRET_KEY")


class Settings:
    """
    Reads env vars in __init__, not as class-body defaults. Class-body
    defaults (`X: str = os.getenv(...)`) are evaluated ONCE at module
    import time — if anything sets env vars after that first import
    (tests, a reload, a different entrypoint order), those values never
    take effect. __init__ re-reads on every instantiation instead.
    """
    def __init__(self):
        self.APP_NAME: str = "Business Reality Engine"
        self.DATABASE_URL: str = normalize_database_url(os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/business_reality"
        ))
        self.ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.ENV: str = os.getenv("ENV", "development")

        # Section 28-30 reasoning layer — provider-agnostic (app/reasoning/llm_providers.py).
        # LLM_PROVIDER: "anthropic" | "openai" | "openai_compatible"
        self.LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
        self.LLM_MODEL: str = os.getenv("LLM_MODEL", "")            # blank = use provider's sensible default
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
        self.LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")         # for openai_compatible (Groq/Together/local/etc)
        self.LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")       # required for openai_compatible

        # Section 17-18: background detection cycle interval.
        self.DETECTION_INTERVAL_MINUTES: int = int(os.getenv("DETECTION_INTERVAL_MINUTES", "15"))
        self.SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"

        # Section 32-35 frontend. Comma-separated list of allowed origins —
        # never "*" with allow_credentials=True (browsers reject that
        # combination anyway, and it would defeat the point).
        self.CORS_ORIGINS: list[str] = [
            o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        ]

        # Section 48/51: separate admin credential for cross-tenant ops
        # endpoints (/api/system/*). Deliberately fails CLOSED (503) if
        # unset, unlike API_KEY's dev-mode fallback — a new sensitive
        # endpoint category should default to unavailable, not open.
        self.ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

        # Section 47/51 performance: DB connection pool, tunable per
        # deployment. Defaults are conservative-but-real, not SQLAlchemy's
        # bare defaults (5+10) — see app/core/db.py for why this matters
        # under concurrent load.
        self.DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "20"))
        self.DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))

        # Section 48: auth. API_KEY is a stopgap service-level key for internal/dev use
        # (e.g. connector agents pushing data). Real per-tenant auth (JWT/OAuth2 + RBAC)
        # is a hard requirement before any production/customer traffic — see SECURITY.md.
        self.API_KEY: str = os.getenv("API_KEY", "")

        # Hosting: serve the built web app from this same service (one URL, no CORS, one thing to keep running).
        self.SERVE_FRONTEND: bool = os.getenv("SERVE_FRONTEND", "true").lower() == "true"
        self.FRONTEND_DIST: str = os.getenv("FRONTEND_DIST", "")
        # Free hosts put idle services to sleep. This pings our own public URL so it stays awake (RENDER_EXTERNAL_URL
        # is set by Render automatically; KEEPALIVE_URL overrides). 0 disables.
        self.KEEPALIVE_MINUTES: int = int(os.getenv("KEEPALIVE_MINUTES", "10"))
        self.KEEPALIVE_URL: str = os.getenv("KEEPALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL", "")


@lru_cache
def get_settings() -> Settings:
    derive_missing_secrets()
    if not os.getenv("APP_BASE_URL") and os.getenv("RENDER_EXTERNAL_URL"):
        os.environ["APP_BASE_URL"] = os.environ["RENDER_EXTERNAL_URL"]   # links in emails/invitations point at this service
    settings = Settings()
    if settings.ENV == "production":
        if not settings.API_KEY:
            raise RuntimeError("API_KEY must be set in production — refusing to boot unauthenticated.")
        jwt_secret = os.getenv("JWT_SECRET_KEY", "")
        if len(jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET_KEY must be set (>= 32 chars) in production.")
        if not os.getenv("CONNECTOR_ENCRYPTION_KEY"):
            raise RuntimeError("CONNECTOR_ENCRYPTION_KEY must be set in production.")
    return settings
