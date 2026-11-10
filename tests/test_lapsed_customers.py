"""Regular customers who have gone quiet: each judged against their own rhythm, never fooled by a stopped data feed,
never duplicated, and closed the moment they come back."""
import datetime as dt

from app.core import detectors_dimension as DD
from app.core.value_at_stake import at_stake
from app.models.alerts import Alert, AlertState
from app.models.core import BusinessEvent
from tests.test_detectors import NOW, connector, db, tid  # noqa: F401  (fixtures)

ACTIVE = (AlertState.detected, AlertState.investigating, AlertState.acknowledged, AlertState.action_planned, AlertState.action_taken, AlertState.monitoring)


def buy(db, tid, cid, customer, when, amount=500.0, status="validated"):
    db.add(BusinessEvent(tenant_id=tid, connector_id=cid, source="t", entity="sale", event_type="sale.x", timestamp=when, amount=amount,
                         customer_id=customer, validation_status=status))


def shop(db, tid, cid, *, regulars=12, quiet=(), history=120, every=5, quiet_days=40, now=NOW):
    """`regulars` customers buying every `every` days for `history` days; those named in `quiet` stop `quiet_days` ago."""
    for i in range(regulars):
        name = f"Customer {i}"
        stop = quiet_days if name in quiet else 0
        d = history
        while d > stop:
            buy(db, tid, cid, name, now - dt.timedelta(days=d, hours=i % 5))
            d -= every
        if not stop:
            buy(db, tid, cid, name, now - dt.timedelta(days=1))                                 # still buying yesterday: the feed is live
    db.commit()


def open_alert(db, tid):
    db.expire_all()
    return db.query(Alert).filter(Alert.tenant_id == tid, Alert.kind == "customers", Alert.state.in_(ACTIVE)).all()


def test_raises_one_alert_naming_the_customers_who_went_quiet_with_their_monthly_value(db, tid):
    c = connector(db, tid)
    gone = {f"Customer {i}" for i in range(4)}
    shop(db, tid, c.id, quiet=gone)
    a = DD.detect_lapsed_customers(db, tid, NOW)
    assert a and a.kind == "customers" and a.state == AlertState.detected
    assert a.evidence["lapsed_count"] == 4 and a.evidence["regulars"] == 12
    assert {x["customer"] for x in a.evidence["top"]} == gone
    assert a.title == "4 regular customers have gone quiet"
    assert "4 of your 12 regular customers" in a.what_happened and "Customer 0" in a.recommended_action
    assert "quiet for 45 days." in a.what_happened and "45 to 45" not in a.what_happened
    assert a.evidence["monthly_value"] > 0 and at_stake("customers", a.evidence) == a.evidence["monthly_value"]
    assert a.evidence["confirmed_at"] == NOW.isoformat()
    DD.detect_lapsed_customers(db, tid, NOW + dt.timedelta(hours=1))                        # a second check refreshes, never duplicates
    assert len(open_alert(db, tid)) == 1


def test_a_healthy_business_gets_nothing(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id)
    assert DD.detect_lapsed_customers(db, tid, NOW) is None and not open_alert(db, tid)


def test_each_customer_is_judged_against_their_own_rhythm(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, regulars=10, quiet={"Customer 0", "Customer 1", "Customer 2"})
    for i in range(10, 14):                                                                  # four who buy every 45 days, last bought 40 days ago: regulars, but not quiet
        for d in (175, 130, 85, 40):
            buy(db, tid, c.id, f"Slow {i}", NOW - dt.timedelta(days=d))
    db.commit()
    a = DD.detect_lapsed_customers(db, tid, NOW)
    assert a.evidence["lapsed_count"] == 3                                                   # only the three who broke a 5-day rhythm
    assert a.evidence["regulars"] == 14                                                      # the slow four are counted as regulars ...
    assert not any(x["customer"].startswith("Slow") for x in a.evidence["top"])              # ... and never called quiet


def test_too_few_quiet_customers_or_too_little_money_is_not_an_alert(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, quiet={"Customer 0", "Customer 1"})                                  # 2 is below the 3 needed to raise
    assert DD.detect_lapsed_customers(db, tid, NOW) is None


def test_walk_in_buyers_rejected_records_and_other_entities_never_count(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, quiet={f"Customer {i}" for i in range(4)})
    for i in range(6):                                                                       # a till that records "walk-in" is not a person who can go quiet
        d = 120
        while d > 40:
            buy(db, tid, c.id, "Walk-in customer", NOW - dt.timedelta(days=d)); d -= 5
    for i in range(30, 36):                                                                  # rejected sales of a quiet-looking customer must not make them a regular
        for d in (100, 90, 80, 70):
            buy(db, tid, c.id, f"Ghost {i}", NOW - dt.timedelta(days=d), status="rejected")
    db.commit()
    a = DD.detect_lapsed_customers(db, tid, NOW)
    assert a.evidence["lapsed_count"] == 4 and a.evidence["regulars"] == 12
    assert not any("Walk" in x["customer"] or "Ghost" in x["customer"] for x in a.evidence["top"])


def test_too_little_history_is_not_judged(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, history=40, every=3, quiet={f"Customer {i}" for i in range(5)}, quiet_days=22)
    assert DD.detect_lapsed_customers(db, tid, NOW) is None


def test_a_stopped_data_feed_is_not_mistaken_for_lost_customers(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, history=140, quiet={f"Customer {i}" for i in range(12)}, quiet_days=30)         # everyone stopped 30 days ago: the feed died
    assert DD.detect_lapsed_customers(db, tid, NOW) is None


def test_the_alert_closes_when_the_customers_come_back_and_survives_a_small_wobble(db, tid):
    c = connector(db, tid)
    gone = {f"Customer {i}" for i in range(4)}
    shop(db, tid, c.id, quiet=gone)
    a = DD.detect_lapsed_customers(db, tid, NOW)
    assert a
    for i in range(3):                                                                       # 3 of 4 return: one is below the keep line, so it closes
        buy(db, tid, c.id, f"Customer {i}", NOW - dt.timedelta(days=1))
    db.commit()
    assert DD.detect_lapsed_customers(db, tid, NOW) is None
    db.refresh(a)
    assert a.state == AlertState.resolved and a.evidence["recovered_at"]


def test_an_open_alert_is_kept_and_refreshed_at_the_looser_line(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, quiet={f"Customer {i}" for i in range(4)})
    a = DD.detect_lapsed_customers(db, tid, NOW)
    for i in range(2):                                                                       # 2 of 4 return: 2 are still quiet, which keeps an alert already open but would not raise a new one
        buy(db, tid, c.id, f"Customer {i}", NOW - dt.timedelta(days=1))
    db.commit()
    b = DD.detect_lapsed_customers(db, tid, NOW + dt.timedelta(hours=1))
    assert b and b.id == a.id and b.evidence["lapsed_count"] == 2 and "2 of your" in b.what_happened


def test_runs_with_the_other_dimension_detectors_without_errors_and_stays_in_one_business(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, quiet={f"Customer {i}" for i in range(4)})
    from tests.conftest import _make_tenant
    other = _make_tenant("det2")
    c2 = connector(db, other)
    shop(db, other, c2.id)
    alerts, errors = DD.run_dimension_detectors(db, tid, NOW)
    assert errors == 0 and any(a.kind == "customers" for a in alerts)
    assert not open_alert(db, other)                                                         # the other business has its own regulars and is untouched
    DD.run_dimension_detectors(db, other, NOW)
    assert not open_alert(db, other) and len(open_alert(db, tid)) == 1
