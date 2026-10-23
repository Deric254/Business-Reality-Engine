"""An owner must be able to read an alert and know what is compared with what, what to do, and whether it is still true.
Also: the always-on checks really run, and a stale alert does not outlive the problem."""
import datetime as dt

import pytest

from app.core import detectors_extra as ex
from app.core import scheduler
from app.core.detection import _headline, compare_headline, _in_cooldown, close_if_cleared, detect_historical_deviation
from app.models.alerts import Alert, AlertSeverity, AlertState
from tests.test_block_c import make_owner
from tests.test_detectors import NOW, connector, db, put, tid, weekly  # noqa: F401  (fixtures)

KEY = "historical_deviation:sale:amount:7d"


def test_scheduled_jobs_are_not_paused():
    """A job added with next_run_time=None is created paused: detection would then only ever run after new data
    arrives, and a source that goes quiet (the case that matters most) would never be noticed."""
    scheduler.start_scheduler(15)
    try:
        for job_id in ("detection_cycle", "notification_dispatch", "connector_sync", "housekeeping"):
            assert scheduler._scheduler.get_job(job_id).next_run_time is not None, job_id
    finally:
        scheduler.stop_scheduler()


@pytest.mark.parametrize("entity,change,days,expected", [
    ("sale", -0.59, 7, "Sales are down 59% from your usual week"),
    ("sale", -0.30, 30, "Sales are down 30% from your usual 30 days"),
    ("expense", 0.50, 7, "Spending is up 50% from your usual week"),
    ("expense", 14.04, 7, "Spending is 15 times your usual week"),     # not "up 1404%"
    ("sale", 1.4, 7, "Sales are 2.4 times your usual week"),
    ("sale", 1.0, 7, "Sales are 2 times your usual week"),
])
def test_headline_names_what_it_is_compared_with(entity, change, days, expected):
    assert _headline(entity, "amount", change, days) == expected


@pytest.mark.parametrize("noun,change,expected", [
    ("Sales", -0.45, "Sales were down 45% from a usual Tuesday"),
    ("Spending", 99.0, "Spending was 100 times a usual Tuesday"),     # not "9900% above"
])
def test_a_single_day_is_compared_with_the_same_weekday_in_plain_words(noun, change, expected):
    assert compare_headline(noun, change, "a usual Tuesday", past=True) == expected


def test_a_loss_already_made_is_stated_as_fact_not_as_a_forecast(db, tid):  # noqa: F811
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [500] * 6)
    weekly(db, tid, c.id, "expense", [900] * 6)
    a = ex.detect_profit_risk(db, tid, now=NOW)
    assert a.title == "Spending exceeds sales"
    assert "you spent KES 400 more than you sold" in a.what_happened and "forecast" not in a.what_happened


def test_alert_gives_the_figures_and_the_gap(db, tid):  # noqa: F811
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    assert a.title == "Sales are down 60% from your usual week"
    for part in ("In the last 7 days sales came to KES 400", "In a usual 7 days it is about KES 1,000", "KES 600 less than usual"):
        assert part in a.what_happened


def test_forecast_alert_stays_quiet_when_the_main_alert_already_says_it(db, tid):  # noqa: F811
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000, 1000, 1000])
    alone = ex.detect_forecast_deviation(db, tid, "sale", "amount", "drop", now=NOW)
    assert alone and "where your recent trend was heading" in alone.title      # worth saying when it is the only signal

    main = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    assert main and main.id != alone.id
    assert ex.detect_forecast_deviation(db, tid, "sale", "amount", "drop", now=NOW) is None
    db.refresh(alone)
    assert alone.state == AlertState.resolved          # one story, one alert: no second, different percentage


def test_untouched_alert_closes_when_the_problem_is_gone_but_one_in_progress_stays(db, tid):  # noqa: F811
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    b = Alert(tenant_id=tid, severity=AlertSeverity.high, state=AlertState.investigating, title="being worked on",
              what_happened="x", evidence={"dedup_key": "historical_deviation:sale:amount:30d"})
    db.add(b); db.commit()
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=1), 700); db.commit()    # sales recover to 1,100

    assert detect_historical_deviation(db, tid, "sale", "amount", now=NOW) is None
    db.refresh(a)
    assert a.state == AlertState.resolved and a.evidence["recovered_at"]
    assert not _in_cooldown(db, tid, KEY, NOW)         # recovering by itself must not silence a real repeat

    # the same rule applied to an alert a person has engaged with leaves it alone
    close_if_cleared(db, tid, "historical_deviation:sale:amount:30d", NOW)
    db.refresh(b)
    assert b.state == AlertState.investigating


def test_a_person_dismissing_an_alert_still_silences_it(db, tid):  # noqa: F811
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    a.state = AlertState.dismissed; db.commit()
    assert _in_cooldown(db, tid, KEY, NOW)
    assert detect_historical_deviation(db, tid, "sale", "amount", now=NOW) is None


