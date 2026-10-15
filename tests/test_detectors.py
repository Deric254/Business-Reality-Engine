"""Detectors, alert lifecycle (cooldown, correlation, ranking, escalation) and the wired cycle."""
import datetime as dt
import uuid

import pytest

from app.core import detectors as D
from app.core.alert_engine import post_process_tenant, priority_score
from app.core.db import SessionLocal
from app.core.detection import detect_historical_deviation, detect_threshold_breach
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent, Connector, Tenant
from tests.conftest import _make_tenant, bearer, register

NOW = dt.datetime(2026, 3, 18, 12, 0)   # a Wednesday, noon UTC


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def tid():
    return _make_tenant("det")


def connector(db, tid, freq="realtime", name="POS"):
    c = Connector(tenant_id=tid, name=name, connector_type="webhook", expected_frequency=freq, is_active=True)
    db.add(c); db.commit(); db.refresh(c)
    return c


def put(db, tid, cid, entity, ts, amount):
    db.add(BusinessEvent(tenant_id=tid, connector_id=cid, source="t", entity=entity, event_type=f"{entity}.x",
                         timestamp=ts, amount=amount, validation_status="validated"))


def weekly(db, tid, cid, entity, per_period, period_days=7, now=NOW):
    """per_period[0] = most recent period (the 'current' one), then older ones."""
    for i, total in enumerate(per_period):
        mid = now - dt.timedelta(days=period_days * i + period_days / 2)
        put(db, tid, cid, entity, mid, total)
    db.commit()


# ---------- direction semantics ----------
def test_expense_rise_is_a_problem_sale_rise_is_not(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "expense", [1500, 1000, 1000, 1000, 1000])
    weekly(db, tid, c.id, "sale", [1500, 1000, 1000, 1000, 1000])
    exp = detect_historical_deviation(db, tid, "expense", "amount", now=NOW, direction="rise")
    assert exp and "spending is up" in exp.title.lower() and exp.severity in (AlertSeverity.critical, AlertSeverity.high)
    assert detect_historical_deviation(db, tid, "sale", "amount", now=NOW, direction="drop") is None


def test_expense_drop_is_not_a_problem(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "expense", [500, 1000, 1000, 1000, 1000])
    assert detect_historical_deviation(db, tid, "expense", "amount", now=NOW, direction="rise") is None


def test_invalid_direction_rejected(db, tid):
    with pytest.raises(ValueError):
        detect_historical_deviation(db, tid, "sale", "amount", now=NOW, direction="sideways")


def test_default_direction():
    assert D.default_direction("expense") == "rise" and D.default_direction("sale") == "drop"


# ---------- opportunity ----------
def test_opportunity_on_good_move(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [1400, 1000, 1000, 1000, 1000])
    a = D.detect_opportunity(db, tid, "sale", "amount", "drop", now=NOW)
    assert a and a.severity == AlertSeverity.opportunity
    again = D.detect_opportunity(db, tid, "sale", "amount", "drop", now=NOW)
    assert again.id == a.id                      # updated, not duplicated


def test_opportunity_for_cost_falling_and_not_for_small_or_bad_moves(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "expense", [600, 1000, 1000, 1000, 1000])
    assert D.detect_opportunity(db, tid, "expense", "amount", "rise", now=NOW)
    t2 = _make_tenant("opp2"); c2 = connector(db, t2)
    weekly(db, t2, c2.id, "sale", [1100, 1000, 1000, 1000, 1000])
    assert D.detect_opportunity(db, t2, "sale", "amount", "drop", now=NOW) is None
    t3 = _make_tenant("opp3"); c3 = connector(db, t3)
    weekly(db, t3, c3.id, "sale", [500, 1000, 1000, 1000, 1000])
    assert D.detect_opportunity(db, t3, "sale", "amount", "drop", now=NOW) is None


def test_no_baseline_no_alerts(db, tid):
    connector(db, tid)
    assert D.detect_opportunity(db, tid, "sale", "amount", "drop", now=NOW) is None


# ---------- trend ----------
def test_trend_detects_steady_slide(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 500, 600, 700, 800, 900])   # newest first: falling
    a = D.detect_sustained_trend(db, tid, "sale", "amount", "drop", now=NOW)
    assert a and a.severity == AlertSeverity.high and a.evidence["r_squared"] > 0.9


