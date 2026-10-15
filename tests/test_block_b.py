"""Block B: model versioning (Section 53) and validation/backtesting (Section 55)."""
import datetime as dt
from datetime import date

import pytest
from sqlalchemy import text

from app.core import accuracy as acc
from app.core.db import SessionLocal, engine
from app.core.detectors import run_metric_detectors
from app.core.model_versions import (active_rebaseline, current_version, detect_model_changes, record_change, stamp_versions,
                                     version_at, _versions)
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.config import GroundTruthEvent, ModelChange
from tests.conftest import _make_tenant, bearer, register
from tests.test_block_a import add, mk_conn

NOW = dt.datetime(2026, 6, 15, 12, 0)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


# ---------------- model versions ----------------

def test_versions_increment_idempotently_and_stamp_retroactively(db):
    t = _make_tenant("mv")
    assert current_version(db, t) == 1
    c1 = record_change(db, t, "restructure", "Merged shops", NOW - dt.timedelta(days=30))
    assert c1.version == 2 and record_change(db, t, "restructure", "Merged shops", NOW) is None     # same subject: no duplicate
    c2 = record_change(db, t, "source_replaced", "New POS", NOW - dt.timedelta(days=5), rebaseline=True, source="manual")
    assert c2.version == 3 and current_version(db, t) == 3
    with pytest.raises(ValueError):
        record_change(db, t, "nonsense", "x", NOW)
    old = Alert(tenant_id=t, severity=AlertSeverity.medium, title="old", what_happened="x", evidence={}, created_at=NOW - dt.timedelta(days=60))
    mid = Alert(tenant_id=t, severity=AlertSeverity.medium, title="mid", what_happened="x", evidence={}, created_at=NOW - dt.timedelta(days=10))
    new = Alert(tenant_id=t, severity=AlertSeverity.medium, title="new", what_happened="x", evidence={}, created_at=NOW)
    db.add_all([old, mid, new]); db.commit()
    assert stamp_versions(db, t) == 3
    assert (old.model_version, mid.model_version, new.model_version) == (1, 2, 3)
    assert stamp_versions(db, t) == 0                                                              # already stamped: untouched
    assert version_at(_versions(db, t), None) == 3


def test_concurrent_style_duplicate_versions_do_not_collide(db):
    t = _make_tenant("mv2")
    for i in range(5):
        assert record_change(db, t, "restructure", f"s{i}", NOW).version == i + 2
    assert sorted(r.version for r in db.query(ModelChange).filter(ModelChange.tenant_id == t)) == [2, 3, 4, 5, 6]


def seed_history(db, t, c, days=200, locations=("Meru",)):
    for i in range(days):
        for loc in locations:
            add(db, t, c, "sale", NOW - dt.timedelta(days=i), 100, location=loc)
    db.commit()


def test_auto_detection_new_and_discontinued_location(db):
    t = _make_tenant("auto"); c = mk_conn(db, t)
    seed_history(db, t, c, 200, ("Meru",))
    for i in range(30):                                            # Old Shop: stopped 100 days ago
        add(db, t, c, "sale", NOW - dt.timedelta(days=100 + i), 50, location="Old Shop")
    for i in range(5):                                             # Nairobi: opened 5 days ago
        add(db, t, c, "sale", NOW - dt.timedelta(days=i), 80, location="Nairobi")
    db.commit()
    got = {(x.kind, x.subject) for x in detect_model_changes(db, t, NOW)}
    assert ("location_added", "Nairobi") in got and ("location_discontinued", "Old Shop") in got
    assert ("location_added", "Meru") not in got and ("location_discontinued", "Meru") not in got
    assert detect_model_changes(db, t, NOW) == []                  # idempotent
    assert all(r.rebaseline is False for r in db.query(ModelChange).filter(ModelChange.tenant_id == t))


def test_young_tenant_and_dead_tenant_produce_no_changes(db):
    t = _make_tenant("young"); c = mk_conn(db, t)
    seed_history(db, t, c, 40, ("A",))
    for i in range(5):
        add(db, t, c, "sale", NOW - dt.timedelta(days=i), 1, location="B")
    db.commit()
    assert detect_model_changes(db, t, NOW) == []                  # <120 days of history: nothing is "new" yet
    t2 = _make_tenant("dead"); c2 = mk_conn(db, t2)
    for i in range(30):
        add(db, t2, c2, "sale", NOW - dt.timedelta(days=200 + i), 1, location="X")
    db.commit()
    assert detect_model_changes(db, t2, NOW) == []                 # whole business silent: that's data silence, not a closed branch


