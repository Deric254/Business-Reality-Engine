"""Alerts about where and why: a cost category, a branch, a margin, a customer, a payment. Each one must fire on a real
problem, name the thing behind it, and stay quiet on ordinary variation."""
import datetime as dt

import pytest

from app.core import detectors_dimension as DD
from app.core.db import SessionLocal
from app.models.alerts import Alert, AlertState
from app.models.core import BusinessEvent, Connector
from tests.conftest import _make_tenant

NOW = dt.datetime(2026, 9, 30, 21, 0)          # 1 Oct, 00:00 in Nairobi: whole days up to 30 Sep


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def tid(db):
    t = _make_tenant("dimdet")
    c = Connector(tenant_id=t, name="Books", connector_type="file", expected_frequency="periodic", is_active=True)
    db.add(c); db.commit()
    return t


def cid(db, t):
    return db.query(Connector.id).filter(Connector.tenant_id == t).scalar()


def put(db, t, entity, window, amount, *, place=None, cat=None, cust=None, day_in_window=10):
    """One record `window` windows (30 days each) back, 0 = the last 30 days."""
    ts = NOW - dt.timedelta(days=30 * window + day_in_window)
    db.add(BusinessEvent(tenant_id=t, connector_id=cid(db, t), source="t", entity=entity, event_type=f"{entity}.x", timestamp=ts,
                         amount=amount, location=place, product_id=cat, customer_id=cust, validation_status="validated"))


def alerts(db, t, kind=None):
    db.expire_all()
    q = db.query(Alert).filter(Alert.tenant_id == t)
    return [a for a in q.all() if kind is None or a.kind == kind]


# ---------- spending category ----------
def test_category_spike_names_the_category_and_the_branch(db, tid):
    for w in (1, 2, 3):
        put(db, tid, "expense", w, 10_000, cat="Transport", place="Meru"); put(db, tid, "expense", w, 40_000, cat="Rent", place="Meru")
        put(db, tid, "sale", w, 300_000, place="Meru")
    for i in range(6):
        put(db, tid, "expense", 0, 15_000, cat="Transport", place="Meru", day_in_window=3 + i)   # 90k against a usual 10k, spread out
    put(db, tid, "expense", 0, 40_000, cat="Rent", place="Meru"); put(db, tid, "sale", 0, 300_000, place="Meru")
    db.commit()
    got = DD.detect_category_spikes(db, tid, NOW)
    assert [a.title for a in got] == ["Transport spending is 9 times your usual 30 days"]          # 90k against a usual 10k
    assert got[0].kind == "category" and got[0].evidence["category"] == "Transport" and got[0].evidence["top_location"] == "Meru"
    assert "Rent" not in got[0].title                                   # a steady cost is not blamed


def test_category_with_no_usual_or_a_small_rise_stays_quiet(db, tid):
    put(db, tid, "expense", 1, 10_000, cat="Licences"); put(db, tid, "expense", 0, 90_000, cat="Licences")      # one earlier window only
    for w in (1, 2, 3):
        put(db, tid, "expense", w, 10_000, cat="Fuel")
    put(db, tid, "expense", 0, 13_000, cat="Fuel")                                                              # +30%
    db.commit()
    assert DD.detect_category_spikes(db, tid, NOW) == []


def test_one_large_payment_gets_one_alert_not_two(db, tid):
    for w in (1, 2, 3):
        for i in range(8):
            put(db, tid, "expense", w, 5_000, cat="Transport", place="Meru", day_in_window=2 + i * 3)
        put(db, tid, "sale", w, 400_000, place="Meru")
    for i in range(8):
        put(db, tid, "expense", 0, 5_000, cat="Transport", place="Meru", day_in_window=12 + i * 2)
    put(db, tid, "expense", 0, 300_000, cat="Transport", place="Meru", day_in_window=3); put(db, tid, "sale", 0, 400_000, place="Meru")
    db.commit()
    found, errors = DD.run_dimension_detectors(db, tid, NOW)
    kinds = sorted(a.kind for a in found)
    assert errors == 0 and "payment" in kinds and "category" not in kinds
    pay = alerts(db, tid, "payment")[0]
    assert pay.evidence["amount"] == 300_000 and pay.evidence["category"] == "Transport" and "times your usual payment" in pay.title