def test_trend_detects_cost_climb_and_ignores_good_direction(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "expense", [900, 800, 700, 600, 500, 400])
    assert D.detect_sustained_trend(db, tid, "expense", "amount", "rise", now=NOW)
    assert D.detect_sustained_trend(db, tid, "expense", "amount", "drop", now=NOW) is None


def test_trend_ignores_noise_and_flat(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [900, 1200, 800, 1100, 850, 1000])
    assert D.detect_sustained_trend(db, tid, "sale", "amount", "drop", now=NOW) is None
    t2 = _make_tenant("flat"); c2 = connector(db, t2)
    weekly(db, t2, c2.id, "sale", [1000] * 6)
    assert D.detect_sustained_trend(db, t2, "sale", "amount", "drop", now=NOW) is None


# ---------- daily anomaly ----------
def daily(db, tid, cid, entity, values_by_date):
    for d, v in values_by_date.items():
        put(db, tid, cid, entity, dt.datetime(d.year, d.month, d.day, 9, 0), v)
    db.commit()


def tuesday_series(target_value, others=1000):
    """now is Wed 2026-03-18 -> target day is Tue 2026-03-17; history = previous 8 Tuesdays."""
    t = dt.date(2026, 3, 17)
    s = {t - dt.timedelta(days=7 * k): others + (k % 3) * 10 for k in range(1, 9)}
    s[t] = target_value
    return s


def test_daily_anomaly_flags_bad_day(db, tid):
    c = connector(db, tid)
    daily(db, tid, c.id, "sale", tuesday_series(300))
    a = D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW)
    assert a and a.evidence["weekday"] == "Tuesday" and a.evidence["robust_z"] <= -3


def test_daily_anomaly_respects_weekday_seasonality(db, tid):
    """Tuesdays are always ~300 because that's the business's slow day: a 300 Tuesday is normal
    even though other days are ~1000."""
    c = connector(db, tid)
    daily(db, tid, c.id, "sale", tuesday_series(300, others=300))
    daily(db, tid, c.id, "sale", {dt.date(2026, 3, 16) - dt.timedelta(days=k): 1000 for k in range(0, 30) if (dt.date(2026, 3, 16) - dt.timedelta(days=k)).weekday() != 1})
    assert D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW) is None


def test_daily_anomaly_needs_history_and_ignores_good_direction(db, tid):
    c = connector(db, tid)
    s = tuesday_series(300)
    daily(db, tid, c.id, "sale", {k: v for k, v in list(s.items())[:4]})     # only target + 3 history days
    assert D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW) is None
    t2 = _make_tenant("good"); c2 = connector(db, t2)
    daily(db, t2, c2.id, "sale", tuesday_series(3000))
    assert D.detect_daily_anomaly(db, t2, "sale", "amount", "drop", now=NOW) is None
    assert D.detect_daily_anomaly(db, t2, "sale", "amount", "rise", now=NOW)


def test_daily_anomaly_uses_tenant_timezone(db):
    """23:30 UTC Monday is Tuesday 02:30 in Nairobi (UTC+3): the sale belongs to the Nairobi Tuesday."""
    t = _make_tenant("tz")
    tenant = db.get(Tenant, t); tenant.timezone = "Africa/Nairobi"; db.commit()
    c = connector(db, t)
    tgt = dt.date(2026, 3, 17)
    for k in range(1, 9):
        d = tgt - dt.timedelta(days=7 * k)
        put(db, t, c.id, "sale", dt.datetime(d.year, d.month, d.day, 8, 0), 1000 + k)
    put(db, t, c.id, "sale", dt.datetime(2026, 3, 16, 23, 30), 5000)    # Nairobi Tuesday 02:30
    db.commit()
    a = D.detect_daily_anomaly(db, t, "sale", "amount", "rise", now=dt.datetime(2026, 3, 18, 6, 0))
    assert a and a.evidence["value"] == 5000 and a.evidence["day"] == "2026-03-17"


def test_daily_anomaly_mad_floor_on_constant_history(db, tid):
    c = connector(db, tid)
    t = dt.date(2026, 3, 17)
    s = {t - dt.timedelta(days=7 * k): 1000 for k in range(1, 9)}
    s[t] = 950                                  # tiny dip on perfectly regular data: must not explode
    daily(db, tid, c.id, "sale", s)
    assert D.detect_daily_anomaly(db, tid, "sale", "amount", "drop", now=NOW) is None


