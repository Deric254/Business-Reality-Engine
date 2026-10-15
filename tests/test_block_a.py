"""Block A: FX, relationships, drivers, quality, discovery, health, forecast/risk/unknown detectors, holidays, locale."""
import datetime as dt
from datetime import date

import pytest

from app.core import fx as fx_mod
from app.core.business_health import discover, health_summary, quality_report
from app.core.context import attach_context, is_holiday, sync_holidays
from app.core.db import SessionLocal
from app.core.detectors import detect_daily_anomaly
from app.core.detectors_extra import detect_forecast_deviation, detect_insufficient_data, detect_profit_risk
from app.core.ingestion import NormalizedEvent, ingest_events_bulk
from app.core.relationships import change_drivers, relationships_for
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent, Connector, ResolvedEntity, Tenant
from tests.conftest import _make_tenant, bearer, register

NOW = dt.datetime(2026, 6, 15, 12, 0)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


def mk_conn(db, t):
    c = Connector(tenant_id=t, name="POS", connector_type="webhook", expected_frequency="realtime", is_active=True)
    db.add(c); db.commit()
    return c


def add(db, t, c, entity, ts, amount, **kw):
    db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity=entity, event_type=f"{entity}.x", timestamp=ts,
                         amount=amount, validation_status=kw.pop("status", "validated"), **kw))