def steady_then_drop(db, t, c, drop_days=8):
    for i in range(90):
        amt = 20 if i < drop_days else 100
        add(db, t, c, "sale", NOW - dt.timedelta(days=i), amt)
    db.commit()


def test_rebaseline_pauses_comparisons_then_resumes(db):
    t = _make_tenant("rb"); c = mk_conn(db, t); steady_then_drop(db, t, c)
    kw = dict(entity="sale", field="amount", period_days=7, baseline_periods=4, now=NOW)
    baseline = run_metric_detectors(db, t, **kw)
    assert any(a.kind != "unknown" for a in baseline)                                              # drop is normally caught
    for a in db.query(Alert).filter(Alert.tenant_id == t): db.delete(a)
    db.commit()
    ch = record_change(db, t, "source_replaced", "New POS", NOW - dt.timedelta(days=6), rebaseline=True, source="manual")
    out = run_metric_detectors(db, t, **kw)
    assert [a.kind for a in out] == ["unknown"] and out[0].evidence["rebaselining"] and out[0].evidence["model_version"] == ch.version
    assert active_rebaseline(db, t, NOW, 35).id == ch.id
    assert active_rebaseline(db, t, NOW + dt.timedelta(days=60), 35) is None                      # window over: monitoring resumes
    assert [a.kind for a in run_metric_detectors(db, t, **{**kw, "now": NOW + dt.timedelta(days=60)}) if a.kind != "unknown"] != None


def test_alert_shows_recent_model_changes_in_evidence(db):
    from app.core.alert_engine import post_process_tenant
    t = _make_tenant("ctx"); c = mk_conn(db, t); steady_then_drop(db, t, c)
    record_change(db, t, "location_added", "Nairobi", NOW - dt.timedelta(days=20), description="New location appeared: Nairobi")
    run_metric_detectors(db, t, entity="sale", field="amount", period_days=7, baseline_periods=4, now=NOW)
    post_process_tenant(db, t, NOW)
    dev = db.query(Alert).filter(Alert.tenant_id == t, Alert.kind == "deviation").one()
    assert dev.evidence["model_changes"][0]["description"].startswith("New location appeared")


