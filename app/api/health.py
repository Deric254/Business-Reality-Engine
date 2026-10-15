from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.db import get_db

router = APIRouter()


@router.get("/")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/setup")
def setup_status(db: Session = Depends(get_db)):
    """Public. Lets the sign-in page tell a brand-new install (no business yet: show 'Create your business') from a
    normal one, and whether the operator key needed to create the first business is configured."""
    import os
    from app.models.core import Tenant
    return {"has_business": db.query(Tenant.id).first() is not None, "setup_key_configured": bool(os.getenv("ADMIN_API_KEY"))}
