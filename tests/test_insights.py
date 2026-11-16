"""What the data says about where money is stuck or leaking, from stock, credit and sales read through the agent."""
import sqlite3
from datetime import date, timedelta

import app.api.agent as agent_api
from app.core import insights
from app.core.clock import utcnow
from app.core.db import SessionLocal
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
    assert [x["customer"] for x in credit["rows"]] == ["C1", "C2"] and credit["rows"][0]["oldest_days"] >= 95
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
    assert client.get(f"/api/business/insights?tenant_id={other}", headers=other_h).json() == {"findings": []}
