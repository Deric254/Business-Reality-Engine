"""Forecasts follow the business's own weekly rhythm: a Saturday is not a Tuesday, a closed Sunday stays closed."""
import datetime as dt

import pytest

from app.core.db import SessionLocal
from app.core.forecasting import forecast_days
from tests.test_block_c import make_owner, mk

CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total"}
PATTERN = {0: 100, 1: 100, 2: 100, 3: 100, 4: 150, 5: 300, 6: 0}                 # Monday..Sunday; closed on Sundays
TODAY = dt.datetime.utcnow().date()


@pytest.fixture()
def db():
    s = SessionLocal(); yield s; s.close()


def _history(client, weeks, growth=0.0, name="s"):
    t, h = make_owner(client, name)
    cid = mk(client, t, h, name="s", connector_type="file", config=CFG).json()["id"]
    rows = []
    for back in range(weeks * 7, 0, -1):
        day = TODAY - dt.timedelta(days=back)
        amount = PATTERN[day.weekday()] * (1 + growth * (weeks * 7 - back) / 7)
        if amount:
            rows.append(f"{day:%Y-%m-%d} 12:00,{amount:.2f}")
    csv = "Date,Total\n" + "\n".join(rows) + "\n"
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", csv.encode(), "text/csv")}, headers=h).status_code == 200
    return t, h


def test_the_forecast_follows_the_weekly_pattern_day_by_day(client, db):
    t, _ = _history(client, 8, name="sf1")
    f = forecast_days(db, t, "sale", 7)
    by_day = {d.weekday: d.expected for d in f.days}
    assert [d.day for d in f.days] == [TODAY + dt.timedelta(days=i) for i in range(1, 8)]                 # from tomorrow
    assert by_day["Saturday"] == pytest.approx(300, rel=0.01) and by_day["Sunday"] == 0 and by_day["Tuesday"] == pytest.approx(100, rel=0.01)
    assert f.total == pytest.approx(850, rel=0.01) and f.low <= f.total <= f.high
    assert (f.busiest, f.quietest, f.weeks_of_history) == ("Saturday", "Sunday", 8) and abs(f.trend_pct) < 1


def test_a_growing_business_is_forecast_higher_and_the_trend_is_reported(client, db):
    t, _ = _history(client, 8, growth=0.02, name="sf2")
    f = forecast_days(db, t, "sale", 7)
    assert f.trend_pct > 5 and f.total > 850 * 1.1


def test_the_range_widens_when_days_are_unpredictable(client, db):
    t, h = make_owner(client, "sf3")
    cid = mk(client, t, h, name="s", connector_type="file", config=CFG).json()["id"]
    rows = [f"{TODAY - dt.timedelta(days=b):%Y-%m-%d} 12:00,{100 + (60 if b % 3 == 0 else -40)}" for b in range(56, 0, -1)]
    client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", ("Date,Total\n" + "\n".join(rows) + "\n").encode(), "text/csv")}, headers=h)
    f = forecast_days(db, t, "sale", 7)
    assert f.high - f.low > 100                                                                       # honest about the noise


def test_less_than_four_weeks_gives_no_forecast_rather_than_an_invented_one(client, db):
    t, _ = _history(client, 3, name="sf4")
    assert forecast_days(db, t, "sale", 7) is None
    t2, _ = make_owner(client, "sf5")
    assert forecast_days(db, t2, "sale", 7) is None


def test_one_record_a_week_is_not_a_daily_rhythm(client, db):
    t, h = make_owner(client, "sf8")
    cid = mk(client, t, h, name="s", connector_type="file", config=CFG).json()["id"]
    rows = [f"{TODAY - dt.timedelta(days=3 + 7 * i):%Y-%m-%d} 12:00,500" for i in range(8)]
    client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", ("Date,Total\n" + "\n".join(rows) + "\n").encode(), "text/csv")}, headers=h)
    assert forecast_days(db, t, "sale", 7) is None


def test_asking_for_next_week_uses_the_pattern_and_says_what_it_does_not_know(client):
    t, h = _history(client, 8, name="sf6")
    out = client.post("/api/questions/ask", json={"tenant_id": t, "question": "What will sales be next week?"}, headers=h).json()
    assert out["intent"] == "forecast" and "weekly pattern" in out["answer"] and "Strongest day: Saturday; quietest: Sunday" in out["answer"]
    assert "Holidays and paydays are not modelled" in out["answer"] and len(out["evidence"]["days"]) == 7
    young, hy = _history(client, 3, name="sf7")                                                       # too little history: the old, plainer answer
    old = client.post("/api/questions/ask", json={"tenant_id": young, "question": "What will sales be next week?"}, headers=hy).json()
    assert "weekly pattern" not in old["answer"]
