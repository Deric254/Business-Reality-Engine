"""
Section 53: business model versioning and change management.

The shape of a business changes (a branch opens, a product line ends, the POS is replaced). Each change is
recorded with a per-tenant version number and the date it took effect:

  * alerts and cases are stamped with the version in force when they were raised, so old evidence stays
    interpretable after the business restructures;
  * changes that are declared as `rebaseline` (e.g. a replaced source system) make the period-over-period
    detectors wait until enough history exists AFTER the change instead of comparing across it;
  * changes spotted automatically from the data (new/discontinued location or product, new source) are recorded
    and shown on alerts as context only; they never silence monitoring on their own.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alerts import Alert, Case
from app.models.config import ModelChange
from app.models.core import BusinessEvent, Connector, ResolvedEntity
from app.core.clock import utcnow

logger = logging.getLogger("business_reality_engine")

KINDS = {"location_added", "location_discontinued", "product_added", "product_discontinued",
         "source_added", "source_replaced", "restructure"}
MANUAL_KINDS = {"source_replaced", "restructure", "location_added", "location_discontinued",
                "product_added", "product_discontinued", "source_added"}
YOUNG_TENANT_DAYS = 120
NEW_WITHIN_DAYS = 30
GONE_AFTER_DAYS = 60


def current_version(db: Session, tenant_id: str) -> int:
    return int(db.query(func.coalesce(func.max(ModelChange.version), 1)).filter(ModelChange.tenant_id == tenant_id).scalar())


def record_change(db: Session, tenant_id: str, kind: str, subject: str, effective_at: datetime, *, description: Optional[str] = None,
                  rebaseline: bool = False, source: str = "auto", details: Optional[dict] = None) -> Optional[ModelChange]:
    """Idempotent per (tenant, kind, subject). Returns None when it was already recorded."""
    if kind not in KINDS:
        raise ValueError(f"unknown change kind '{kind}'")
    for _ in range(2):
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"model_change:{tenant_id}"})
            if db.query(ModelChange.id).filter(ModelChange.tenant_id == tenant_id, ModelChange.kind == kind, ModelChange.subject == subject).first():
                db.rollback()
                return None
            row = ModelChange(tenant_id=tenant_id, version=current_version(db, tenant_id) + 1, kind=kind, subject=subject[:255],
                              description=description, effective_at=effective_at, rebaseline=rebaseline, source=source, details=details)
            db.add(row); db.commit(); db.refresh(row)
            return row
        except IntegrityError:
            db.rollback()
    return None


def active_rebaseline(db: Session, tenant_id: str, now: datetime, window_days: int) -> Optional[ModelChange]:
    """A declared re-baseline whose post-change history is still too short for the detector's baseline window."""
    return (db.query(ModelChange)
            .filter(ModelChange.tenant_id == tenant_id, ModelChange.rebaseline == True,  # noqa: E712
                    ModelChange.effective_at <= now, ModelChange.effective_at > now - timedelta(days=window_days))
            .order_by(ModelChange.effective_at.desc()).first())


def detect_model_changes(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[ModelChange]:
    now = now or utcnow()
    first, last = db.query(func.min(BusinessEvent.timestamp), func.max(BusinessEvent.timestamp)).filter(
        BusinessEvent.tenant_id == tenant_id, BusinessEvent.validation_status == "validated").one()
    if not first or (now - first).days < YOUNG_TENANT_DAYS:
        return []                                  # too little history to call anything "new" or "gone"
    live = last >= now - timedelta(days=7)
    out: list[ModelChange] = []

    def scan(col, added_kind, gone_kind, label):
        rows = (db.query(col, func.min(BusinessEvent.timestamp), func.max(BusinessEvent.timestamp), func.count(BusinessEvent.id))
                .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.validation_status == "validated", col.isnot(None))
                .group_by(col).limit(2000).all())
        names = {}
        if label == "product":
            ids = [r[0] for r in rows]
            names = {e.id: e.canonical_name for e in db.query(ResolvedEntity).filter(ResolvedEntity.tenant_id == tenant_id, ResolvedEntity.id.in_(ids))} if ids else {}
        for key, f, l, n in rows:
            nm = names.get(key, key)
            if f >= now - timedelta(days=NEW_WITHIN_DAYS) and f >= first + timedelta(days=90) and n >= 3:
                c = record_change(db, tenant_id, added_kind, str(key), f, description=f"New {label} appeared: {nm}", details={"name": nm, "events": int(n)})
                if c: out.append(c)
            elif live and n >= 20 and l <= now - timedelta(days=GONE_AFTER_DAYS):
                c = record_change(db, tenant_id, gone_kind, str(key), l, description=f"{label.capitalize()} stopped appearing: {nm}", details={"name": nm, "events": int(n)})
                if c: out.append(c)

    scan(BusinessEvent.location, "location_added", "location_discontinued", "location")
    scan(BusinessEvent.resolved_product_id, "product_added", "product_discontinued", "product")
    for cid, name, f in (db.query(Connector.id, Connector.name, func.min(BusinessEvent.timestamp)).join(BusinessEvent, BusinessEvent.connector_id == Connector.id)
                         .filter(Connector.tenant_id == tenant_id, BusinessEvent.validation_status == "validated").group_by(Connector.id, Connector.name).all()):
        if f >= now - timedelta(days=NEW_WITHIN_DAYS) and f >= first + timedelta(days=90):
            c = record_change(db, tenant_id, "source_added", cid, f, description=f"New data source began sending data: {name}", details={"name": name})
            if c: out.append(c)
    return out


def _versions(db: Session, tenant_id: str) -> list[tuple[datetime, int]]:
    return [(r.effective_at, r.version) for r in db.query(ModelChange).filter(ModelChange.tenant_id == tenant_id).order_by(ModelChange.effective_at, ModelChange.version)]


def version_at(versions: list[tuple[datetime, int]], when: Optional[datetime]) -> int:
    """Highest version whose change took effect on/before `when` (1 = the original model)."""
    if when is None:
        return max([v for _, v in versions], default=1)
    best = 1
    for eff, v in versions:
        if eff <= when:
            best = max(best, v)
    return best


def stamp_versions(db: Session, tenant_id: str) -> int:
    versions = _versions(db, tenant_id)
    n = 0
    for model in (Alert, Case):
        for row in db.query(model).filter(model.tenant_id == tenant_id, model.model_version.is_(None)).limit(1000):
            row.model_version = version_at(versions, getattr(row, "created_at", None) or getattr(row, "detected_at", None))
            n += 1
    db.commit()
    return n


def changes_near(db: Session, tenant_id: str, start: Optional[datetime], end: Optional[datetime], lookback_days: int = 90) -> list[dict]:
    if not end:
        return []
    rows = (db.query(ModelChange).filter(ModelChange.tenant_id == tenant_id, ModelChange.effective_at <= end,
                                         ModelChange.effective_at >= (start or end) - timedelta(days=lookback_days))
            .order_by(ModelChange.effective_at.desc()).limit(5).all())
    return [{"version": r.version, "kind": r.kind, "description": r.description or r.subject,
             "effective_at": r.effective_at.isoformat(), "rebaseline": r.rebaseline} for r in rows]
