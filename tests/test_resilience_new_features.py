"""Robustness of the money-at-stake and regular-customers features, and of the alert machinery they sit on:
checks that overlap, checks that fail, bad data, and the numbers themselves against an independent calculation."""
import datetime as dt
import random
import threading

import pytest

from app.core import detectors as D, detectors_dimension as DD, value_at_stake
from app.core.db import SessionLocal
from app.core.detection import data_start
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent
from tests.conftest import _make_tenant
from tests.test_block_c import make_owner
from tests.test_detectors import NOW, connector, db, tid, weekly  # noqa: F401  (fixtures)
from tests.test_lapsed_customers import ACTIVE, buy, shop


def _open(db, tid_, **flt):
    db.expire_all()
    return db.query(Alert).filter(Alert.tenant_id == tid_, Alert.state.in_(ACTIVE)).filter_by(**flt).all()


def _race(fn, n=6):
    """Run fn(session) in n threads released at the same instant; return any exceptions."""
    gate, errs = threading.Barrier(n), []

    def work():
        s = SessionLocal()
        try:
            gate.wait()
            fn(s)
        except Exception as e:                                                                       # noqa: BLE001
            errs.append(e)
        finally:
            s.close()
    ts = [threading.Thread(target=work) for _ in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return errs


# ---------- checks that overlap (a scheduled run and "Check now") must never double an alert ----------
def test_overlapping_runs_create_one_gone_quiet_alert_not_several(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, regulars=14, quiet={f"Customer {i}" for i in range(5)})
    assert _race(lambda s: DD.detect_lapsed_customers(s, tid, NOW)) == []
    assert len(_open(db, tid, kind="customers")) == 1


def test_overlapping_runs_never_double_any_existing_alert_either(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    weekly(db, tid, c.id, "expense", [3000, 1000, 1000, 1000, 1000])
    run = lambda s: D.run_metric_detectors(s, tid, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW)       # noqa: E731
    assert _race(run, 8) == []
    keys = [a.evidence["dedup_key"] for a in _open(db, tid)]
    assert keys and len(keys) == len(set(keys)), keys                                                # every alert exactly once; includes the fixed-limit and the deviation paths
    assert any(k.startswith("threshold:") for k in keys) and any(k.startswith("historical_deviation") for k in keys)


def test_a_second_run_after_the_first_updates_rather_than_adds(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, regulars=14, quiet={f"Customer {i}" for i in range(5)})
    first = DD.detect_lapsed_customers(db, tid, NOW)
    again = DD.detect_lapsed_customers(db, tid, NOW + dt.timedelta(hours=2))
    assert again.id == first.id and len(_open(db, tid, kind="customers")) == 1


# ---------- a check that fails must never stop the rest ----------
def test_a_failing_new_check_costs_one_error_and_nothing_else(db, tid, monkeypatch):
    c = connector(db, tid)
    shop(db, tid, c.id, regulars=14, quiet={f"Customer {i}" for i in range(5)})
    monkeypatch.setattr(DD, "detect_lapsed_customers", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated failure")))
    # the registry holds the function object, so patch the tuple's callable via the module-level name the runner reads
    alerts, errors = DD.run_dimension_detectors(db, tid, NOW)
    assert errors == 1                                                                                # counted, not hidden
    db.add(Alert(tenant_id=tid, severity=AlertSeverity.medium, state=AlertState.detected, title="session still usable", what_happened="x", evidence={}))
    db.commit()                                                                                       # and the database session is still healthy afterwards


def test_the_whole_cycle_still_finishes_and_still_raises_other_alerts_when_the_new_check_fails(db, tid, monkeypatch):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    monkeypatch.setattr(DD, "detect_lapsed_customers", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated failure")))
    out = D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount", "direction": "drop"}], now=NOW)
    assert out["errors"] >= 1 and out["alerts"], out                                                  # other alerts still came out
    assert _open(db, tid)


def test_the_alert_list_cannot_be_broken_by_the_money_field(client, monkeypatch):
    t, h = make_owner(client, "resil")
    s = SessionLocal()
    s.add(Alert(tenant_id=t, severity=AlertSeverity.medium, state=AlertState.detected, title="a", what_happened="b", kind="deviation",
                evidence={"metric": "sale.amount", "current": 1, "baseline": 100, "direction": "drop", "dedup_key": "x"}))
    s.commit()
    monkeypatch.setattr(value_at_stake, "at_stake", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get(f"/api/alerts/?tenant_id={t}", headers=h)
    assert r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["at_stake"] is None             # the alert still shows; only the extra figure is missing


def test_awkward_data_never_breaks_the_new_check(db, tid):
    c = connector(db, tid)
    shop(db, tid, c.id, regulars=12)
    for cust in ("", " ", None, "客户", "O'Brien \"Big\" <b>&</b>", "x" * 400):                        # odd names, empty, huge
        for d in (100, 90, 80, 70, 60):
            buy(db, tid, c.id, cust, NOW - dt.timedelta(days=d), amount=0.0 if cust == "" else 10.0)
    buy(db, tid, c.id, "Negative", NOW - dt.timedelta(days=100), amount=-50.0)
    db.add(BusinessEvent(tenant_id=tid, connector_id=c.id, source="t", entity="sale", event_type="sale.x", timestamp=NOW - dt.timedelta(days=30), amount=None, customer_id="NoAmount", validation_status="validated"))
    db.commit()
    DD.detect_lapsed_customers(db, tid, NOW)                                                            # must simply return (an alert or nothing), never raise


# ---------- the numbers: the check against an independent calculation ----------
def _reference(events, now, first):
    """Plain-Python version of the definition: who is a regular, who has gone quiet, and what they are worth a month."""
    win_start = now - dt.timedelta(days=DD.LAPSED_LOOKBACK_DAYS)
    by = {}
    for cust, ts, amount in events:
        if cust is not None and win_start <= ts < now:
            by.setdefault(cust, []).append((ts, amount))
    on_record = max(1.0, min(float(DD.LAPSED_LOOKBACK_DAYS), (now - first).total_seconds() / 86400))
    monthly_sales = sum(a for rows in by.values() for _, a in rows) / on_record * 30      # (all named and unnamed sales are in `events` only if named; see the caller)
    regulars, lapsed, value = 0, [], 0.0
    for cust, rows in by.items():
        days = {ts.date() for ts, _ in rows}
        if len(days) < DD.LAPSED_MIN_VISITS or DD.is_generic_buyer(cust):
            continue
        lo, hi = min(ts for ts, _ in rows), max(ts for ts, _ in rows)
        active = (hi - lo).total_seconds() / 86400
        gap = active / (len(days) - 1)
        quiet = (now - hi).total_seconds() / 86400
        if gap <= 0 or quiet > DD.LAPSED_MAX_QUIET_DAYS:
            continue
        regulars += 1
        if quiet > max(DD.LAPSED_GAP_MULTIPLE * gap, DD.LAPSED_MIN_QUIET_DAYS):
            lapsed.append(cust)
            value += sum(a for _, a in rows) / max(active + gap, 30.0) * 30
    return regulars, set(lapsed), value, monthly_sales


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_check_agrees_with_an_independent_calculation_on_random_businesses(db, seed):
    rnd = random.Random(seed)
    t = _make_tenant(f"acc{seed}"); other = _make_tenant(f"accother{seed}")
    c, c2 = connector(db, t), connector(db, other)
    events = []                                                                                       # (customer, time, amount) of every VALIDATED NAMED SALE of this business
    for i in range(60):
        name = f"Cust {i}"
        every, stop = rnd.choice([2, 3, 5, 7, 10, 14, 21, 30]), rnd.choice([0, 0, 0, 5, 15, 30, 50, 90, 140])
        amount = rnd.choice([100.0, 250.0, 800.0])
        d = rnd.randrange(150, 178)
        while d > stop:
            ts = NOW - dt.timedelta(days=d, hours=rnd.randrange(0, 20))
            buy(db, t, c.id, name, ts, amount); events.append((name, ts, amount)); d -= every + rnd.randrange(0, 2)
        if not stop:
            ts = NOW - dt.timedelta(hours=rnd.randrange(2, 30)); buy(db, t, c.id, name, ts, amount); events.append((name, ts, amount))
    for i in range(12):                                                                               # things that must NOT count: rejected sales, expenses, generic buyers, another business
        for d in (120, 100, 80, 60, 40):
            ts = NOW - dt.timedelta(days=d)
            buy(db, t, c.id, f"Rej {i}", ts, 500.0, status="rejected")
            db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity="expense", event_type="expense.x", timestamp=ts, amount=500.0, customer_id=f"Exp {i}", validation_status="validated"))
            buy(db, other, c2.id, f"Other {i}", ts, 500.0)
    for d in range(10, 170, 4):
        ts = NOW - dt.timedelta(days=d); buy(db, t, c.id, "Walk-in customer", ts, 300.0); events.append(("Walk-in customer", ts, 300.0))
    db.commit()
    regs, lapsed, value, monthly = _reference(events, NOW, data_start(db, t))
    share = value / monthly if monthly else 0
    raised = len(lapsed) >= DD.LAPSED_NEW[0] and share >= DD.LAPSED_NEW[1]
    a = DD.detect_lapsed_customers(db, t, NOW)
    assert (a is not None) == raised, (seed, len(lapsed), share)
    if raised:
        assert a.evidence["lapsed_count"] == len(lapsed) and a.evidence["regulars"] == regs
        assert a.evidence["monthly_value"] == pytest.approx(value, abs=0.05 * max(1, len(lapsed)))   # evidence rounds each shown figure, so compare the total to within a few cents
        assert {x["customer"] for x in a.evidence["top"]} <= lapsed


# ---------- consistency between the alert, the amount, and the summary ----------
def test_the_summary_total_is_exactly_the_sum_of_the_amounts_shown_on_the_alerts(client):
    t, h = make_owner(client, "cons")
    s = SessionLocal()
    shop(s, t, connector(s, t).id, regulars=14, quiet={f"Customer {i}" for i in range(5)}, now=dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0))
    assert client.post("/api/detection/run/all", json={"tenant_id": t}, headers=h).json()["errors"] == 0
    alerts = client.get(f"/api/alerts/?tenant_id={t}&state=open", headers=h).json()
    shown = round(sum(a["at_stake"] for a in alerts if a["at_stake"] is not None), 2)
    v = client.get(f"/api/alerts/value?tenant_id={t}", headers=h).json()
    assert shown > 0 and v["open_at_stake"] == shown and v["open_count"] == len(alerts)
    assert v["open_measured"] == sum(1 for a in alerts if a["at_stake"] is not None)
    mine = next(a for a in alerts if a["kind"] == "customers")
    assert f"{round(mine['at_stake']):,}" in mine["what_happened"].replace(" ", "")                      # the number on the card and the number in its sentence are the same figure
