"""Section 35-40: historical experience in alert detail, similar-case matching, automatic outcome measurement."""
import datetime as dt

from app.core.business_memory import create_case_from_alert, find_similar_cases, measure_due_cases
from app.core.db import SessionLocal
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent, Connector
from tests.conftest import _make_tenant, bearer, register

KEY = "historical_deviation:sale:amount:7d"


def mk_alert(db, t, key=KEY, created=None, title="Sale deterioration detected", state=AlertState.detected):
    a = Alert(tenant_id=t, severity=AlertSeverity.high, state=state, title=title, what_happened="down",
              created_at=created or dt.datetime.utcnow(),
              evidence={"metric": "sale.amount", "period_days": 7, **({"dedup_key": key} if key else {})})
    db.add(a); db.commit(); db.refresh(a)
    return a


def measured_case(db, t, alert, outcome="Sale amount improved by 20%"):
    c = create_case_from_alert(db, t, alert.id, "doing_this", intervention="Ran a promo")
    c.actual_outcome = outcome; c.attribution_confidence = "medium"; c.outcome_measured_at = dt.datetime.utcnow()
    db.commit()
    return c


def test_similar_matches_same_key_only_and_excludes_self_and_other_tenants():
    db = SessionLocal()
    try:
        t, other = _make_tenant("mem"), _make_tenant("mem2")
        old = mk_alert(db, t, created=dt.datetime.utcnow() - dt.timedelta(days=30))
        measured_case(db, t, old)
        diff = mk_alert(db, t, key="historical_deviation:expense:amount:7d", created=dt.datetime.utcnow() - dt.timedelta(days=20))
        measured_case(db, t, diff)
        foreign = mk_alert(db, other, created=dt.datetime.utcnow() - dt.timedelta(days=10))
        measured_case(db, other, foreign)
        cur = mk_alert(db, t)
        measured_case(db, t, cur, outcome="its own")
        got = find_similar_cases(db, t, cur)
        assert [c.alert_id for c in got] == [old.id]
    finally:
        db.close()


def test_title_fallback_when_no_dedup_key():
    db = SessionLocal()
    try:
        t = _make_tenant("memt")
        old = mk_alert(db, t, key=None, title="Legacy alert"); measured_case(db, t, old)
        cur = mk_alert(db, t, key=None, title="Legacy alert")
        other = mk_alert(db, t, key=None, title="Different"); measured_case(db, t, other)
        assert [c.alert_id for c in find_similar_cases(db, t, cur)] == [old.id]
    finally:
        db.close()


def test_unmeasured_cases_are_not_experience():
    db = SessionLocal()
    try:
        t = _make_tenant("memu")
        old = mk_alert(db, t)
        create_case_from_alert(db, t, old.id, "doing_this")
        assert find_similar_cases(db, t, mk_alert(db, t)) == []
    finally:
        db.close()


def test_alert_detail_returns_historical_experience(client, tenant_a, tenant_b):
    _, _, tokens = register(client, tenant_a); h = bearer(tokens)
    db = SessionLocal()
    try:
        old = mk_alert(db, tenant_a, created=dt.datetime.utcnow() - dt.timedelta(days=30)); measured_case(db, tenant_a, old)
        cur = mk_alert(db, tenant_a); cid = cur.id
    finally:
        db.close()
    d = client.get(f"/api/alerts/{cid}/detail", params={"tenant_id": tenant_a}, headers=h).json()
    assert len(d["historical_experience"]) == 1
    e = d["historical_experience"][0]
    assert e["decision"] == "doing_this" and e["intervention"] == "Ran a promo" and "improved" in e["outcome"]
    assert e["attribution_confidence"] == "medium"
    # the legacy endpoint agrees
    s = client.get(f"/api/cases/similar/{cid}", params={"tenant_id": tenant_a}, headers=h).json()
    assert len(s) == 1
    # another tenant's token cannot read it
    _, _, t2 = register(client, tenant_b)
    assert client.get(f"/api/alerts/{cid}/detail", params={"tenant_id": tenant_a}, headers=bearer(t2)).status_code == 403


def test_measure_due_cases_only_after_window_and_only_once():
    db = SessionLocal()
    try:
        t = _make_tenant("memm")
        conn = Connector(tenant_id=t, name="POS", connector_type="webhook", is_active=True); db.add(conn); db.commit()
        t0 = dt.datetime(2026, 3, 1, 12, 0)
        for day, amt in ((-3, 100), (3, 150)):
            db.add(BusinessEvent(tenant_id=t, connector_id=conn.id, source="t", entity="sale", event_type="sale.x",
                                 timestamp=t0 + dt.timedelta(days=day), amount=amt, validation_status="validated"))
        a = mk_alert(db, t, created=t0)
        case = create_case_from_alert(db, t, a.id, "doing_this")
        db.commit()
        assert measure_due_cases(db, t, t0 + dt.timedelta(days=3)) == 0          # window not elapsed
        assert measure_due_cases(db, t, t0 + dt.timedelta(days=8)) == 1
        db.refresh(case)
        assert "improved by 50.0%" in case.actual_outcome and case.attribution_confidence == "medium"
        assert "does not confirm" in case.actual_outcome                          # never claims causation
        assert measure_due_cases(db, t, t0 + dt.timedelta(days=9)) == 0          # not re-measured
    finally:
        db.close()


def test_case_without_metric_is_skipped_not_fatal():
    db = SessionLocal()
    try:
        t = _make_tenant("memn")
        a = Alert(tenant_id=t, severity=AlertSeverity.high, state=AlertState.detected, title="x", what_happened="y",
                  created_at=dt.datetime(2026, 3, 1), evidence={})
        db.add(a); db.commit()
        create_case_from_alert(db, t, a.id, "doing_this")
        assert measure_due_cases(db, t, dt.datetime(2026, 4, 1)) == 0
    finally:
        db.close()
