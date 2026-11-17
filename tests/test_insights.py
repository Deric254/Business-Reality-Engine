"""What the data says about where money is stuck or leaking, from stock, credit and sales read through the agent."""
import sqlite3
from datetime import date, timedelta

import app.api.agent as agent_api
from app.core import insights
from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.core.detectors import run_tenant_cycle
from app.models.alerts import Alert, AlertState
from app.models.core import BusinessEvent
from tests.test_custom_all_data import confirm
from tests.test_local_systems import Rig


def business(tmp):
    d = tmp / "data"
    d.mkdir()
    con = sqlite3.connect(d / "shop.db")
    con.execute("CREATE TABLE sales (sale_id INTEGER PRIMARY KEY, sold_on TEXT, total REAL, customer_id TEXT, product_id TEXT, qty REAL)")
    con.execute("CREATE TABLE stock_levels (sku TEXT PRIMARY KEY, product_name TEXT, on_hand REAL, unit_cost REAL)")
    con.execute("CREATE TABLE debtors (debt_id INTEGER PRIMARY KEY, customer_id TEXT, amount REAL, due_on TEXT)")
    today = date.today()
    n = 0
    for day in range(0, 120):                                          # 120 days of history
        for p in ("FAST", "STEADY"):                                    # always selling
            n += 1
            con.execute("INSERT INTO sales VALUES (?,?,?,?,?,?)", (n, (today - timedelta(days=day)).isoformat(), 200.0, f"C{day % 4}", p, 2))
        if day >= 90:                                                   # sold until three months ago, then stopped
            n += 1
            con.execute("INSERT INTO sales VALUES (?,?,?,?,?,?)", (n, (today - timedelta(days=day)).isoformat(), 300.0, "C1", "STALLED", 1))
    con.executemany("INSERT INTO stock_levels VALUES (?,?,?,?)", [("FAST", "Fast mover", 3, 80.0), ("STEADY", "Steady seller", 500, 60.0), ("STALLED", "Stalled item", 40, 100.0), ("NEVER", "Never sold item", 10, 250.0)])
    con.executemany("INSERT INTO debtors (customer_id, amount, due_on) VALUES (?,?,?)",
                    [("C1", 4000.0, (today - timedelta(days=95)).isoformat()), ("C2", 1500.0, (today - timedelta(days=45)).isoformat()), ("C3", 900.0, (today - timedelta(days=5)).isoformat())])
    con.execute("CREATE TABLE pad (t TEXT)")
    con.executemany("INSERT INTO pad VALUES (?)", [("x" * 200,)] * 200)
    con.commit()
    con.close()
    return d


def connected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    r = Rig(client, tmp_path, "custom", business)
    r.runner.tick()
    confirm(client, r, str(tmp_path / "data" / "shop.db"), {"sales", "stock_levels", "debtors"})
    assert r.summary()["state"] == "connected"
    return r