def test_model_api_rules(client):
    t = _make_tenant("mapi"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    body = {"kind": "source_replaced", "subject": "New POS", "effective_at": "2026-05-01T00:00:00", "rebaseline": True}
    url = f"/api/accuracy/model/changes?tenant_id={t}"
    assert client.post(url, json=body, headers=bearer(staff)).status_code == 403
    assert client.post(url, json={**body, "kind": "bogus"}, headers=bearer(owner)).status_code == 400
    assert client.post(url, json={**body, "effective_at": "2999-01-01T00:00:00"}, headers=bearer(owner)).status_code == 400
    r = client.post(url, json=body, headers=bearer(owner)); assert r.status_code == 200 and r.json()["version"] == 2
    assert client.post(url, json=body, headers=bearer(owner)).status_code == 409
    ov = client.get(f"/api/accuracy/model?tenant_id={t}", headers=bearer(staff)).json()
    assert ov["version"] == 2 and ov["changes"][0]["rebaseline"] is True
    cid = ov["changes"][0]["id"]
    assert client.post(f"/api/accuracy/model/changes/{cid}/clear-rebaseline?tenant_id={t}", headers=bearer(staff)).status_code == 403
    assert client.post(f"/api/accuracy/model/changes/{cid}/clear-rebaseline?tenant_id={t}", headers=bearer(owner)).status_code == 204
    assert client.get(f"/api/accuracy/model?tenant_id={t}", headers=bearer(owner)).json()["changes"][0]["rebaseline"] is False
    other = _make_tenant("mapi2"); _, _, o2 = register(client, other, "owner")
    assert client.post(f"/api/accuracy/model/changes/{cid}/clear-rebaseline?tenant_id={other}", headers=bearer(o2)).status_code == 404
    assert client.get(f"/api/accuracy/model?tenant_id={t}", headers=bearer(o2)).status_code == 403


# ---------------- backtest ----------------

def drop_tenant(db, name="bt"):
    t = _make_tenant(name); c = mk_conn(db, t)
    for i in range(120):                      # steady 100/day, with a collapse 10..4 days before NOW
        amt = 15 if 4 <= i <= 10 else 100
        add(db, t, c, "sale", NOW - dt.timedelta(days=i), amt)
    db.commit()
    return t


def test_backtest_scores_against_known_events_and_writes_nothing(db):
    t = drop_tenant(db)
    real = Alert(tenant_id=t, severity=AlertSeverity.high, state=AlertState.acknowledged, title="real one", what_happened="x",
                 evidence={"dedup_key": "historical_deviation:sale:amount:7d"})
    db.add(real)
    db.add(GroundTruthEvent(tenant_id=t, label="Shop closed for repairs", metric="sale", starts_on=(NOW - dt.timedelta(days=10)).date(),
                            ends_on=(NOW - dt.timedelta(days=4)).date()))
    db.add(GroundTruthEvent(tenant_id=t, label="Unseen theft", metric="expense", starts_on=(NOW - dt.timedelta(days=25)).date(),
                            ends_on=(NOW - dt.timedelta(days=24)).date()))
    db.commit()
    n_alerts = db.query(Alert).filter(Alert.tenant_id == t).count(); real_id = real.id
    res = acc.run_backtest(t, (NOW - dt.timedelta(days=30)).date(), NOW.date(), tolerance_days=3)
    r = res["results"][0]
    assert r["labelled_events"] == 2 and r["detected_events"] == 1 and r["missed_events"] == ["Unseen theft"]
    assert r["recall"] == 0.5 and r["precision_is_lower_bound"] is True and r["precision"] > 0
    assert r["median_days_to_detect"] is not None and r["by_kind"]
    assert "Data-silence" in res["limitations"][0]
    db.expire_all()                                                                                 # replay touched nothing real
    assert db.query(Alert).filter(Alert.tenant_id == t).count() == n_alerts
    kept = db.get(Alert, real_id)
    assert kept.state == AlertState.acknowledged and kept.title == "real one"
    assert db.query(ModelChange).filter(ModelChange.tenant_id == t).count() == 0


def test_backtest_without_labels_reports_volume_only(db):
    t = drop_tenant(db, "bt2")
    r = acc.run_backtest(t, (NOW - dt.timedelta(days=30)).date(), NOW.date())["results"][0]
    assert r["precision"] is None and r["recall"] is None and "cannot be measured" in r["note"] and r["alerts_per_30_days"] is not None


def test_backtest_threshold_sweep_recommends_best_f1(db):
    t = drop_tenant(db, "bt3")
    db.add(GroundTruthEvent(tenant_id=t, label="Closure", metric="sale", starts_on=(NOW - dt.timedelta(days=10)).date(),
                            ends_on=(NOW - dt.timedelta(days=4)).date())); db.commit()
    res = acc.run_backtest(t, (NOW - dt.timedelta(days=30)).date(), NOW.date(), threshold_multipliers=[0.5, 1.0, 10.0])
    assert [x["threshold_multiplier"] for x in res["results"]] == [0.5, 1.0, 10.0]
    assert res["recommended_threshold_multiplier"] in (0.5, 1.0)           # an absurd threshold misses the closure
    assert res["recommended_threshold_multiplier"] == 1.0 and res["results"][2]["episodes"] <= res["results"][1]["episodes"]


@pytest.mark.parametrize("kw", [
    dict(start=date(2026, 6, 10), end=date(2026, 6, 1)),
    dict(start=date(2026, 1, 1), end=date(2026, 6, 1)),                     # 152 steps x 2 variants > limit
    dict(start=date(2026, 6, 1), end=date(2026, 6, 10), step_days=0),
    dict(start=date(2026, 6, 1), end=date(2026, 6, 10), tolerance_days=99),
    dict(start=date(2026, 6, 1), end=date(2026, 6, 10), threshold_multipliers=[0]),
    dict(start=date(2026, 6, 1), end=date(2026, 6, 10), threshold_multipliers=[1, 2, 3, 4, 5]),
])
def test_backtest_rejects_bad_input(db, kw):
    t = _make_tenant("btbad")
    if kw.get("start") == date(2026, 1, 1):
        kw = {**kw, "threshold_multipliers": [1.0, 2.0]}
    with pytest.raises(ValueError):
        acc.run_backtest(t, **kw)


def test_backtest_api_owner_only_lock_and_history(client, db):
    t = drop_tenant(db, "btapi"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    body = {"start": str((NOW - dt.timedelta(days=12)).date()), "end": str(NOW.date())}
    url = f"/api/accuracy/backtest?tenant_id={t}"
    assert client.post(url, json=body, headers=bearer(staff)).status_code == 403
    assert client.post(url, json={**body, "end": "2000-01-01"}, headers=bearer(owner)).status_code == 400
    assert client.post(url, json={**body, "step_days": 0}, headers=bearer(owner)).status_code == 422
    holder = engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"backtest:{t}"})
    try:
        assert client.post(url, json=body, headers=bearer(owner)).status_code == 409                # one at a time per business
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"backtest:{t}"}); holder.close()
    r = client.post(url, json=body, headers=bearer(owner)); assert r.status_code == 200, r.text
    hist = client.get(url, headers=bearer(staff)).json()
    assert len(hist) == 1 and hist[0]["id"] == r.json()["id"]


