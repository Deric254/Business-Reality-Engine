"""Beyond money: stock, customers, products and what is owed are read like sales, and none of them changes a single existing figure."""
import json
import sqlite3
from datetime import date, timedelta

import app.api.agent as agent_api
from tests.test_custom_system import url
from tests.test_local_systems import Rig


def shop(tmp):
    d = tmp / "data"
    d.mkdir()
    con = sqlite3.connect(d / "shop.db")
    con.execute("CREATE TABLE sales (sale_id INTEGER PRIMARY KEY, sold_on TEXT, total REAL, customer_id TEXT, product_id TEXT, qty REAL)")
    con.execute("CREATE TABLE expenses (exp_id INTEGER PRIMARY KEY, paid_on TEXT, amount REAL, supplier TEXT)")
    con.execute("CREATE TABLE stock_levels (sku TEXT PRIMARY KEY, product_name TEXT, on_hand REAL, unit_cost REAL)")
    con.execute("CREATE TABLE debtors (debt_id INTEGER PRIMARY KEY, customer_id TEXT, amount REAL, due_on TEXT)")
    con.execute("CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT, joined_on TEXT)")
    con.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, price REAL)")
    today = date.today()
    for i in range(60):
        con.execute("INSERT INTO sales (sold_on, total, customer_id, product_id, qty) VALUES (?,?,?,?,?)",
                    ((today - timedelta(days=i % 40)).isoformat(), 100 + (i % 7) * 25, f"C{i % 9}", f"P{i % 5}", 1 + i % 3))
    for i in range(12):
        con.execute("INSERT INTO expenses (paid_on, amount, supplier) VALUES (?,?,?)", ((today - timedelta(days=i * 3)).isoformat(), 300 + i * 10, f"S{i % 3}"))
    con.executemany("INSERT INTO stock_levels VALUES (?,?,?,?)", [(f"P{i}", f"Item {i}", 10 * i + 1, 50.0) for i in range(8)])
    con.executemany("INSERT INTO debtors (customer_id, amount, due_on) VALUES (?,?,?)", [(f"C{i}", 500.0 * (i + 1), (today - timedelta(days=30 * i)).isoformat()) for i in range(4)])
    con.executemany("INSERT INTO customers VALUES (?,?,?)", [(f"C{i}", f"Customer {i}", "2025-01-01") for i in range(9)])
    con.executemany("INSERT INTO products VALUES (?,?,?)", [(f"P{i}", f"Item {i}", 120.0) for i in range(8)])
    con.execute("CREATE TABLE pad (t TEXT)")
    con.executemany("INSERT INTO pad VALUES (?)", [("x" * 200,)] * 200)
    con.commit()
    con.close()
    return d


SNAPSHOT = ("/api/business/analytics?tenant_id={t}&days=60", "/api/business/health?tenant_id={t}", "/api/business/quality?tenant_id={t}", "/api/business/trend?tenant_id={t}",
            "/api/alerts/value?tenant_id={t}", "/api/questions/kpis?tenant_id={t}")
VOLATILE = {"events", "generated_at", "computed_at", "as_of", "now", "updated_at", "last_event_at", "latest_event_at"}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in VOLATILE}
    return [strip(v) for v in o] if isinstance(o, list) else o


def look(client, r):
    out = {}
    for tpl in SNAPSHOT:
        resp = client.get(tpl.format(t=r.tenant), headers=r.h)
        out[tpl] = (resp.status_code, strip(resp.json()) if resp.headers.get("content-type", "").startswith("application/json") else None)
    return out


def confirm(client, r, path, keep):
    prop = client.post(url(r, "discovery/propose"), json={"path": path}, headers=r.h).json()
    tables = [t for t in prop["tables"] if t["table"] in keep]
    res = client.post(url(r, "discovery/confirm"), json={"path": path, "tables": tables}, headers=r.h)
    assert res.status_code == 200, res.text
    r.runner.heartbeat()
    r.tick(3)
    return prop


def test_every_kind_of_table_is_read_and_the_existing_figures_do_not_move(client, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    r = Rig(client, tmp_path, "custom", shop)
    r.runner.tick()
    path = str(tmp_path / "data" / "shop.db")
    prop = client.post(url(r, "discovery/propose"), json={"path": path}, headers=r.h).json()
    roles = {t["table"]: t["role"] for t in prop["tables"]}
    assert roles == {"sales": "sale", "expenses": "expense", "stock_levels": "stock", "debtors": "receivable", "customers": "customer", "products": "product"}

    confirm(client, r, path, {"sales", "expenses"})
    assert r.summary()["state"] == "connected"
    before = look(client, r)
    assert all(code == 200 for code, _ in before.values())

    confirm(client, r, path, set(roles))
    s = r.summary()
    assert s["state"] == "connected" and s["in_sync"], s
    kinds = {}
    for e in r.events():
        kinds[e.entity] = kinds.get(e.entity, 0) + 1
    assert kinds == {"sale": 60, "expense": 12, "stock": 8, "receivable": 4, "customer": 9, "product": 8}
    stock = {e.product_id: float(e.quantity) for e in r.events("stock")}
    assert stock["P3"] == 31.0                                           # keyed by the code the sales use, so the two can be joined
    assert next(e for e in r.events("stock") if e.product_id == "P3").metadata_json == {"label": "Item 3"}
    assert {e.customer_id for e in r.events("receivable")} == {"C0", "C1", "C2", "C3"} and sorted(float(e.amount) for e in r.events("receivable")) == [500.0, 1000.0, 1500.0, 2000.0]

    after = look(client, r)
    # "events" (the quality page's row count) is left out of the comparison on purpose: it counts the new rows too
    assert json.dumps(after, sort_keys=True, default=str) == json.dumps(before, sort_keys=True, default=str)
