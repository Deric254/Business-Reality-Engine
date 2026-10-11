from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import get_settings

settings = get_settings()

# Explicit pool sizing — SQLAlchemy's defaults (pool_size=5, max_overflow=10,
# 15 connections total per process) are fine for local dev but a real
# constraint under concurrent load: a handful of slow requests exhausts the
# pool and every other request queues behind them. Tunable via env vars so
# this can be raised per deployment without a code change; pool_recycle
# prevents using a connection Postgres (or a proxy in front of it) has
# silently dropped after sitting idle.
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session per request, always rolled back on error."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
