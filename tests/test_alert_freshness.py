"""No stale alerts: every kind of alert closes itself when its condition is gone, however the condition went away
(recovered, limit changed or removed, source switched off, detector no longer looks at it), and nothing current is closed."""
import datetime as dt

from app.core import alert_engine, detectors as D, scheduler
from app.core.clock import utcnow
from app.core.detection import detect_threshold_breach
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import Connector
from tests.test_detectors import NOW, connector, db, put, tid, weekly  # noqa: F401  (fixtures)

ACTIVE = (AlertState.detected, AlertState.investigating, AlertState.acknowledged, AlertState.action_planned, AlertState.action_taken, AlertState.monitoring)


def _open_keys(db, tid):
    db.expire_all()
    return {a.evidence.get("dedup_key") for a in db.query(Alert).filter(Alert.tenant_id == tid, Alert.state.in_(ACTIVE)).all()}


def _alert(db, tid, key, *, kind=None, state=AlertState.detected, confirmed_days_ago=None, **extra):
    ev = {"dedup_key": key, **extra}
    if confirmed_days_ago is not None:
        ev["confirmed_at"] = (utcnow() - dt.timedelta(days=confirmed_days_ago)).isoformat()
    a = Alert(tenant_id=tid, severity=AlertSeverity.medium, state=state, title=key, what_happened="x", kind=kind, evidence=ev)
    db.add(a); db.commit()
    return a


# ---------- a fixed limit ----------
def test_a_limit_alert_closes_when_the_figure_is_back_above_the_limit(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [100, 1000, 1000, 1000, 1000])
    a = detect_threshold_breach(db, tid, "sale", "amount", 500.0, "below", period_days=7, now=NOW)
    assert a and a.state == AlertState.detected
    put(db, tid, c.id, "sale", NOW - dt.timedelta(days=1), 2000); db.commit()          # sales recover well past the limit
    assert detect_threshold_breach(db, tid, "sale", "amount", 500.0, "below", period_days=7, now=NOW) is None
    db.refresh(a)
    assert a.state == AlertState.resolved and a.evidence["recovered_at"]


def test_a_limit_alert_closes_when_the_limit_is_changed_or_removed(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [100, 1000, 1000, 1000, 1000])
    kw = dict(entity="sale", field="amount", direction="drop", now=NOW)
    D.run_metric_detectors(db, tid, absolute_threshold=500, **kw)
    assert "threshold:sale:amount:below:500.0" in _open_keys(db, tid)
    D.run_metric_detectors(db, tid, absolute_threshold=50, **kw)                         # the owner lowered the line: the old alert is not true any more
    assert "threshold:sale:amount:below:500.0" not in _open_keys(db, tid)
    D.run_metric_detectors(db, tid, absolute_threshold=900, **kw)
    assert "threshold:sale:amount:below:900.0" in _open_keys(db, tid)
    D.run_metric_detectors(db, tid, absolute_threshold=None, **kw)                       # the owner removed the line
    assert not {k for k in _open_keys(db, tid) if k.startswith("threshold:")}


def test_a_limit_alert_a_person_is_working_on_also_closes_when_the_limit_is_gone(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [100, 1000, 1000, 1000, 1000])
    D.run_metric_detectors(db, tid, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW)
    a = db.query(Alert).filter(Alert.tenant_id == tid).all()
    thr = next(x for x in a if x.evidence["dedup_key"].startswith("threshold:"))
    thr.state = AlertState.investigating; db.commit()
    D.run_metric_detectors(db, tid, entity="sale", field="amount", direction="drop", absolute_threshold=None, now=NOW)
    db.refresh(thr)
    assert thr.state == AlertState.resolved


# ---------- a source that is switched off or removed ----------
def test_a_silence_alert_closes_when_the_source_is_switched_off(db, tid):
    c = connector(db, tid, freq="realtime")
    put(db, tid, c.id, "sale", NOW - dt.timedelta(hours=30), 10); db.commit()
    assert D.detect_data_silence(db, tid, NOW)
    assert f"data_silence:{c.id}" in _open_keys(db, tid)
    c.is_active = False; db.commit()
    D.detect_data_silence(db, tid, NOW + dt.timedelta(hours=1))
    assert f"data_silence:{c.id}" not in _open_keys(db, tid)


