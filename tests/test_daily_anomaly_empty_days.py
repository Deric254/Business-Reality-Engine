"""A business whose same weekday was mostly empty has no "usual day" to measure against: the daily check must stand down
quietly (and close any alert it had), never divide by zero."""
import datetime as dt

from app.core import detectors as D
from app.models.alerts import Alert, AlertSeverity, AlertState
from tests.test_detectors import NOW, connector, db, put, tid  # noqa: F401  (fixtures)


def _target_day():
    return NOW - dt.timedelta(days=1)


def test_a_weekday_that_was_mostly_empty_is_not_an_error(db, tid):
    c = connector(db, tid)
    put(db, tid, c.id, "sale", _target_day() - dt.timedelta(days=7), 900.0)            # one earlier same weekday had sales, the other seven had none
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=30), 100.0)
    put(db, tid, c.id, "sale", _target_day(), 50.0); db.commit()
    for direction in ("drop", "rise"):
        assert D.detect_daily_anomaly(db, tid, "sale", "amount", direction, now=NOW) is None


def test_an_alert_the_daily_check_had_open_closes_when_there_is_no_usual_day_to_compare_with(db, tid):
    c = connector(db, tid)
    put(db, tid, c.id, "sale", _target_day() - dt.timedelta(days=7), 900.0)
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=30), 100.0); db.commit()
    a = Alert(tenant_id=tid, severity=AlertSeverity.medium, state=AlertState.detected, title="old daily", what_happened="x", kind="daily",
              evidence={"dedup_key": "daily_anomaly:sale:amount"})
    db.add(a); db.commit()
    assert D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW) is None
    db.refresh(a)
    assert a.state == AlertState.resolved


def test_a_real_collapse_on_a_busy_weekday_is_still_caught(db, tid):
    c = connector(db, tid)
    for k in range(1, 7):
        put(db, tid, c.id, "sale", _target_day() - dt.timedelta(days=7 * k), 1000.0)
    put(db, tid, c.id, "sale", _target_day(), 300.0); db.commit()
    assert D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW) is not None
