import os
from functools import lru_cache


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
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/business_reality"
        )
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


@lru_cache
def get_settings() -> Settings:
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