def test_a_link_down_alert_closes_when_the_system_is_switched_off(db, tid):
    c = Connector(tenant_id=tid, name="Till", connector_type="system", is_active=True)
    db.add(c); db.commit()
    _alert(db, tid, f"system_link:{c.id}")
    from app.core.systems import detect_system_links
    c.is_active = False; db.commit()
    detect_system_links(db, tid, NOW)
    assert f"system_link:{c.id}" not in _open_keys(db, tid)


def test_a_business_with_every_source_switched_off_is_still_checked_so_its_alerts_can_close(db, tid):
    c = connector(db, tid)
    _alert(db, tid, "category_spike:expense:fuel:7d")
    assert tid in scheduler._active_tenant_ids(db)
    c.is_active = False; db.commit()
    assert tid in scheduler._active_tenant_ids(db)                                       # it has an open alert, so it is still looked at


# ---------- the safety net: nothing a detector no longer confirms stays on screen ----------
def test_an_alert_no_detector_has_confirmed_for_days_is_closed_and_a_fresh_one_stays(db, tid):
    stale = _alert(db, tid, "category_spike:expense:ghost:7d", confirmed_days_ago=4)
    fresh = _alert(db, tid, "category_spike:expense:fuel:7d", confirmed_days_ago=1)
    worked = _alert(db, tid, "branch_decline:sale:meru:7d", state=AlertState.investigating, confirmed_days_ago=5)
    payment = _alert(db, tid, "large_payment:abc", kind="payment", state=AlertState.investigating, confirmed_days_ago=30)
    by_hand = Alert(tenant_id=tid, severity=AlertSeverity.medium, state=AlertState.detected, title="typed in", what_happened="x", evidence={})
    db.add(by_hand); db.commit()
    assert alert_engine.close_unconfirmed(db, tid, utcnow()) == 2
    for a in (stale, fresh, worked, payment, by_hand):
        db.refresh(a)
    assert stale.state == AlertState.resolved and stale.evidence["recovered_at"] and stale.evidence["closed_because"]
    assert worked.state == AlertState.resolved                                            # a person working on something that is no longer true: closed, record kept
    assert fresh.state == AlertState.detected                                             # a recent confirmation stays
    assert payment.state == AlertState.investigating                                      # a payment is an event, not a condition: it stays while someone works it
    assert by_hand.state == AlertState.detected                                           # nothing that did not come from a detector is ever touched


def test_an_old_alert_with_no_confirmation_mark_gets_a_grace_period_instead_of_being_closed(db, tid):
    legacy = _alert(db, tid, "category_spike:expense:old:7d")
    assert alert_engine.close_unconfirmed(db, tid, utcnow()) == 0
    db.refresh(legacy)
    assert legacy.state == AlertState.detected and legacy.evidence["confirmed_at"]       # marked now, so it is closed in 3 days if nothing confirms it
    assert alert_engine.close_unconfirmed(db, tid, utcnow() + dt.timedelta(days=4)) == 1


def test_every_detector_that_refreshes_an_alert_marks_it_confirmed(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000])
    D.run_metric_detectors(db, tid, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW)
    D.run_metric_detectors(db, tid, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW + dt.timedelta(hours=1))
    alerts = db.query(Alert).filter(Alert.tenant_id == tid, Alert.state.in_(ACTIVE)).all()
    assert {a.evidence["dedup_key"].split(":")[0] for a in alerts} >= {"historical_deviation", "threshold"}
    for a in alerts:
        assert a.evidence["confirmed_at"] == (NOW + dt.timedelta(hours=1)).isoformat(), a.evidence["dedup_key"]


def test_a_full_live_cycle_does_not_close_anything_that_is_still_true(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 1000, 1000, 1000, 1000], now=utcnow())
    D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount"}])
    first = {a.id for a in db.query(Alert).filter(Alert.tenant_id == tid, Alert.state.in_(ACTIVE)).all()}
    D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "amount"}])
    db.expire_all()
    still = {a.id for a in db.query(Alert).filter(Alert.tenant_id == tid, Alert.state.in_(ACTIVE)).all()}
    assert first and first <= still