# ---------- data silence ----------
def test_silence_create_update_resolve_and_skip_never_synced(db, tid):
    c = connector(db, tid, freq="realtime")
    never = connector(db, tid, name="Never")           # never sent anything: onboarding, not incident
    put(db, tid, c.id, "sale", NOW - dt.timedelta(hours=30), 10); db.commit()
    out = D.detect_data_silence(db, tid, NOW)
    assert len(out) == 1 and out[0].evidence["connector_id"] == c.id
    assert out[0].severity == AlertSeverity.high
    again = D.detect_data_silence(db, tid, NOW + dt.timedelta(hours=1))
    assert again[0].id == out[0].id and again[0].evidence["silent_hours"] > out[0].evidence["silent_hours"] - 0.01
    put(db, tid, c.id, "sale", NOW + dt.timedelta(hours=2), 10); db.commit()
    assert D.detect_data_silence(db, tid, NOW + dt.timedelta(hours=2, minutes=1)) == []
    db.expire_all()
    assert db.get(Alert, out[0].id).state == AlertState.resolved


# ---------- threshold ----------
def test_threshold_wired_and_cooldown(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [100, 1000, 1000, 1000, 1000])
    a = detect_threshold_breach(db, tid, "sale", "amount", 500.0, "below", period_days=7, now=NOW)
    assert a and a.severity == AlertSeverity.critical and a.evidence["value"] == 100
    assert detect_threshold_breach(db, tid, "sale", "amount", 500.0, "below", period_days=7, now=NOW).id == a.id
    assert detect_threshold_breach(db, tid, "sale", "amount", 50.0, "below", period_days=7, now=NOW) is None
    # in-cycle: absolute_threshold flows through run_metric_detectors
    t2 = _make_tenant("thr"); c2 = connector(db, t2)
    weekly(db, t2, c2.id, "sale", [100, 1000, 1000, 1000, 1000])
    found = D.run_metric_detectors(db, t2, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW)
    assert any(x.evidence.get("dedup_key", "").startswith("threshold:") for x in found)


def test_threshold_skipped_for_tenant_without_data(db, tid):
    connector(db, tid)
    found = D.run_metric_detectors(db, tid, entity="sale", field="amount", direction="drop", absolute_threshold=500, now=NOW)
    assert found == []


# ---------- cooldown ----------
@pytest.mark.parametrize("state,hours_after,suppressed", [
    (AlertState.resolved, 2, True), (AlertState.resolved, 30, False),
    (AlertState.dismissed, 40, True), (AlertState.dismissed, 60, False),
    (AlertState.false_positive, 6 * 24, True), (AlertState.false_positive, 8 * 24, False),
])
def test_cooldown_after_human_close(db, state, hours_after, suppressed):
    t = _make_tenant("cool"); c = connector(db, t)
    later = NOW + dt.timedelta(hours=hours_after)
    weekly(db, t, c.id, "sale", [500, 1000, 1000, 1000, 1000], now=later)     # condition holds at `later`
    closed = Alert(tenant_id=t, severity=AlertSeverity.high, state=state, title="x", what_happened="y",
                   resolved_at=NOW, updated_at=NOW,
                   evidence={"dedup_key": "historical_deviation:sale:amount:7d", "metric": "sale.amount"})
    db.add(closed); db.commit()
    from app.core.detection import _in_cooldown
    assert _in_cooldown(db, t, "historical_deviation:sale:amount:7d", later) == suppressed
    b = detect_historical_deviation(db, t, "sale", "amount", now=later)
    assert (b is None) == suppressed