def test_the_three_findings_come_from_the_numbers_in_the_records(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        found = {f["kind"]: f for f in insights.findings(db, r.tenant, utcnow())}
    finally:
        db.close()
    assert set(found) == {"dead_stock", "stock_out_risk", "overdue_credit"}

    dead = found["dead_stock"]
    assert {x["product"] for x in dead["rows"]} == {"Stalled item", "Never sold item"}
    assert dead["cost"]["amount"] == 40 * 100.0 + 10 * 250.0                     # units on hand x their cost
    assert any(x["never_sold"] for x in dead["rows"]) and "Stalled item" in dead["why"] or "Never sold item" in dead["why"]

    out = found["stock_out_risk"]
    assert [x["product"] for x in out["rows"]] == ["Fast mover"] and out["rows"][0]["days_of_stock"] < 7

    credit = found["overdue_credit"]
    assert credit["cost"]["amount"] == 5500.0                                   # the two debts older than 30 days
    assert [x["customer"] for x in credit["rows"]] == ["C1", "C2"] and credit["rows"][0]["oldest_days"] >= 94
    assert "bought again" in credit["why"]                                      # C1 and C2 still buy while old balances stand
    for f in found.values():
        assert f["fix"] and f["what"] and f["why"] and f["since"]


def test_nothing_is_claimed_without_enough_history(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        early = utcnow() - timedelta(days=118)                                   # judged as if only two days of sales were on record
        assert insights.dead_stock(db, r.tenant, early) is None
        assert insights.findings(db, "00000000-0000-0000-0000-000000000000", utcnow()) == []
    finally:
        db.close()


def test_the_screen_gets_the_findings_and_only_for_its_own_business(client, tmp_path, monkeypatch):
    from tests.test_block_c import make_owner
    r = connected(client, tmp_path, monkeypatch)
    got = client.get(f"/api/business/insights?tenant_id={r.tenant}", headers=r.h)
    assert got.status_code == 200 and {f["kind"] for f in got.json()["findings"]} == {"dead_stock", "stock_out_risk", "overdue_credit"}
    other, other_h = make_owner(client, "other")
    assert client.get(f"/api/business/insights?tenant_id={r.tenant}", headers=other_h).status_code in (401, 403, 404)
    assert client.get(f"/api/business/insights?tenant_id={other}", headers=other_h).json() == {"findings": [], "strengths": []}


def growing(tmp):
    d = tmp / "data"
    d.mkdir()
    con = sqlite3.connect(d / "shop.db")
    con.execute("CREATE TABLE sales (sale_id INTEGER PRIMARY KEY, sold_on TEXT, total REAL, customer_id TEXT, product_id TEXT, qty REAL)")
    con.execute("CREATE TABLE stock_levels (sku TEXT PRIMARY KEY, product_name TEXT, on_hand REAL, unit_cost REAL)")
    today = date.today()
    n = 0
    for day in range(0, 75):
        recent = day < 30
        for p, now_amt, before_amt in (("RISER", 400.0, 200.0), ("FLAT", 100.0, 100.0)):
            n += 1
            con.execute("INSERT INTO sales VALUES (?,?,?,?,?,?)", (n, (today - timedelta(days=day)).isoformat(), now_amt if recent else before_amt, "C1", p, 1))
    con.executemany("INSERT INTO stock_levels VALUES (?,?,?,?)", [("RISER", "Riser item", 900, 10.0), ("FLAT", "Flat item", 900, 10.0)])
    con.commit()
    con.close()
    return d


def test_what_is_going_well_is_found_from_the_same_records(client, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    r = Rig(client, tmp_path, "custom", growing)
    r.runner.tick()
    confirm(client, r, str(tmp_path / "data" / "shop.db"), {"sales", "stock_levels"})
    db = SessionLocal()
    try:
        good = {f["kind"]: f for f in insights.strengths(db, r.tenant, utcnow())}
        assert set(good) == {"sales_growth", "growing_products"}
        g = good["sales_growth"]
        assert g["severity"] == "good" and abs(g["gain"]["amount"] - 30 * 200.0) <= 400 and "Riser item" in g["why"]      # 30 days x (500 - 300) more, give or take the day on the edge of the window
        rows = good["growing_products"]["rows"]
        assert [x["product"] for x in rows] == ["Riser item"] and 80 <= rows[0]["change_pct"] <= 125           # the flat one is not listed
        assert insights.strengths(db, r.tenant, utcnow() - timedelta(days=40)) == []                        # the earlier window would not be on record
        assert insights.findings(db, r.tenant, utcnow()) == []                                              # nothing wrong here: no problem is invented to balance it
    finally:
        db.close()
    assert {f["kind"] for f in client.get(f"/api/business/insights?tenant_id={r.tenant}", headers=r.h).json()["strengths"]} == {"sales_growth", "growing_products"}


def test_problems_become_alerts_that_close_themselves_and_strengths_do_not(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        def mine():
            return {a.evidence["insight"]: a for a in db.query(Alert).filter(Alert.tenant_id == r.tenant, Alert.kind == "insight").all()}
        run_tenant_cycle(db, r.tenant, [])
        run_tenant_cycle(db, r.tenant, [])                                                                    # a second pass refreshes; it does not duplicate
        got = mine()
        assert set(got) == {"dead_stock", "stock_out_risk", "overdue_credit"} and all(a.state == AlertState.detected for a in got.values())
        assert db.query(Alert).filter(Alert.tenant_id == r.tenant, Alert.kind == "insight").count() == 3
        credit = got["overdue_credit"]
        assert credit.recommended_action.startswith("1. ") and credit.evidence["cost"]["amount"] == 5500.0

        db.query(BusinessEvent).filter(BusinessEvent.tenant_id == r.tenant, BusinessEvent.entity == "receivable").delete()
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        db.expire_all()
        got = mine()
        assert got["overdue_credit"].state == AlertState.resolved and got["overdue_credit"].resolved_at is not None      # the money was collected: it closes itself
        assert got["dead_stock"].state == AlertState.detected                                                          # what is still true stays
    finally:
        db.close()


def test_the_alert_goes_only_where_the_owner_asked(client, tmp_path, monkeypatch):
    from app.core.notifications import enqueue_deliveries
    from app.models.config import NotificationDelivery
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        run_tenant_cycle(db, r.tenant, [])
        enqueue_deliveries(db)
        assert db.query(NotificationDelivery).filter(NotificationDelivery.tenant_id == r.tenant).count() == 0        # no channel opted in: nothing is sent to anyone
        body = {"tenant_id": r.tenant, "channel_type": "email", "target": "boss@example.com", "min_severity": "medium"}
        assert client.post("/api/notifications/channels", json={**body, "filter_kinds": ["insight"]}, headers=r.h).status_code == 200
        enqueue_deliveries(db)
        assert db.query(NotificationDelivery).filter(NotificationDelivery.tenant_id == r.tenant).count() == 0        # a channel added later is not sent yesterday's news
        db.query(Alert).filter(Alert.tenant_id == r.tenant, Alert.kind == "insight").delete()
        db.commit()
        run_tenant_cycle(db, r.tenant, [])                                                                           # a finding that appears after the channel exists is sent
        enqueue_deliveries(db)
        enqueue_deliveries(db)                                                                                       # once each, however often it runs
        assert db.query(NotificationDelivery).filter(NotificationDelivery.tenant_id == r.tenant).count() == 3
    finally:
        db.close()
    assert client.post("/api/notifications/channels", json={**body, "filter_kinds": ["nonsense"]}, headers=r.h).status_code == 400


def test_history_tells_what_happened_to_each_problem(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        run_tenant_cycle(db, r.tenant, [])
        first = {e["kind"]: e for e in insights.history(db, r.tenant)}
        assert set(first) == {"dead_stock", "stock_out_risk", "overdue_credit"} and all(e["status"] == "open" and not e["came_back"] for e in first.values())
        assert first["overdue_credit"]["cost_first"] == first["overdue_credit"]["cost_last"] == 5500.0
        assert first["overdue_credit"]["began"] and first["overdue_credit"]["summary"].startswith("Still open after 0 days, costing")

        credit = db.query(Alert).filter(Alert.tenant_id == r.tenant, Alert.kind == "insight", Alert.title.like("%credit%") | Alert.title.like("%owe%") | Alert.title.like("%unpaid%")).first()
        assert credit is not None, [a.title for a in db.query(Alert).filter(Alert.tenant_id == r.tenant).all()]
        credit.state = AlertState.action_taken                                                                     # the owner chased the customers
        db.commit()
        rows = db.query(BusinessEvent).filter(BusinessEvent.tenant_id == r.tenant, BusinessEvent.entity == "receivable").all()
        saved = [{c.name: getattr(e, c.name) for c in BusinessEvent.__table__.columns} for e in rows]
        db.query(BusinessEvent).filter(BusinessEvent.tenant_id == r.tenant, BusinessEvent.entity == "receivable").delete()
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        now = {e["kind"]: e for e in insights.history(db, r.tenant)}
        assert now["overdue_credit"]["status"] == "cleared" and now["overdue_credit"]["marked"] == "action_taken" and now["overdue_credit"]["ended"]
        assert "it was costing" in now["overdue_credit"]["summary"] and "action taken" in now["overdue_credit"]["summary"]
        assert now["dead_stock"]["status"] == "open"                                                               # what did not clear is not called fixed

        for d in saved:
            db.add(BusinessEvent(**d))
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        again = [e for e in insights.history(db, r.tenant) if e["kind"] == "overdue_credit"]
        assert [e["status"] for e in again] == ["open", "cleared"]                                                  # newest first
        assert again[0]["came_back"] is True and "came back" in again[0]["summary"] and again[1]["came_back"] is False
    finally:
        db.close()
    api = client.get(f"/api/business/insights/history?tenant_id={r.tenant}", headers=r.h)
    assert api.status_code == 200 and len(api.json()["episodes"]) == 4
    assert client.get(f"/api/business/insights/history?tenant_id={r.tenant}").status_code in (401, 403)


def test_history_is_empty_before_anything_was_raised(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    assert client.get(f"/api/business/insights/history?tenant_id={r.tenant}", headers=r.h).json() == {"episodes": []}


def test_history_keeps_the_first_and_the_highest_cost_while_it_changes(client, tmp_path, monkeypatch):
    r = connected(client, tmp_path, monkeypatch)
    db = SessionLocal()
    try:
        run_tenant_cycle(db, r.tenant, [])
        rows = db.query(BusinessEvent).filter(BusinessEvent.tenant_id == r.tenant, BusinessEvent.entity == "receivable").all()
        extra = [{c.name: getattr(e, c.name) for c in BusinessEvent.__table__.columns} for e in rows]
        def credit():
            return next(e for e in insights.history(db, r.tenant) if e["kind"] == "overdue_credit")
        assert credit()["cost_first"] == 5500.0
        added = []
        for d in extra:
            d.pop("id")
            e = BusinessEvent(**{**d, "entity_id": f"{d['entity_id']}-copy"})
            db.add(e); added.append(e)
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        grown = credit()
        assert grown["cost_first"] == 5500.0 and grown["cost_last"] > 5500.0 and grown["cost_peak"] == grown["cost_last"]
        for e in added:
            db.delete(e)
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        shrunk = credit()
        assert shrunk["cost_last"] == 5500.0 and shrunk["cost_peak"] == grown["cost_peak"] and shrunk["status"] == "open"     # the high point is not forgotten
    finally:
        db.close()


def test_a_problem_that_doubles_is_reported_again_once(client, tmp_path, monkeypatch):
    from app.core.notifications import build_email, enqueue_deliveries
    from app.models.config import NotificationDelivery
    r = connected(client, tmp_path, monkeypatch)
    body = {"tenant_id": r.tenant, "channel_type": "email", "target": "boss@example.com", "min_severity": "medium"}
    assert client.post("/api/notifications/channels", json=body, headers=r.h).status_code == 200
    db = SessionLocal()
    try:
        def sent(kind):
            return (db.query(NotificationDelivery).join(Alert, Alert.id == NotificationDelivery.alert_id)
                    .filter(NotificationDelivery.tenant_id == r.tenant, NotificationDelivery.kind == kind, Alert.kind == "insight").count())     # other detectors' alerts are not what is under test
        run_tenant_cycle(db, r.tenant, [])
        enqueue_deliveries(db)
        assert sent("alert") == 3 and sent("worsened") == 0
        rows = db.query(BusinessEvent).filter(BusinessEvent.tenant_id == r.tenant, BusinessEvent.entity == "receivable").all()
        copies = [{c.name: getattr(e, c.name) for c in BusinessEvent.__table__.columns} for e in rows]
        for d in copies:
            d.pop("id")
            db.add(BusinessEvent(**{**d, "entity_id": f"{d['entity_id']}-copy"}))
        db.commit()
        run_tenant_cycle(db, r.tenant, [])
        enqueue_deliveries(db)
        enqueue_deliveries(db)
        assert sent("worsened") == 1 and sent("alert") == 3                                                       # one problem, one more message, however often it runs
        delivery = db.query(NotificationDelivery).filter(NotificationDelivery.tenant_id == r.tenant, NotificationDelivery.kind == "worsened").one()
        alert = db.get(Alert, delivery.alert_id)
        mail = build_email(alert, "boss@example.com", "worsened")
        assert mail["Subject"].startswith("[GOT WORSE ") and "grown since it was first reported" in mail.get_content() and "11,000" in mail.get_content()
        assert build_email(alert, "boss@example.com")["Subject"].startswith("[MEDIUM]") or build_email(alert, "boss@example.com")["Subject"].startswith("[HIGH]")
        run_tenant_cycle(db, r.tenant, [])
        enqueue_deliveries(db)
        assert sent("worsened") == 1                                                                              # staying bad is not news
    finally:
        db.close()


def test_outside_reading_is_a_fixed_generic_search_with_its_source_and_day(client, tmp_path, monkeypatch):
    from app.reasoning import web_search
    r = connected(client, tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(insights, "_cache", {})
    monkeypatch.setattr(web_search, "is_configured", lambda: True)
    monkeypatch.setattr(web_search, "search", lambda q: (asked.append(q), [{"title": "Clearing slow stock", "url": "https://www.example.org/a", "snippet": "Mark it down early."}] * 5)[1])
    got = client.get(f"/api/business/insights/references?tenant_id={r.tenant}", headers=r.h).json()["references"]
    assert set(got) == {"dead_stock", "stock_out_risk", "overdue_credit"}
    page = got["dead_stock"][0]
    assert len(got["dead_stock"]) == 3 and page["source"] == "example.org" and page["fetched"] == utcnow().date().isoformat() and page["url"] == "https://www.example.org/a"
    assert sorted(asked) == sorted(insights.REFERENCE_QUERIES[k] for k in got)                       # only the fixed sentences were searched
    assert not any(w in q.lower() for q in asked for w in ("c1", "stalled item", "fast mover", "5500", "kes"))      # nothing of this business is in them
    client.get(f"/api/business/insights/references?tenant_id={r.tenant}", headers=r.h)
    assert len(asked) == 3                                                                          # asked once a day, not at every visit


def test_outside_reading_is_off_when_web_search_is_not_set_up(client, tmp_path, monkeypatch):
    from app.reasoning import web_search
    r = connected(client, tmp_path, monkeypatch)
    monkeypatch.setattr(insights, "_cache", {})
    monkeypatch.setattr(web_search, "is_configured", lambda: False)
    boom = []
    monkeypatch.setattr(web_search, "search", lambda q: boom.append(q) or [])
    assert client.get(f"/api/business/insights/references?tenant_id={r.tenant}", headers=r.h).json() == {"references": {}} and boom == []
    assert client.get(f"/api/business/insights/references?tenant_id={r.tenant}").status_code in (401, 403)