def test_ground_truth_api(client):
    t = _make_tenant("gt"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    u = f"/api/accuracy/ground-truth?tenant_id={t}"
    b = {"label": "Power outage", "metric": "sale", "starts_on": "2026-05-01", "ends_on": "2026-05-03"}
    assert client.post(u, json=b, headers=bearer(staff)).status_code == 403
    assert client.post(u, json={**b, "ends_on": "2026-04-01"}, headers=bearer(owner)).status_code == 400
    assert client.post(u, json={**b, "ends_on": "2026-12-31"}, headers=bearer(owner)).status_code == 400
    assert client.post(u, json={**b, "label": ""}, headers=bearer(owner)).status_code == 422
    gid = client.post(u, json=b, headers=bearer(owner)).json()["id"]
    assert len(client.get(u, headers=bearer(staff)).json()) == 1
    assert client.delete(f"/api/accuracy/ground-truth/{gid}?tenant_id={t}", headers=bearer(owner)).status_code == 204
    assert client.delete(f"/api/accuracy/ground-truth/{gid}?tenant_id={t}", headers=bearer(owner)).status_code == 404


# ---------------- live accuracy ----------------

def mk_alert(db, t, kind, state, conf, created=None):
    a = Alert(tenant_id=t, severity=AlertSeverity.medium, state=state, title="a", what_happened="x", kind=kind, confidence=conf,
              evidence={"metric": "sale.amount"}, created_at=created or NOW - dt.timedelta(days=3))
    db.add(a)
    return a


def test_live_accuracy_rates_calibration_and_recall(db):
    t = _make_tenant("live")
    for _ in range(12):
        mk_alert(db, t, "deviation", AlertState.false_positive, 0.9)                     # confident but wrong
    for _ in range(3):
        mk_alert(db, t, "deviation", AlertState.resolved, 0.9)
    for _ in range(4):
        mk_alert(db, t, "trend", AlertState.resolved, 0.5)
    mk_alert(db, t, "trend", AlertState.dismissed, 0.5)
    mk_alert(db, t, "deviation", AlertState.detected, 0.9)                               # open: excluded from rates
    mk_alert(db, t, "unknown", AlertState.dismissed, 1.0)                                # not a claim of trouble: excluded
    db.add(GroundTruthEvent(tenant_id=t, label="Hit", metric="sale", starts_on=(NOW - dt.timedelta(days=4)).date(), ends_on=(NOW - dt.timedelta(days=3)).date()))
    db.add(GroundTruthEvent(tenant_id=t, label="Miss", metric="sale", starts_on=(NOW - dt.timedelta(days=60)).date(), ends_on=(NOW - dt.timedelta(days=60)).date()))
    db.commit()
    rep = acc.live_accuracy(db, t, 180, now=NOW)
    dev = rep["by_kind"]["deviation"]
    assert dev["closed"] == 15 and dev["false_positive_rate"] == 0.8 and dev["alerts"] == 16
    trend = rep["by_kind"]["trend"]
    assert trend["closed"] == 5 and trend["false_positive_rate"] is None and "only 5 closed" in trend["note"]
    assert "unknown" not in rep["by_kind"]
    top = [b for b in rep["calibration"] if b["bucket"] == "0.8-1.0"][0]
    assert top["observed_useful_rate"] == 0.2 and top["verdict"] == "overconfident"
    low = [b for b in rep["calibration"] if b["bucket"] == "0.4-0.6"][0]
    assert low["verdict"] == "not enough closed alerts to judge"
    assert rep["live_recall"]["recall"] == 0.5 and rep["live_recall"]["missed"] == ["Miss"]
    assert sum(m["alerts"] for m in rep["by_month"].values()) == 22 - 1
    assert acc.live_accuracy(db, _make_tenant("live0"), 180)["live_recall"] is None


def test_report_api_auth_and_isolation(client, db):
    t = _make_tenant("rep"); _, _, owner = register(client, t, "owner")
    assert client.get(f"/api/accuracy/report?tenant_id={t}").status_code == 401
    other = _make_tenant("rep2"); _, _, o2 = register(client, other, "owner")
    assert client.get(f"/api/accuracy/report?tenant_id={t}", headers=bearer(o2)).status_code == 403
    assert client.get(f"/api/accuracy/report?tenant_id={t}&days=1", headers=bearer(owner)).status_code == 422
    assert client.get(f"/api/accuracy/report?tenant_id={t}", headers=bearer(owner)).json()["live_recall"] is None