# ---------- alert engine ----------
def mk(db, t, sev, entity="sale", created=NOW, mag=-0.2, conf=0.7, cov=100, state=AlertState.detected, key=None):
    a = Alert(tenant_id=t, severity=sev, state=state, title="x", what_happened="y", magnitude=mag, confidence=conf,
              data_coverage_pct=cov, created_at=created, evidence={"metric": f"{entity}.amount", "dedup_key": key or uuid.uuid4().hex})
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_ranking_order(db, tid):
    crit = mk(db, tid, AlertSeverity.critical); high = mk(db, tid, AlertSeverity.high)
    med = mk(db, tid, AlertSeverity.medium); opp = mk(db, tid, AlertSeverity.opportunity)
    post_process_tenant(db, tid, NOW)
    for a in (crit, high, med, opp): db.refresh(a)
    assert crit.priority_rank > high.priority_rank > med.priority_rank > opp.priority_rank
    big = mk(db, tid, AlertSeverity.medium, mag=-0.5); post_process_tenant(db, tid, NOW); db.refresh(big); db.refresh(med)
    assert big.priority_rank > med.priority_rank
    assert 0 <= float(crit.priority_rank) <= 999.99


def test_low_coverage_lowers_rank(db, tid):
    a = mk(db, tid, AlertSeverity.high, cov=100); b = mk(db, tid, AlertSeverity.high, cov=20)
    assert priority_score(a, NOW) > priority_score(b, NOW)


def test_correlation_symmetry_and_clearing(db, tid):
    a = mk(db, tid, AlertSeverity.high, "sale"); b = mk(db, tid, AlertSeverity.medium, "sale"); c = mk(db, tid, AlertSeverity.high, "expense")
    post_process_tenant(db, tid, NOW)
    for x in (a, b, c): db.refresh(x)
    assert a.correlated_alert_ids == [b.id] and b.correlated_alert_ids == [a.id] and not c.correlated_alert_ids
    b.state = AlertState.resolved; db.commit()
    post_process_tenant(db, tid, NOW)
    db.refresh(a)
    assert not a.correlated_alert_ids               # stale link cleared when the partner closed


def test_correlation_window_and_cap(db, tid):
    old = mk(db, tid, AlertSeverity.high, "sale", created=NOW - dt.timedelta(days=10))
    new = mk(db, tid, AlertSeverity.high, "sale")
    post_process_tenant(db, tid, NOW); db.refresh(old); db.refresh(new)
    assert not new.correlated_alert_ids
    many = [mk(db, tid, AlertSeverity.medium, "product") for _ in range(14)]
    post_process_tenant(db, tid, NOW); db.refresh(many[0])
    assert len(many[0].correlated_alert_ids) == 10


def test_escalation_stamp_once_and_only_when_untouched(db, tid):
    crit_old = mk(db, tid, AlertSeverity.critical, created=NOW - dt.timedelta(hours=5), key="k1")
    crit_new = mk(db, tid, AlertSeverity.critical, created=NOW - dt.timedelta(hours=1), key="k2")
    ack = mk(db, tid, AlertSeverity.critical, created=NOW - dt.timedelta(hours=9), state=AlertState.acknowledged, key="k3")
    med_old = mk(db, tid, AlertSeverity.medium, created=NOW - dt.timedelta(days=3), key="k4")
    r = post_process_tenant(db, tid, NOW)
    for x in (crit_old, crit_new, ack, med_old): db.refresh(x)
    assert r["escalated"] == 1 and crit_old.evidence["escalated_at"]
    assert not any((x.evidence or {}).get("escalated_at") for x in (crit_new, ack, med_old))
    stamp = crit_old.evidence["escalated_at"]
    assert post_process_tenant(db, tid, NOW + dt.timedelta(hours=1))["escalated"] == 0
    db.refresh(crit_old); assert crit_old.evidence["escalated_at"] == stamp
    assert crit_old.priority_rank > crit_new.priority_rank


def test_upsert_keeps_escalation_stamp(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [500, 1000, 1000, 1000, 1000])
    a = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    a.created_at = NOW - dt.timedelta(hours=30); db.commit()
    post_process_tenant(db, tid, NOW)
    b = detect_historical_deviation(db, tid, "sale", "amount", now=NOW)
    assert b.id == a.id and b.evidence.get("escalated_at")


# ---------- cycle ----------
def test_run_tenant_cycle_end_to_end(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 500, 600, 700, 800, 900])
    weekly(db, tid, c.id, "expense", [1500, 1000, 1000, 1000, 1000])
    metrics = [{"entity": "sale", "field": "amount", "direction": "drop"},
               {"entity": "expense", "field": "amount", "direction": "rise"}]
    r = D.run_tenant_cycle(db, tid, metrics, NOW)
    assert r["errors"] == 0 and r["post"]["open"] >= 3
    kinds = {a.evidence["dedup_key"].split(":")[0] for a in r["alerts"]}
    assert {"trend"} <= kinds and ("historical_deviation" in kinds or any("deviation" in k for k in kinds) or len(kinds) >= 2)
    n = db.query(Alert).filter_by(tenant_id=tid).count()
    D.run_tenant_cycle(db, tid, metrics, NOW)
    assert db.query(Alert).filter_by(tenant_id=tid).count() == n      # idempotent


