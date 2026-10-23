"""Is the database as new as this code? A deploy that skipped the migration fails in confusing ways; this says so."""
from sqlalchemy import text
from sqlalchemy.orm import Session

EXPECTED_REVISION = "d1a7f3c2b9e5"          # the newest alembic revision; a test fails if a migration is added without updating it


def status(db: Session) -> dict:
    try:
        found = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        db.rollback()
        found = None
    return {"current": found == EXPECTED_REVISION, "database": found, "expected": EXPECTED_REVISION}
