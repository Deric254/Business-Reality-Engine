"""A business's own clock: its day starts and ends at local midnight, not UTC midnight."""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.core import Tenant


def tenant_zone(db: Session, tenant_id: str) -> ZoneInfo:
    t = db.get(Tenant, tenant_id)
    try:
        return ZoneInfo(t.timezone if t and t.timezone else "UTC")
    except Exception:
        return ZoneInfo("UTC")


def local_midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    """The start of `day` on the business's clock, as the naive UTC time events are stored in."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def start_of_local_day(now: datetime, tz: ZoneInfo) -> datetime:
    """Midnight at the start of the business's current day, as naive UTC. Everything before it is a complete day."""
    return local_midnight_utc(now.replace(tzinfo=timezone.utc).astimezone(tz).date(), tz)