# ---------- branch ----------
def test_branch_decline_is_found_even_when_the_total_hides_it(db, tid):
    for w in (1, 2, 3):
        put(db, tid, "sale", w, 100_000, place="Maua"); put(db, tid, "sale", w, 100_000, place="Meru")
    put(db, tid, "sale", 0, 50_000, place="Maua"); put(db, tid, "sale", 0, 160_000, place="Meru")        # total 210k vs 200k: up
    db.commit()
    got = DD.detect_branch_declines(db, tid, NOW)
    assert [a.evidence["branch"] for a in got] == ["Maua"] and got[0].evidence["change_pct"] == -50.0
    assert "hides this in your total" in got[0].what_happened


def test_one_branch_or_a_steady_branch_stays_quiet(db, tid):
    for w in (1, 2, 3):
        put(db, tid, "sale", w, 100_000, place="Only")
    put(db, tid, "sale", 0, 10_000, place="Only")
    db.commit()
    assert DD.detect_branch_declines(db, tid, NOW) == []            # one place: the overall comparison says it


# ---------- margin ----------
def test_branch_losing_money_and_margin_erosion(db, tid):
    for w in (1, 2, 3):
        put(db, tid, "sale", w, 100_000, place="Nkubu"); put(db, tid, "expense", w, 50_000, place="Nkubu", cat="Stock")
        put(db, tid, "sale", w, 100_000, place="Maua"); put(db, tid, "expense", w, 50_000, place="Maua", cat="Stock")
    put(db, tid, "sale", 0, 100_000, place="Nkubu"); put(db, tid, "expense", 0, 130_000, place="Nkubu", cat="Stock")      # loses money
    put(db, tid, "sale", 0, 100_000, place="Maua"); put(db, tid, "expense", 0, 60_000, place="Maua", cat="Stock")        # 10 points less: not enough
    db.commit()
    got = {a.evidence["branch"] or "all": a for a in DD.detect_margin_compression(db, tid, NOW)}
    assert "Nkubu" in got and got["Nkubu"].evidence["loss"] and "spent more than it sold" in got["Nkubu"].title
    assert "Maua" not in got
    assert got["Nkubu"].evidence["driver"] == "Stock"


def test_margin_alert_closes_itself_when_the_margin_recovers(db, tid):
    for w in (1, 2, 3):
        put(db, tid, "sale", w, 100_000, place="Nkubu"); put(db, tid, "expense", w, 50_000, place="Nkubu", cat="Stock")
    put(db, tid, "sale", 0, 100_000, place="Nkubu"); put(db, tid, "expense", 0, 130_000, place="Nkubu", cat="Stock"); db.commit()
    assert DD.detect_margin_compression(db, tid, NOW)
    later = NOW + dt.timedelta(days=30)                                  # the next window is back to normal
    put(db, tid, "sale", -1, 100_000, place="Nkubu"); put(db, tid, "expense", -1, 50_000, place="Nkubu", cat="Stock"); db.commit()
    DD.detect_margin_compression(db, tid, later)
    assert all(a.state == AlertState.resolved for a in alerts(db, tid, "margin") if a.evidence.get("branch") == "Nkubu")


# ---------- customer ----------
def test_customer_dependence_ignores_walk_in_buyers(db, tid):
    for i in range(5):
        put(db, tid, "sale", 0, 20_000, cust="Mama Njeri Wholesale", day_in_window=2 + i * 5)
    for i in range(6):
        put(db, tid, "sale", 0, 10_000, cust="Ann", day_in_window=3 + i * 4)
    for i in range(10):
        put(db, tid, "sale", 0, 5_000, cust="Walk-in Customer", day_in_window=1 + i * 2)
    db.commit()
    a = DD.detect_customer_concentration(db, tid, NOW)
    assert a and a.evidence["customer"] == "Mama Njeri Wholesale" and a.evidence["orders"] == 5 and 0.0 < a.evidence["share_pct"] <= 100


def test_a_business_of_walk_ins_has_no_dependence_alert(db, tid):
    for i in range(12):
        put(db, tid, "sale", 0, 10_000, cust="Walk-in Customer", day_in_window=1 + i)
    db.commit()
    assert DD.detect_customer_concentration(db, tid, NOW) is None


# ---------- the cycle ----------
def test_the_detection_cycle_runs_them_and_counts_no_errors(db, tid):
    from app.core.detectors import run_tenant_cycle
    for w in (1, 2, 3):
        put(db, tid, "sale", w, 100_000, place="Maua"); put(db, tid, "sale", w, 100_000, place="Meru")
    put(db, tid, "sale", 0, 40_000, place="Maua"); put(db, tid, "sale", 0, 160_000, place="Meru"); db.commit()
    res = run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount", "direction": "drop"}], now=NOW, replay=True)
    assert res["errors"] == 0 and any(a.kind == "branch" for a in res["alerts"])
