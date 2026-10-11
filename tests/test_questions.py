"""Section 45: question routing, period parsing, deterministic answers, API validation and isolation."""
import datetime as dt

import pytest

from app.core.business_questions import answer_question
from app.core.db import SessionLocal
from app.core.question_router import classify, parse_period
from app.models.alerts import Alert, AlertSeverity, AlertState
from app.models.core import BusinessEvent, Connector, ResolvedEntity
from tests.conftest import _make_tenant, bearer, register


@pytest.mark.parametrize("q,intent", [
    ("Why did profit fall?", "profit"), ("what is my margin", "profit"),
    ("How are sales this month?", "sales"), ("what was revenue last week", "sales"),
    ("what are my expenses", "expenses"), ("how much did we spend", "expenses"),
    ("Who are my top customers?", "top_customers"), ("best customers", "top_customers"),
    ("what are my best selling products", "top_products"), ("top items", "top_products"),
    ("who are my biggest suppliers", "top_suppliers"),
    ("What will sales be next week?", "forecast"), ("forecast expenses", "forecast"),
    ("What needs my attention?", "attention"), ("any problems?", "attention"),
    ("is my data up to date", "data_health"), ("which connectors are stale", "data_health"),
    ("what is the weather", None), ("hello", None),
])
def test_classify(q, intent):
    assert classify(q) == intent


@pytest.mark.parametrize("q,days", [
    ("sales last 14 days", 14), ("sales over the past 2 weeks", 14), ("last 3 months", 90),
    ("sales this week", 7), ("sales last month", 30), ("profit this quarter", 90),
    ("sales today", 1), ("sales yesterday", 1), ("sales", 30), ("last 999 years", 365),
])
def test_parse_period(q, days):
    assert parse_period(q)[0] == days


def seed(db, t):
    c = Connector(tenant_id=t, name="POS", connector_type="webhook", expected_frequency="realtime", is_active=True)
    db.add(c); db.commit()
    now = dt.datetime.utcnow()
    ents = {n: ResolvedEntity(tenant_id=t, entity_type=ty, canonical_name=n) for n, ty in
            [("Amina", "customer"), ("Brian", "customer"), ("Tea", "product"), ("Acme Supplies", "supplier")]}
    db.add_all(ents.values()); db.commit()

    def ev(entity, days_ago, amount, **fk):
        db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity=entity, event_type=f"{entity}.x",
                             timestamp=now - dt.timedelta(days=days_ago), amount=amount, validation_status="validated", **fk))
    for i in range(6):                                      # current 30d: sales 1000 each week-ish
        ev("sale", i * 4 + 1, 300, resolved_customer_id=ents["Amina"].id, resolved_product_id=ents["Tea"].id)
    ev("sale", 3, 100, resolved_customer_id=ents["Brian"].id)
    ev("sale", 40, 1000)                                    # previous period
    ev("expense", 5, 400, resolved_supplier_id=ents["Acme Supplies"].id)
    ev("expense", 45, 200)
    db.commit()
    return c


def test_answers_are_computed_from_data():
    db = SessionLocal()
    try:
        t = _make_tenant("q"); seed(db, t)
        sales = answer_question(db, t, "How are sales this month?")
        assert sales["intent"] == "sales" and sales["answered"] and sales["evidence"]["current"] == 1900.0
        assert sales["evidence"]["previous"] == 1000.0 and "up 90.0%" in sales["answer"]
        exp = answer_question(db, t, "what are my expenses this month")
        assert exp["evidence"]["current"] == 400.0
        cust = answer_question(db, t, "who are my top customers")
        assert [i["name"] for i in cust["evidence"]["items"]] == ["Amina", "Brian"] and cust["evidence"]["items"][0]["amount"] == 1800.0
        assert answer_question(db, t, "best selling products")["evidence"]["items"][0]["name"] == "Tea"
        sup = answer_question(db, t, "top suppliers")
        assert sup["evidence"]["items"][0]["name"] == "Acme Supplies" and sup["evidence"]["items"][0]["amount"] == 400.0
        prof = answer_question(db, t, "why did profit change")
        assert prof["intent"] == "profit" and prof["evidence"]["profit"]["current"] == 1500.0
    finally:
        db.close()


def test_empty_tenant_gets_honest_answers_not_errors():
    db = SessionLocal()
    try:
        t = _make_tenant("qe")
        for q in ("how are sales", "top customers", "what will sales be next week", "what needs attention", "is my data up to date", "profit"):
            r = answer_question(db, t, q)
            assert r["answered"] and r["answer"]
        assert "not enough" in answer_question(db, t, "what will sales be next week")["answer"]
        assert "no open alerts" in answer_question(db, t, "what needs attention")["answer"]
    finally:
        db.close()


def test_unknown_question_is_declined_with_suggestions():
    db = SessionLocal()
    try:
        r = answer_question(db, _make_tenant("qu"), "what is the capital of France")
        assert r["answered"] is False and r["intent"] is None and r["suggestions"] and "can't answer" in r["answer"]
    finally:
        db.close()


def test_attention_lists_ranked_open_alerts_only():
    db = SessionLocal()
    try:
        t = _make_tenant("qa")
        def mk(title, sev, rank, state=AlertState.detected):
            db.add(Alert(tenant_id=t, severity=sev, state=state, title=title, what_happened="w", priority_rank=rank, evidence={}))
        mk("Low one", AlertSeverity.medium, 40); mk("Big one", AlertSeverity.critical, 120); mk("Closed", AlertSeverity.critical, 200, AlertState.resolved)
        db.commit()
        r = answer_question(db, t, "what needs my attention")
        assert [a["title"] for a in r["evidence"]["alerts"]] == ["Big one", "Low one"] and r["evidence"]["open"] == 2
    finally:
        db.close()


def test_forecast_projects_trend():
    db = SessionLocal()
    try:
        t = _make_tenant("qf"); c = Connector(tenant_id=t, name="POS", connector_type="webhook", is_active=True); db.add(c); db.commit()
        now = dt.datetime.utcnow()
        for i, v in enumerate([600, 500, 400, 300, 200, 100]):     # i=0 is the newest 7-day period
            db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity="sale", event_type="s", timestamp=now - dt.timedelta(days=7 * i + 3),
                                 amount=v, validation_status="validated"))
        db.commit()
        r = answer_question(db, t, "what will sales be next week")
        assert r["intent"] == "forecast" and r["evidence"]["direction"] in ("up", "down") and r["evidence"]["forecast_next"] >= 0
    finally:
        db.close()


def test_ranking_never_leaks_other_tenants():
    db = SessionLocal()
    try:
        t1, t2 = _make_tenant("ql1"), _make_tenant("ql2"); seed(db, t1)
        assert answer_question(db, t2, "top customers")["evidence"]["items"] == []
    finally:
        db.close()


def test_api_validation_auth_and_shape(client, tenant_a, tenant_b):
    _, _, tk = register(client, tenant_a); h = bearer(tk)
    r = client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": "how are sales"}, headers=h)
    assert r.status_code == 200 and {"answer", "intent", "answered", "evidence"} <= set(r.json())
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": ""}, headers=h).status_code == 422
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": "x" * 501}, headers=h).status_code == 422
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": "sales", "period_days": 0}, headers=h).status_code == 422
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": "sales", "period_days": 9999}, headers=h).status_code == 422
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}&period_days=0", headers=h).status_code == 422
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_b, "question": "sales"}, headers=h).status_code == 403
    assert client.post("/api/questions/ask", json={"tenant_id": tenant_a, "question": "sales"}).status_code == 401
