"""No complete-day or minimum-history restriction: every record counts the moment it arrives, today's included, and
thin or part-recorded history is used (and said so), never refused. Windows are equal in length, so a part-day cannot
make a steady business look like it is falling."""
import datetime as dt

import pytest

from app.core import detectors as D
from app.core import goals, periods
from app.core.detection import detect_historical_deviation
from tests.conftest import _make_tenant
from tests.test_detectors import NOW, connector, db, put, tid  # noqa: F401  (fixtures)


def _daily(db, tid, cid, entity, days, amount, hour=9, now=NOW):
    for k in range(days):                                  # k=0 is today
        d = (now - dt.timedelta(days=k)).replace(hour=hour, minute=0)
        put(db, tid, cid, entity, d, amount)
    db.commit()


def test_analysis_end_is_now_once_today_has_a_record_and_the_start_of_today_before(db, tid):
    c = connector(db, tid)
    _daily(db, tid, c.id, "sale", 5, 100, now=NOW - dt.timedelta(days=1))          # records up to yesterday only (a nightly export)
    assert periods.analysis_end(db, tid, NOW) == dt.datetime(2026, 3, 18, 0, 0)
    put(db, tid, c.id, "sale", NOW - dt.timedelta(hours=3), 50); db.commit()       # the first record of today arrives
    assert periods.analysis_end(db, tid, NOW) == NOW


def test_a_steady_business_is_not_read_as_falling_because_today_is_part_way_through(db, tid):
    c = connector(db, tid)
    _daily(db, tid, c.id, "sale", 40, 1000)                                        # 1000 a day, today's sale already in at 09:00
    assert detect_historical_deviation(db, tid, "sale", "amount", now=NOW) is None
    assert D.detect_opportunity(db, tid, "sale", "amount", "drop", now=NOW) is None


def test_live_cycle_counts_todays_records_and_says_so(db, tid, monkeypatch):
    c = connector(db, tid)
    monkeypatch.setattr(D, "utcnow", lambda: NOW)
    for k in range(1, 36):                                                         # 1000 a day for 5 weeks, then the last 3 days collapse
        put(db, tid, c.id, "sale", (NOW - dt.timedelta(days=k)).replace(hour=9), 1000 if k > 3 else 100)
    put(db, tid, c.id, "sale", NOW.replace(hour=9), 50)                            # today so far: almost nothing
    db.commit()
    out = D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount"}])
    titles = [a.title for a in out["alerts"]]
    dev = next(a for a in out["alerts"] if a.evidence.get("dedup_key", "").startswith("historical_deviation"))
    assert "Sales are down" in dev.title, titles
    assert "Today so far is included" in dev.what_happened and dev.evidence["includes_today"] is True
    assert "complete days" not in dev.what_happened


def test_a_nightly_export_is_judged_on_the_days_it_has_not_on_a_quiet_today(db, tid, monkeypatch):
    c = connector(db, tid)
    monkeypatch.setattr(D, "utcnow", lambda: NOW)
    for k in range(1, 40):                                                         # steady up to yesterday; nothing of today has arrived
        put(db, tid, c.id, "sale", (NOW - dt.timedelta(days=k)).replace(hour=9), 1000)
    db.commit()
    out = D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount"}])
    assert not [a for a in out["alerts"] if a.evidence.get("dedup_key", "").startswith("historical_deviation")]


def test_one_earlier_period_is_enough_and_the_alert_says_how_early_it_is(db, tid):
    c = connector(db, tid)
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=10), 1000)                  # the only earlier period
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=2), 100)
    db.commit()
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    assert a and "early signal" in a.what_happened and float(a.confidence) <= 0.4


def test_records_that_begin_part_way_through_a_window_are_not_read_as_a_rise(db, tid):
    c = connector(db, tid)
    for k in range(0, 10):                                                         # the books began 10 days ago: 100 a day since
        put(db, tid, c.id, "expense", (NOW - dt.timedelta(days=k)).replace(hour=9), 100)
    db.commit()
    assert detect_historical_deviation(db, tid, "expense", "amount", now=NOW, direction="rise") is None   # not "up 133%" against a part-recorded week


def test_goal_pace_counts_todays_records_and_waits_only_for_the_period_to_be_a_day_old():
    assert goals.judge("sales", 500, 1000, 15.5, 30, True)[0] == "at_risk"          # 15.5 days gone, 500 so far: heading for ~968
    assert goals.judge("sales", 500, 1000, 0.5, 30, True) == ("not_started", None)   # half a day old: a pace over a few hours is noise
    status, projected = goals.judge("spending", 300, 1000, 10.25, 30, True)
    assert status == "on_track" and projected == pytest.approx(300 / 10.25 * 30)


def test_today_is_a_real_period_everywhere_and_is_compared_like_for_like(db, tid):
    from app.api.business import build_trend
    from app.core import data_check
    from app.core.analytics import build_analytics
    from app.core.business_questions import compute_profit_breakdown
    c = connector(db, tid)
    for k in range(1, 9):                                                           # 1000 at 09:00 and 500 at 15:00 every earlier day
        put(db, tid, c.id, "sale", (NOW - dt.timedelta(days=k)).replace(hour=9), 1000)
        put(db, tid, c.id, "sale", (NOW - dt.timedelta(days=k)).replace(hour=15), 500)
    put(db, tid, c.id, "sale", NOW.replace(hour=9), 1000)                           # today, 12:00 now: the 09:00 sale is in, the 15:00 one has not happened
    db.commit()
    k = compute_profit_breakdown(db, tid, 1, now=NOW)
    assert k["revenue"]["current"] == 1000 and k["revenue"]["previous"] == 1000 and k["compared_with"] == "the same time yesterday"   # not 1500: yesterday's afternoon is not due yet
    assert k["profit"]["change_pct"] == 0
    t2 = _make_tenant("today-quiet"); c2 = connector(db, t2)                        # a nightly export: nothing of today has arrived
    put(db, t2, c2.id, "sale", (NOW - dt.timedelta(days=1)).replace(hour=9), 1000); db.commit()
    q = compute_profit_breakdown(db, t2, 1, now=NOW)
    assert q["revenue"]["current"] == 0 and q["revenue"]["previous"] == 0 and q["compared_with"] is None     # no false "down 100%"
    assert build_trend(db, tid, 1)["period"]["days"] == 1
    assert build_analytics(db, tid, 1)["insights"] == []
    assert data_check.run(db, tid, 1) is not None
