"""
Data Coverage Map (Section 15).

The system must know what it doesn't know. Freshness is judged against
each connector's own expected_frequency (Section 17) — a connector
declared "periodic" (daily) going quiet for 20 hours is normal; the same
gap on a "realtime" connector is a real problem.

Coverage feeds directly into alert confidence (Section 21's example:
"Data coverage: 87%"). An alert built on stale or missing source data
should say so, not present the same confidence as one built on live data.
"""
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import BusinessEvent, Connector, DataCoverage, DataCoverageStatus

# How long a connector can go quiet before it's "stale" rather than "recent",
# scaled to what it declared as its own expected frequency.
_STALE_AFTER = {
    "realtime": timedelta(minutes=15),
    "near_realtime": timedelta(hours=1),
    "periodic": timedelta(hours=26),   # daily + buffer
    "slow": timedelta(days=8),          # weekly + buffer
}
_LIVE_WITHIN = {
    "realtime": timedelta(minutes=2),
    "near_realtime": timedelta(minutes=10),
    "periodic": timedelta(hours=2),
    "slow": timedelta(hours=12),
}
_DEFAULT_STALE_AFTER = timedelta(hours=26)  # connectors with no declared frequency default to "periodic"-like

# Used to turn a status into a number an alert's data_coverage_pct can carry.
STATUS_TO_PCT = {
    DataCoverageStatus.live: 100.0,
    DataCoverageStatus.recent: 90.0,
    DataCoverageStatus.stale: 50.0,
    DataCoverageStatus.not_connected: 0.0,
}


def _status_for_connector(connector: Connector, last_data_at: Optional[datetime], now: datetime) -> DataCoverageStatus:
    if last_data_at is None:
        return DataCoverageStatus.not_connected
    age = now - last_data_at
    freq = connector.expected_frequency or "periodic"
    if age <= _LIVE_WITHIN.get(freq, timedelta(hours=2)):
        return DataCoverageStatus.live
    if age <= _STALE_AFTER.get(freq, _DEFAULT_STALE_AFTER):
        return DataCoverageStatus.recent
    return DataCoverageStatus.stale


def compute_and_upsert_coverage(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[DataCoverage]:
    """
    Recomputes coverage for every active connector on this tenant and
    upserts the result. Called on-demand via the API for now — Section 51
    (system self-monitoring) would run this on a schedule instead.
    """
    now = now or datetime.utcnow()
    connectors = db.query(Connector).filter(Connector.tenant_id == tenant_id, Connector.is_active == True).all()

    results = []
    for connector in connectors:
        last_data_at = (
            db.query(func.max(BusinessEvent.timestamp))
            .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.connector_id == connector.id)
            .scalar()
        )
        status = _status_for_connector(connector, last_data_at, now)

        row = (
            db.query(DataCoverage)
            .filter(DataCoverage.tenant_id == tenant_id, DataCoverage.area == connector.name)
            .first()
        )
        if row:
            row.status = status
            row.last_data_at = last_data_at
            row.checked_at = now
        else:
            row = DataCoverage(
                tenant_id=tenant_id, area=connector.name, status=status,
                last_data_at=last_data_at, checked_at=now,
            )
            db.add(row)
        results.append(row)

    db.commit()
    for r in results:
        db.refresh(r)
    return results


def coverage_pct_for_entity(db: Session, tenant_id: str, entity: str, now: Optional[datetime] = None) -> Optional[float]:
    """
    Section 21's 'Data coverage: 87%' on an alert. An entity's data can come
    through multiple connectors — we take the WORST (minimum) status among
    contributing connectors rather than the average or best, so an alert
    never overstates confidence when even one source is stale.
    """
    now = now or datetime.utcnow()
    # Get distinct connector IDs first — cannot .distinct() the full Connector
    # row because Connector.config is a JSON column and Postgres has no
    # equality operator for json, so DISTINCT on the whole row fails.
    connector_ids = (
        db.query(BusinessEvent.connector_id)
        .join(Connector, Connector.id == BusinessEvent.connector_id)
        .filter(Connector.tenant_id == tenant_id, BusinessEvent.entity == entity)
        .distinct()
        .all()
    )
    connector_ids = [row[0] for row in connector_ids]
    if not connector_ids:
        return None  # no connector has ever sent this entity — nothing to score

    connectors = db.query(Connector).filter(Connector.id.in_(connector_ids)).all()

    worst_pct = 100.0
    for connector in connectors:
        last_data_at = (
            db.query(func.max(BusinessEvent.timestamp))
            .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.connector_id == connector.id,
                    BusinessEvent.entity == entity)
            .scalar()
        )
        status = _status_for_connector(connector, last_data_at, now)
        worst_pct = min(worst_pct, STATUS_TO_PCT[status])

    return worst_pct