def weekly(db, t, c, entity, values, now=NOW, period=7, **kw):
    """values oldest -> newest, one event mid-period each."""
    n = len(values)
    for i, v in enumerate(values):
        add(db, t, c, entity, now - dt.timedelta(days=period * (n - i) - period // 2), v, **kw)
    db.commit()


# ---------------- FX ----------------

def ev(t, c, **kw):
    base = dict(tenant_id=t, connector_id=c.id, source="t", entity="sale", event_type="sale.created",
                timestamp=dt.datetime.utcnow() - dt.timedelta(days=1), amount=10.0, currency="USD")
    base.update(kw)
    return NormalizedEvent(**base)


def test_fx_missing_rate_flags_then_reprocess_converts(db):
    t = _make_tenant("fx"); c = mk_conn(db, t)
    r = ingest_events_bulk(db, [ev(t, c, entity_id="a1")])[0]
    assert r["validation_status"] == "failed" and "no FX rate for USD" in r["errors"][0]
    from app.models.config import FxRate
    db.add(FxRate(tenant_id=t, currency="USD", rate_to_base=130, effective_date=date(2020, 1, 1))); db.commit()
    out = fx_mod.reprocess_unconverted(db, t)
    assert out == {"converted": 1, "still_missing_rate": 0}
    row = db.get(BusinessEvent, r["id"])
    assert row.validation_status == "validated" and float(row.amount) == 1300.0 and row.currency == "KES"
    assert row.metadata_json["fx"]["original_currency"] == "USD" and row.metadata_json["fx"]["original_amount"] in ("10.0", "10.00")


def test_fx_converts_at_ingestion_uses_latest_rate_not_after_event(db):
    from app.models.config import FxRate
    t = _make_tenant("fx2"); c = mk_conn(db, t)
    today = dt.datetime.utcnow()
    db.add_all([FxRate(tenant_id=t, currency="USD", rate_to_base=100, effective_date=(today - dt.timedelta(days=30)).date()),
                FxRate(tenant_id=t, currency="USD", rate_to_base=200, effective_date=(today + dt.timedelta(days=30)).date())]); db.commit()
    r = ingest_events_bulk(db, [ev(t, c, entity_id="b1"), ev(t, c, entity_id="b2", currency="KES"), ev(t, c, entity_id="b3", currency=None)])
    assert [x["validation_status"] for x in r] == ["validated"] * 3
    assert float(db.get(BusinessEvent, r[0]["id"]).amount) == 1000.0          # old rate, never the future one
    assert float(db.get(BusinessEvent, r[1]["id"]).amount) == 10.0            # base currency untouched
    assert float(db.get(BusinessEvent, r[2]["id"]).amount) == 10.0


def test_fx_unconverted_excluded_from_totals(db):
    from app.core.detection import _sum_metric
    t = _make_tenant("fx3"); c = mk_conn(db, t)
    ingest_events_bulk(db, [ev(t, c, entity_id="c1"), ev(t, c, entity_id="c2", currency="KES", amount=5)])
    now = dt.datetime.utcnow() + dt.timedelta(days=1)
    assert _sum_metric(db, t, "sale", "amount", now - dt.timedelta(days=5), now) == 5.0


def test_fx_api_owner_only_and_validation(client):
    t = _make_tenant("fxapi")
    _, _, owner = register(client, t, "owner")
    _, _, staff = register(client, t, "staff")
    body = {"currency": "usd", "rate_to_base": 129.5, "effective_date": "2026-01-01"}
    assert client.put(f"/api/business/fx?tenant_id={t}", json=body, headers=bearer(staff)).status_code == 403
    assert client.put(f"/api/business/fx?tenant_id={t}", json={**body, "currency": "KES"}, headers=bearer(owner)).status_code == 400
    assert client.put(f"/api/business/fx?tenant_id={t}", json={**body, "rate_to_base": -1}, headers=bearer(owner)).status_code == 422
    assert client.put(f"/api/business/fx?tenant_id={t}", json={**body, "currency": "US1"}, headers=bearer(owner)).status_code == 400
    r = client.put(f"/api/business/fx?tenant_id={t}", json=body, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["converted"] == 0
    lst = client.get(f"/api/business/fx?tenant_id={t}", headers=bearer(staff)).json()
    assert lst[0]["currency"] == "USD" and lst[0]["rate_to_base"] == 129.5


# ---------------- relationships ----------------

def seed_rel(db):
    t = _make_tenant("rel"); c = mk_conn(db, t)
    ents = {n: ResolvedEntity(tenant_id=t, entity_type=ty, canonical_name=n) for n, ty in
            [("Amina", "customer"), ("Brian", "customer"), ("Tea", "product"), ("Milk", "product"), ("Acme", "supplier")]}
    db.add_all(ents.values()); db.commit()
    cur, prev = NOW - dt.timedelta(days=3), NOW - dt.timedelta(days=10)
    add(db, t, c, "sale", cur, 100, resolved_customer_id=ents["Amina"].id, resolved_product_id=ents["Tea"].id, location="Meru")
    add(db, t, c, "sale", cur, 100, resolved_customer_id=ents["Amina"].id, resolved_product_id=ents["Tea"].id, location="Meru")
    add(db, t, c, "sale", cur, 50, resolved_customer_id=ents["Brian"].id, resolved_product_id=ents["Milk"].id, location="Nairobi")
    add(db, t, c, "sale", prev, 400, resolved_customer_id=ents["Amina"].id, resolved_product_id=ents["Tea"].id, location="Meru")
    add(db, t, c, "sale", prev, 50, resolved_customer_id=ents["Brian"].id, location="Nairobi")
    add(db, t, c, "expense", cur, 60, resolved_supplier_id=ents["Acme"].id)
    db.commit()
    return t, ents


def test_relationships_concentration_statements(db):
    t, e = seed_rel(db)
    out = relationships_for(db, t, e["Amina"].id, days=30, now=NOW)
    assert out["total"] == 200 + 400 and out["share_of_business"] == pytest.approx(600 / 700, abs=1e-3)
    assert out["linked"]["product"][0]["name"] == "Tea" and out["linked"]["product"][0]["share"] == 1.0
    assert out["linked"]["location"][0]["name"] == "Meru"
    assert any("accounts for 86% of all sales" in s for s in out["statements"])
    assert any("involve one product (Tea)" in s for s in out["statements"])
    sup = relationships_for(db, t, e["Acme"].id, days=30, now=NOW)
    assert sup["measure"] == "expense amount" and sup["total"] == 60


def test_relationships_tenant_isolated(db):
    t, e = seed_rel(db)
    other = _make_tenant("rel2")
    assert relationships_for(db, other, e["Amina"].id) is None
    assert relationships_for(db, t, "00000000-0000-0000-0000-000000000000") is None


def test_change_drivers_name_the_cause(db):
    t, e = seed_rel(db)
    d = change_drivers(db, t, "sale", NOW - dt.timedelta(days=7), NOW)
    names = {x["name"] for x in d["drivers"]}
    assert "Amina" in names and "Tea" in names and "Meru" in names
    assert all(x["share_of_change"] >= 0.5 for x in d["drivers"])
    assert any("came from customer 'Amina'" in s for s in d["statements"])


def test_change_drivers_silent_when_no_dominant_cause(db):
    t = _make_tenant("drv"); c = mk_conn(db, t)
    for loc in ("A", "B", "C", "D"):
        add(db, t, c, "sale", NOW - dt.timedelta(days=10), 100, location=loc)
        add(db, t, c, "sale", NOW - dt.timedelta(days=3), 50, location=loc)
    db.commit()
    assert change_drivers(db, t, "sale", NOW - dt.timedelta(days=7), NOW)["drivers"] == []


# ---------------- quality / discovery / health ----------------

def test_quality_report_numbers(db):
    t = _make_tenant("q"); c = mk_conn(db, t)
    now = dt.datetime.utcnow()
    for i in range(8):
        add(db, t, c, "sale", now - dt.timedelta(days=1), 10, customer_id="x" if i < 4 else None)
    add(db, t, c, "sale", now - dt.timedelta(days=1), -5, status="failed", validation_errors=["negative amount on sale/expense — check source mapping"])
    add(db, t, c, "sale", now - dt.timedelta(days=1), 5, status="failed", validation_errors=["no FX rate for USD on 2026-01-01"])
    db.commit()
    q = quality_report(db, t, 30)
    assert q["events"] == 10 and q["failed"] == 2 and q["failed_pct"] == 20.0
    assert q["sales_missing_pct"]["customer"] == 60.0
    assert q["by_connector"][0]["failed"] == 2
    assert any("waiting for an exchange rate" in n for n in q["notes"]) and any("failed validation" in n for n in q["notes"])
    assert 0 <= q["score"] < 80
    assert quality_report(db, _make_tenant("q0"), 30)["score"] is None


def test_discovery_suggests_unmonitored_metrics(db):
    t = _make_tenant("disc"); c = mk_conn(db, t)
    for i in range(240):
        add(db, t, c, "sale", NOW - dt.timedelta(days=i // 4), 10, quantity=1)
    for i in range(5):
        add(db, t, c, "expense", NOW - dt.timedelta(days=i), 10)
    db.commit()
    d = discover(db, t, NOW)
    by = {(s["entity"], s["field"]): s for s in d["suggested_metrics"]}
    assert ("sale", "amount") in by and ("sale", "quantity") in by and ("expense", "amount") not in by   # <20 events
    assert by[("sale", "amount")]["enough_history"] is True and by[("sale", "amount")]["period_days"] == 7
    assert by[("sale", "amount")]["direction"] == "drop"
    assert {e["entity"] for e in d["entities"]} == {"sale", "expense"}
    assert discover(db, _make_tenant("d0"))["next_steps"]


def test_health_summary_status_and_score(db):
    t = _make_tenant("hs"); c = mk_conn(db, t)
    now = dt.datetime.utcnow()
    add(db, t, c, "sale", now - dt.timedelta(days=2), 1000); add(db, t, c, "expense", now - dt.timedelta(days=2), 400)
    add(db, t, c, "sale", now - dt.timedelta(days=40), 500)
    db.commit()
    h = health_summary(db, t, 30)
    assert h["financials"]["profit"] == 600 and h["financials"]["sales_change_pct"] == 100.0
    assert h["status"] in ("healthy", "watch") and h["score"] <= 100
    db.add(Alert(tenant_id=t, severity=AlertSeverity.critical, state=AlertState.detected, title="Boom", what_happened="x", evidence={}))
    db.commit()
    h2 = health_summary(db, t, 30)
    assert h2["status"] == "attention" and h2["score"] <= h["score"] - 25 + 1 and h2["top_alert"]["title"] == "Boom"
    assert health_summary(db, _make_tenant("hs0"), 30)["status"] == "no_data"


def test_business_api_requires_auth_and_isolates(client, db):
    t, e = seed_rel(db)
    other = _make_tenant("apiother")
    assert client.get(f"/api/business/health?tenant_id={t}").status_code == 401
    _, _, owner_other = register(client, other, "owner")
    assert client.get(f"/api/business/health?tenant_id={t}", headers=bearer(owner_other)).status_code == 403
    assert client.get(f"/api/business/relationships/{e['Amina'].id}?tenant_id={other}", headers=bearer(owner_other)).status_code == 404
    _, _, owner = register(client, t, "owner")
    for path in ("health", "quality", "discovery", f"relationships/{e['Amina'].id}", "context", "fx"):
        sep = "&" if "?" in path else "?"
        r = client.get(f"/api/business/{path}{sep}tenant_id={t}", headers=bearer(owner))
        assert r.status_code == 200, path
    assert client.get(f"/api/business/health?tenant_id={t}&days=0", headers=bearer(owner)).status_code == 422


# ---------------- detectors ----------------

def test_forecast_deviation_fires_below_trend_and_not_on_steady(db):
    t = _make_tenant("fc"); c = mk_conn(db, t)
    weekly(db, t, c, "sale", [1000, 1050, 1100, 1150, 1200, 1250, 500])
    a = detect_forecast_deviation(db, t, "sale", "amount", "drop", now=NOW)
    assert a and a.kind == "forecast" and a.evidence["forecast"] > 1000 and a.evidence["actual"] == 500
    assert a.severity == AlertSeverity.high and float(a.magnitude) < -0.5
    t2 = _make_tenant("fc2"); c2 = mk_conn(db, t2)
    weekly(db, t2, c2, "sale", [1000, 1050, 1100, 1150, 1200, 1250, 1300])
    assert detect_forecast_deviation(db, t2, "sale", "amount", "drop", now=NOW) is None
    t3 = _make_tenant("fc3"); c3 = mk_conn(db, t3)
    weekly(db, t3, c3, "sale", [1000, 1000, 1000])                      # too little history: silent, not guessing
    assert detect_forecast_deviation(db, t3, "sale", "amount", "drop", now=NOW) is None


def test_forecast_deviation_dedups_open_alert(db):
    t = _make_tenant("fcd"); c = mk_conn(db, t)
    weekly(db, t, c, "sale", [1000, 1050, 1100, 1150, 1200, 1250, 500])
    a1 = detect_forecast_deviation(db, t, "sale", "amount", "drop", now=NOW)
    a2 = detect_forecast_deviation(db, t, "sale", "amount", "drop", now=NOW)
    assert a1.id == a2.id
    assert db.query(Alert).filter(Alert.tenant_id == t).count() == 1


def test_profit_risk_projection_and_already_negative(db):
    t = _make_tenant("risk"); c = mk_conn(db, t)
    weekly(db, t, c, "sale", [1000] * 6)
    weekly(db, t, c, "expense", [500, 600, 700, 800, 900, 980])         # costs closing in on sales
    a = detect_profit_risk(db, t, now=NOW)
    assert a and a.kind == "risk" and a.evidence["projection"] is True and a.evidence["periods_to_zero"] <= 4
    assert "not a certainty" in a.what_happened and 0 < float(a.confidence) <= 0.7
    t2 = _make_tenant("risk2"); c2 = mk_conn(db, t2)
    weekly(db, t2, c2, "sale", [1000] * 6); weekly(db, t2, c2, "expense", [1200] * 6)
    b = detect_profit_risk(db, t2, now=NOW)
    assert b and b.severity == AlertSeverity.high and b.title == "Spending exceeds sales"
    t3 = _make_tenant("risk3"); c3 = mk_conn(db, t3)
    weekly(db, t3, c3, "sale", [1000] * 6); weekly(db, t3, c3, "expense", [400] * 6)
    assert detect_profit_risk(db, t3, now=NOW) is None
    t4 = _make_tenant("risk4"); c4 = mk_conn(db, t4)
    weekly(db, t4, c4, "sale", [1000, 1000]); weekly(db, t4, c4, "expense", [2000, 2000])
    assert detect_profit_risk(db, t4, now=NOW) is None                  # two periods is not a trend


def test_insufficient_data_alert_then_self_resolves(db):
    t = _make_tenant("unk"); c = mk_conn(db, t)
    weekly(db, t, c, "sale", [100, 100])
    a = detect_insufficient_data(db, t, "sale", "amount", 7, 4, now=NOW)
    assert a and a.kind == "unknown" and a.severity == AlertSeverity.informational and a.evidence["periods_with_data"] == 2
    assert detect_insufficient_data(db, _make_tenant("unk0"), "sale", "amount", 7, 4, now=NOW) is None   # no data at all: onboarding
    weekly(db, t, c, "sale", [100, 100, 100, 100, 100], now=NOW - dt.timedelta(days=14))
    assert detect_insufficient_data(db, t, "sale", "amount", 7, 4, now=NOW) is None
    db.expire_all()
    assert db.get(Alert, a.id).state == AlertState.resolved


# ---------------- holidays / external context ----------------

def test_sync_holidays_idempotent_and_needs_country(db):
    t = _make_tenant("hol")
    assert sync_holidays(db, t)["added"] == 0 and sync_holidays(db, t)["note"]
    db.get(Tenant, t).country = "KE"; db.commit()
    first = sync_holidays(db, t, [2026])
    assert first["added"] > 5 and sync_holidays(db, t, [2026])["added"] == 0
    assert is_holiday(db, t, date(2026, 12, 25)) == "Christmas Day" and is_holiday(db, t, date(2026, 12, 24)) is None
    db.get(Tenant, t).country = "ZZ"; db.commit()
    assert sync_holidays(db, t, [2026])["note"].startswith("No holiday calendar")


def test_holiday_caps_daily_anomaly_and_context_is_attached(db):
    t = _make_tenant("hol2"); c = mk_conn(db, t)
    db.get(Tenant, t).country = "KE"; db.commit()
    sync_holidays(db, t, [2026])
    # Wed 2026-12-23 is "yesterday"; make 2026-12-25 the target instead: now = Dec 26 noon UTC (Friday=25th).
    now = dt.datetime(2026, 12, 26, 12, 0)
    target = date(2026, 12, 25)
    for k in range(1, 9):
        d = target - dt.timedelta(days=7 * k)
        add(db, t, c, "sale", dt.datetime(d.year, d.month, d.day, 9), 1000)
    add(db, t, c, "sale", dt.datetime(2026, 12, 25, 9), 100)
    db.commit()
    a = detect_daily_anomaly(db, t, "sale", "amount", "drop", now=now)
    assert a and a.severity == AlertSeverity.informational and a.evidence["holiday"] == "Christmas Day"
    assert attach_context(db, a) is True
    assert a.evidence["external_context"][0]["name"] == "Christmas Day"
    assert attach_context(db, a) is False                                   # unchanged: no needless write


def test_context_api_owner_only_and_validation(client):
    t = _make_tenant("ctx"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    body = {"kind": "promotion", "name": "Black Friday", "starts_on": "2026-11-27", "ends_on": "2026-11-30"}
    assert client.post(f"/api/business/context?tenant_id={t}", json=body, headers=bearer(staff)).status_code == 403
    assert client.post(f"/api/business/context?tenant_id={t}", json={**body, "kind": "x"}, headers=bearer(owner)).status_code == 400
    assert client.post(f"/api/business/context?tenant_id={t}", json={**body, "ends_on": "2026-01-01"}, headers=bearer(owner)).status_code == 400
    r = client.post(f"/api/business/context?tenant_id={t}", json=body, headers=bearer(owner)); assert r.status_code == 200
    assert client.post(f"/api/business/context?tenant_id={t}", json=body, headers=bearer(owner)).status_code == 409
    assert len(client.get(f"/api/business/context?tenant_id={t}", headers=bearer(staff)).json()) == 1
    assert client.delete(f"/api/business/context/{r.json()['id']}?tenant_id={t}", headers=bearer(owner)).status_code == 204
    assert client.get(f"/api/business/context?tenant_id={t}", headers=bearer(owner)).json() == []


def test_tenant_locale_settings_validation(client):
    t = _make_tenant("loc"); _, _, owner = register(client, t, "owner")
    h = bearer(owner)
    assert client.patch("/api/tenants/me", json={"country": "ke", "language": "sw"}, headers=h).json()["country"] == "KE"
    assert client.get("/api/tenants/me", headers=h).json()["language"] == "sw"
    assert client.patch("/api/tenants/me", json={"country": "KEN"}, headers=h).status_code == 400
    assert client.patch("/api/tenants/me", json={"language": "xx"}, headers=h).status_code == 400


# ---------------- LLM fallback, language, advisory ----------------

class FakeProvider:
    name = "fake"

    def __init__(self, reply="sales", raises=False):
        self.reply, self.raises, self.calls = reply, raises, []

    def generate_json(self, system_prompt, user_content, max_tokens=400):
        self.calls.append((system_prompt, user_content))
        if self.raises:
            raise RuntimeError("down")
        return self.reply


@pytest.mark.parametrize("reply,answered", [("sales", True), ("  Sales.\n", True), ("none", False), ("DROP TABLE x", False),
                                            ("sales and expenses", False), ("", False), ("profit", True)])
def test_llm_intent_fallback_only_accepts_known_intents(db, monkeypatch, reply, answered):
    from app.core import business_questions as bq
    t = _make_tenant("llm"); c = mk_conn(db, t)
    add(db, t, c, "sale", dt.datetime.utcnow() - dt.timedelta(days=1), 100); db.commit()
    fake = FakeProvider(reply)
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: fake)
    out = bq.answer_question(db, t, "bei ya mauzo ikoje wiki hii")
    assert out["answered"] is answered and out["advisory"]
    assert fake.calls and "Do not answer the question" in fake.calls[0][0]
    if answered and reply.strip(" .\n").lower() == "sales":
        assert out["intent"] == "sales" and out["classified_by"] == "llm" and out["answer_source"] == "deterministic"


def test_llm_fallback_failure_and_no_call_when_rules_match(db, monkeypatch):
    from app.core import business_questions as bq
    t = _make_tenant("llm2")
    boom = FakeProvider(raises=True)
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: boom)
    out = bq.answer_question(db, t, "what is the weather")
    assert out["answered"] is False and out["suggestions"]
    ok = FakeProvider("expenses")
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: ok)
    r = bq.answer_question(db, t, "how are sales this month?")
    assert r["classified_by"] == "rules" and ok.calls == []              # keyword hit: the model is never consulted


def test_profit_answer_and_narrative_use_tenant_language(db, monkeypatch):
    from app.core import business_questions as bq
    from app.reasoning import llm_reasoning as lr
    t = _make_tenant("lang"); db.get(Tenant, t).language = "sw"; db.commit()
    fake = FakeProvider("Faida imeshuka.")
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: fake)
    out = bq.answer_question(db, t, "why did profit fall?")
    assert "Swahili" in fake.calls[0][0] and out["advisory"].startswith("Hii ni msaada")
    a = Alert(tenant_id=t, severity=AlertSeverity.high, state=AlertState.detected, title="x", what_happened="y", evidence={"k": 1})
    db.add(a); db.commit()
    nar = FakeProvider('{"narrative": "n", "recommended_action": "r"}')
    monkeypatch.setattr(lr, "get_provider", lambda *a, **k: nar)
    lr.reason_about_alert(db, a)
    assert "Swahili" in nar.calls[0][0] and a.ai_evidence_hash.endswith(":sw")
    db.get(Tenant, t).language = "en"; db.commit()
    lr.reason_about_alert(db, a)                                         # language changed -> regenerated, not stale cache
    assert len(nar.calls) == 2


def test_alert_detail_has_kind_context_and_advisory(client, db):
    t = _make_tenant("det"); _, _, owner = register(client, t, "owner")
    a = Alert(tenant_id=t, severity=AlertSeverity.medium, state=AlertState.detected, title="x", what_happened="y", kind="forecast",
              evidence={"external_context": [{"kind": "holiday", "name": "Madaraka Day", "starts_on": "2026-06-01", "ends_on": "2026-06-01"}]})
    db.add(a); db.commit()
    r = client.get(f"/api/alerts/{a.id}/detail?tenant_id={t}", headers=bearer(owner)).json()
    assert r["kind"] == "forecast" and r["external_context"][0]["name"] == "Madaraka Day" and r["advisory"].startswith("This is decision support")


def test_cycle_sets_kind_and_attaches_drivers(db):
    from app.core.detectors import run_tenant_cycle
    t = _make_tenant("cyc"); c = mk_conn(db, t)
    weekly(db, t, c, "sale", [1000, 1000, 1000, 1000, 1000, 1000, 1000, 300],
           resolved_customer_id=None)
    out = run_tenant_cycle(db, t, [dict(entity="sale", field="amount", period_days=7, baseline_periods=4)], now=NOW)
    assert out["errors"] == 0
    kinds = {a.kind for a in db.query(Alert).filter(Alert.tenant_id == t)}
    assert "deviation" in kinds and None not in kinds
    dev = db.query(Alert).filter(Alert.tenant_id == t, Alert.kind == "deviation").one()
    assert "change_drivers" not in dev.evidence or isinstance(dev.evidence["change_drivers"]["statements"], list)
