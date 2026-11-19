"""First-boot provisioning for hosts where the database gets swapped.

If the database has NO business yet and OWNER_EMAIL + OWNER_PASSWORD are set, create (once):
  - the business (OWNER_BUSINESS_NAME, default "My Business"),
  - its owner login,
  - the master flag on that login,
  - a free, permanent licence for that business (so LICENCE_ENFORCED=true never locks the owner out).

If any business already exists this does nothing, so it is safe to leave on across restarts and deploys.
It never overwrites or resets an existing account. It never raises: a failure is logged and the app still starts.
"""
import logging
import os

from sqlalchemy import text

from app.core.auth_users import hash_password
from app.core.clock import utcnow
from app.core.db import SessionLocal

logger = logging.getLogger("business_reality_engine")


def provision_owner_if_empty() -> bool:
    email = os.getenv("OWNER_EMAIL", "").strip()
    password = os.getenv("OWNER_PASSWORD", "")
    if not email or not password:
        return False
    if len(password) < 8 or len(password.encode()) > 72:
        logger.warning("Auto-provision skipped: OWNER_PASSWORD must be 8 to 72 bytes")
        return False

    db = None
    try:
        db = SessionLocal()
        # Several workers can start at the same instant. This lock (released when the transaction ends) makes the second
        # one wait, then see the business the first one created and stand down, so a business is never created twice.
        db.execute(text("SELECT pg_advisory_xact_lock(727001)"))
        from app.core import licensing
        from app.models.config import MonitoredMetric
        from app.models.core import Tenant, User
        from app.models.licence import Licence

        if db.query(Tenant.id).first() is not None:
            return False          # a business already exists: never touch it
        if db.query(User.id).filter(User.email == email).first() is not None:
            return False

        tenant = Tenant(name=os.getenv("OWNER_BUSINESS_NAME", "My Business").strip() or "My Business",
                        timezone=os.getenv("OWNER_TIMEZONE", "Africa/Nairobi"),
                        base_currency=os.getenv("OWNER_CURRENCY", "KES"))
        db.add(tenant)
        db.flush()
        for entity in ("sale", "expense"):          # same defaults as POST /api/system/tenants
            db.add(MonitoredMetric(tenant_id=tenant.id, entity=entity, field="amount",
                                   direction="rise" if entity == "expense" else "drop"))
        user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(password),
                    role="owner", is_master=True)
        db.add(user)
        db.flush()
        lic = licensing.issue(db, 1, "Platform owner", 0, "USD", user.id)[0]   # issue() commits
        row = db.get(Licence, lic["id"])
        row.status, row.redeemed_at, row.tenant_id = "active", utcnow(), tenant.id
        db.commit()
        logger.info("Auto-provisioned the first business and owner (%s) in an empty database", email)
        return True
    except Exception:
        if db is not None:
            db.rollback()
        logger.exception("Auto-provision failed (the app will still start)")
        return False
    finally:
        if db is not None:
            db.close()
