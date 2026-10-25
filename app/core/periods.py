"""One definition of "the last N days" for the whole product.

A period is N COMPLETE days on the business's own calendar, ending yesterday (so "last 7 days" is the seven days
before today, each from local midnight to local midnight). Today is left out because it is not over: counting it
against a previous period of full days would make a steady business look like it is falling, most of all in the
morning. The comparison period is the N complete days right before it. This is the same window the alerts use. Every
total, chart and comparison that says "last N days" must take its boundaries from here, so the same words can never
mean two different stretches of time on one screen.

`days == 1` is "Today": the day so far, on its own, and it is never presented as a verdict.
`days == 0` means all time: from the business's first validated sale or expense to the end of today, with nothing
to compare against.

`today` and `today_end` always describe the business's current day, whatever the period, for the views that are not
sliced by the period (the monthly chart, the heat map, the forecast).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.tenant_time import local_midnight_utc, tenant_zone
from app.models.core import BusinessEvent

ALL_TIME = 0
MAX_DAYS = 3650


@dataclass(frozen=True)
class Period:
    days: int                    # whole days covered (for all time: the span from the first record)
    all_time: bool
    start: datetime              # naive UTC, inclusive
    end: datetime                # naive UTC, exclusive (the start of tomorrow on the business's clock)
    prev_start: Optional[datetime]
    prev_end: Optional[datetime]
    first_day: date              # local calendar dates, both inclusive
    last_day: date
    tz: object
    today: date = None           # the business's current local date, whatever the period
    today_end: datetime = None   # naive UTC, exclusive: the start of tomorrow on the business's clock

    def as_dict(self) -> dict:
        return {"days": self.days, "all_time": self.all_time, "first_day": self.first_day.isoformat(),
                "last_day": self.last_day.isoformat(), "timezone": getattr(self.tz, "key", "UTC"),
                "includes_today": self.last_day == self.today}


def local_today(db: Session, tenant_id: str, now: Optional[datetime] = None) -> date:
    now = now or datetime.utcnow()
    return now.replace(tzinfo=timezone.utc).astimezone(tenant_zone(db, tenant_id)).date()


def first_record_day(db: Session, tenant_id: str, tz) -> Optional[date]:
    first = (db.query(func.min(BusinessEvent.timestamp))
             .filter(BusinessEvent.tenant_id == tenant_id, BusinessEvent.entity.in_(("sale", "expense")),
                     BusinessEvent.validation_status == "validated").scalar())
    return first.replace(tzinfo=timezone.utc).astimezone(tz).date() if first else None


def resolve(db: Session, tenant_id: str, days: int, now: Optional[datetime] = None) -> Period:
    if days < 0 or days > MAX_DAYS:
        raise ValueError(f"days must be between 0 and {MAX_DAYS}")
    tz = tenant_zone(db, tenant_id)
    today = local_today(db, tenant_id, now)
    today_end = local_midnight_utc(today + timedelta(days=1), tz)
    if days == ALL_TIME:
        first = first_record_day(db, tenant_id, tz) or today
        return Period((today - first).days + 1, True, local_midnight_utc(first, tz), today_end, None, None, first, today, tz, today, today_end)
    if days == 1:                                   # Today: the day so far
        first, last, end = today, today, today_end
    else:                                           # complete days only: the N days before today
        last = today - timedelta(days=1)
        first = last - timedelta(days=days - 1)
        end = local_midnight_utc(today, tz)
    prev_first = first - timedelta(days=days)
    return Period(days, False, local_midnight_utc(first, tz), end, local_midnight_utc(prev_first, tz),
                  local_midnight_utc(first, tz), first, last, tz, today, today_end)