def test_cycle_isolates_failing_metric(db, tid):
    c = connector(db, tid)
    weekly(db, tid, c.id, "sale", [400, 500, 600, 700, 800, 900])
    r = D.run_tenant_cycle(db, tid, [{"entity": "sale", "field": "bogus", "direction": "drop"},
                                     {"entity": "sale", "field": "amount", "direction": "drop"}], NOW)
    assert r["alerts"]                            # the good metric still produced alerts


# ---------- API + delivery ----------
def test_api_exposes_rank_correlation_escalation_and_run_all(client, tenant_a, tenant_b):
    email, pw, tokens = register(client, tenant_a)
    h = bearer(tokens)
    s = SessionLocal()
    try:
        c = connector(s, tenant_a)
        weekly(s, tenant_a, c.id, "expense", [1500, 1000, 1000, 1000, 1000], now=dt.datetime.utcnow())
    finally:
        s.close()
    r = client.post("/api/detection/run/all", json={"tenant_id": tenant_a}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["alerts"] and r.json()["errors"] == 0
    lst = client.get("/api/alerts/", params={"tenant_id": tenant_a}, headers=h).json()
    assert lst and all("priority_rank" in a and "escalated" in a and "correlated_alert_ids" in a for a in lst)
    ranks = [a["priority_rank"] or 0 for a in lst]
    assert ranks == sorted(ranks, reverse=True)
    d = client.get(f"/api/alerts/{lst[0]['id']}/detail", params={"tenant_id": tenant_a}, headers=h).json()
    assert "correlated_alert_ids" in d and d["escalated"] is False
    # tenant isolation + auth
    assert client.post("/api/detection/run/all", json={"tenant_id": tenant_b}, headers=h).status_code == 403
    assert client.post("/api/detection/run/all", json={"tenant_id": tenant_a}).status_code == 401


def test_metric_api_direction_and_threshold(client, tenant_a):
    _, _, tokens = register(client, tenant_a); h = bearer(tokens)
    r = client.post("/api/monitoring/metrics", json={"tenant_id": tenant_a, "entity": "expense", "direction": "rise", "absolute_threshold": 5000}, headers=h)
    assert r.status_code == 200 and r.json()["direction"] == "rise" and float(r.json()["absolute_threshold"]) == 5000
    mid = r.json()["id"]
    r = client.patch(f"/api/monitoring/metrics/{mid}", params={"tenant_id": tenant_a}, json={"direction": "drop"}, headers=h)
    assert r.json()["direction"] == "drop"
    assert client.post("/api/monitoring/metrics", json={"tenant_id": tenant_a, "entity": "x", "direction": "up"}, headers=h).status_code == 422
    assert client.post("/api/monitoring/metrics", json={"tenant_id": tenant_a, "entity": "y", "absolute_threshold": -1}, headers=h).status_code == 422


def test_escalation_delivery_alert_and_escalation_each_once(db, tid):
    from app.core import notifications as N
    from app.models.config import NotificationChannel, NotificationDelivery
    db.add(NotificationChannel(tenant_id=tid, channel_type="email", target="owner@example.com", min_severity="high", enabled=True,
                               created_at=dt.datetime.utcnow() - dt.timedelta(days=1)))
    db.commit()
    a = mk(db, tid, AlertSeverity.critical, created=dt.datetime.utcnow() - dt.timedelta(hours=5), key="esc1")
    N.enqueue_deliveries(db)
    kinds = sorted(d.kind for d in db.query(NotificationDelivery).filter_by(alert_id=a.id))
    assert kinds == ["alert"]
    post_process_tenant(db, tid)
    N.enqueue_deliveries(db); N.enqueue_deliveries(db)
    kinds = sorted(d.kind for d in db.query(NotificationDelivery).filter_by(alert_id=a.id))
    assert kinds == ["alert", "escalation"]
