"""Is the database as new as this code? A deploy that skipped the migration fails in confusing ways; this says so."""
from sqlalchemy import text
from sqlalchemy.orm import Session

EXPECTED_REVISION = "a3c5e7f1b942"          # the newest alembic revision; a test fails if a migration is added without updating it


def status(db: Session) -> dict:
    try:
        found = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        db.rollback()
        found = None
    return {"current": found == EXPECTED_REVISION, "database": found, "expected": EXPECTED_REVISION}
