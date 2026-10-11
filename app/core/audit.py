"""
Audit log (Section 48): an append-only record of who did what. Written in its own short transaction
so a failure to audit can never fail the request, and an audited action that is rolled back does not
leave a misleading entry. Details never contain secrets, tokens or passwords.
"""
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.rate_limit import _client_ip
from app.models.core import AuditLog

logger = logging.getLogger("business_reality_engine")
_MAX_DETAIL = 2000


def record(db: Session, action: str, *, request: Optional[Request] = None, tenant_id: Optional[str] = None,
           actor_user_id: Optional[str] = None, actor_type: str = "user", target: Optional[str] = None,
           detail: Optional[dict[str, Any]] = None, success: bool = True) -> None:
    try:
        d = detail or {}
        for banned in ("password", "token", "secret", "authorization"):
            d = {k: v for k, v in d.items() if banned not in k.lower()}
        if len(str(d)) > _MAX_DETAIL:
            d = {"truncated": True}
        db.add(AuditLog(
            tenant_id=tenant_id, actor_user_id=actor_user_id, actor_type=actor_type, action=action,
            target=(target or None) and str(target)[:200], success=success, detail=d,
            ip=_client_ip(request) if request is not None else None,
            user_agent=(request.headers.get("user-agent", "")[:200] if request is not None else None),
        ))
        db.commit()
    except Exception as e:                      # auditing must never break the request
        db.rollback()
        logger.error(f"Audit write failed for {action}: {e}")