def test_alert_list_defaults_can_show_only_open_or_only_closed_and_detail_names_related_alerts(client, db):  # noqa: F811
    t, h = make_owner(client, "clarity")
    ids = {}
    for name, state in (("live", AlertState.detected), ("working", AlertState.investigating),
                        ("done", AlertState.resolved), ("not real", AlertState.false_positive)):
        al = Alert(tenant_id=t, severity=AlertSeverity.medium, state=state, title=name, what_happened="x", evidence={})
        db.add(al); db.commit(); ids[name] = al.id
    ids_by = lambda q: {a["title"] for a in client.get(f"/api/alerts/?tenant_id={t}{q}", headers=h).json()}   # noqa: E731
    assert ids_by("&state=open") == {"live", "working"}
    assert ids_by("&state=closed") == {"done", "not real"}
    assert ids_by("") == {"live", "working", "done", "not real"}
    assert len(client.get(f"/api/alerts/?tenant_id={t}&limit=1", headers=h).json()) == 1
    assert client.get(f"/api/alerts/?tenant_id={t}&limit=0", headers=h).status_code == 422

    live = db.get(Alert, ids["live"]); live.correlated_alert_ids = [ids["working"]]; db.commit()
    detail = client.get(f"/api/alerts/{ids['live']}/detail?tenant_id={t}", headers=h).json()
    assert detail["related"] == [{"id": ids["working"], "title": "working"}]


def test_oversized_upload_is_refused_without_being_read_in_full(client):
    from app.connectors.file_connector import MAX_FILE_SIZE_BYTES
    from tests.test_block_c import MAP, mk
    t, h = make_owner(client, "bigfile")
    cid = mk(client, t, h, connector_type="file", config=MAP).json()["id"]
    big = b"a,b\n" + b"x" * (MAX_FILE_SIZE_BYTES + 10)
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("big.csv", big, "text/csv")}, headers=h)
    assert r.status_code == 413
    r = client.post("/api/connectors/inspect-file", data={"tenant_id": t}, files={"file": ("big.csv", big, "text/csv")}, headers=h)
    assert r.status_code == 413


def _spread(db, tid, cid, entity, per_period, big=None, now=NOW, period_days=7):
    """Several ordinary records per period (per_period[0] = most recent); `big` = (period index, amount) adds one large record."""
    for i, total in enumerate(per_period):
        for k in range(5):
            put(db, tid, cid, entity, now - dt.timedelta(days=period_days * i + 1 + k * 0.5), total / 5)
    if big:
        put(db, tid, cid, entity, now - dt.timedelta(days=period_days * big[0] + 3), big[1])
    db.commit()


def test_one_large_payment_is_named_and_the_next_step_says_what_to_do_if_it_was_planned(db, tid):  # noqa: F811
    c = connector(db, tid)
    _spread(db, tid, c.id, "expense", [1000, 1000, 1000, 1000, 1000], big=(0, 5000))
    a = detect_historical_deviation(db, tid, "expense", "amount", now=NOW, direction="rise")
    first = a.possible_explanations[0]
    assert first["driver"] == "One large payment" and "KES 5,000" in first["evidence"] and "one-off" in first["evidence"]
    assert "large payment of KES 5,000" in a.recommended_action and "'Not relevant'" in a.recommended_action


def test_ordinary_busy_week_is_not_mistaken_for_a_one_off(db, tid):  # noqa: F811
    c = connector(db, tid)
    _spread(db, tid, c.id, "expense", [2000, 1000, 1000, 1000, 1000])          # five similar records each week
    a = detect_historical_deviation(db, tid, "expense", "amount", now=NOW, direction="rise")
    assert a and not any(e.get("one_off") for e in a.possible_explanations)
    assert a.recommended_action.startswith("Check what drove the rise in spending")


def test_a_drop_caused_by_one_big_sale_in_the_earlier_week_says_so(db, tid):  # noqa: F811
    c = connector(db, tid)
    _spread(db, tid, c.id, "sale", [1000, 1000, 1000, 1000, 1000])
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=10), 5000); db.commit()          # one big sale in the week before last week
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW, deterioration_threshold=0.05)
    drivers = [e for e in a.possible_explanations if e.get("one_off")]
    assert drivers and drivers[0]["driver"] == "One large sale" and "KES 5,000" in drivers[0]["evidence"]
    assert "rare for you" in a.recommended_action


def test_counts_such_as_items_sold_carry_no_currency(db, tid):  # noqa: F811
    from app.core.detection import money_formatter
    assert money_formatter(db, tid, "amount")(1234) == "KES 1,234"
    assert money_formatter(db, tid, "quantity")(1234) == "1,234"


def test_a_loss_caused_by_one_payment_says_what_it_would_look_like_without_it(db, tid):  # noqa: F811
    c = connector(db, tid)
    _spread(db, tid, c.id, "sale", [1000, 1000, 1000, 1000, 1000])
    _spread(db, tid, c.id, "expense", [500, 500, 500, 500, 500], big=(0, 4000))
    a = ex.detect_profit_risk(db, tid, now=NOW)
    assert a.title == "Spending exceeds sales"
    assert "One payment of KES 4,000" in a.what_happened and "without it you would have made a profit of" in a.what_happened
    assert a.recommended_action.startswith("Start with the payment of KES 4,000")
