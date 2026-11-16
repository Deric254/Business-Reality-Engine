"""'Your own system' on a database server (PostgreSQL, MariaDB/MySQL): found through a connection kept on the agent's computer,
described by names, confirmed by the owner, then synced and verified like a file database. Needs the servers of test_dbconn.py."""

import pytest

import app.api.agent as agent_api
from tests.test_dbconn import D
from tests.test_custom_system import url
from tests.test_dbconn import LIVE, spec
from tests.test_local_systems import Rig, S

pytestmark = pytest.mark.skipif(not LIVE, reason="no database server configured")


def make_server_data(kind):
    admin = D.connect(spec(kind, *LIVE[kind]["admin"]), readonly=False)
    q = D.DIALECTS[kind].quote
    for t in ("tbl_sales", "supplier_bills", "staff_pw"):
        admin.execute(f"DROP TABLE IF EXISTS {q(t)}")
    admin.execute(f"CREATE TABLE {q('tbl_sales')} (sale_no INTEGER PRIMARY KEY, sale_date DATE, grand_total NUMERIC(12,2), client VARCHAR(50))")
    admin.execute(f"CREATE TABLE {q('supplier_bills')} (bill_id INTEGER PRIMARY KEY, paid_on DATE, amount NUMERIC(12,2))")
    admin.execute(f"CREATE TABLE {q('staff_pw')} (id INTEGER PRIMARY KEY, name VARCHAR(50), password VARCHAR(50))")
    admin.execute(f"INSERT INTO {q('tbl_sales')} VALUES (1, '2026-03-01', 1200.50, 'Wanjiru'), (2, '2026-03-02', 300, 'Otieno'), (10, '2026-03-05', 49.99, NULL)")
    admin.execute(f"INSERT INTO {q('supplier_bills')} VALUES (1, '2026-03-01', 800), (2, '2026-03-03', 150.25)")
    admin.execute(f"INSERT INTO {q('staff_pw')} VALUES (1, 'Ann', 'hunter2')")
    if kind == "postgres":
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader")
        admin.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO writer")
    admin.close()


@pytest.fixture(params=sorted(LIVE))
def rig(request, client, tmp_path):
    kind = request.param
    make_server_data(kind)
    r = Rig(client, tmp_path, "custom", lambda t: t / "empty")
    r.cfg.update(state_file=str(tmp_path / "state.db"), scan_roots=[str(tmp_path / "nowhere")])
    r.entry.pop("path")
    D.save_connections(tmp_path, {"shop": {**spec(kind, "reader", "pw1")}})
    r.kind = kind
    return r


def test_the_server_is_found_described_confirmed_and_synced(client, rig, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    rig.runner.tick()
    found = client.get(url(rig, "discovery"), headers=rig.h).json()["found"]
    assert len(found) == 1 and found[0]["source"] == {"kind": rig.kind, "name": "shop"} and not found[0]["error"]
    assert "Wanjiru" not in str(found) and "hunter2" not in str(found)
    path = found[0]["path"]
    prop = client.post(url(rig, "discovery/propose"), json={"path": path}, headers=rig.h).json()
    roles = {t["table"].split(".")[-1]: t["role"] for t in prop["tables"]}
    assert roles == {"tbl_sales": "sale", "supplier_bills": "expense"}
    r = client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": prop["tables"], "business": {"kind": "Shop"}}, headers=rig.h)
    assert r.status_code == 200, r.text
    rig.runner.heartbeat()
    rig.tick(3)
    s = rig.summary()
    assert s["state"] == "connected" and s["in_sync"], s
    assert sum(rig.amounts("sale").values()) == pytest.approx(1550.49) and sum(rig.amounts("expense").values()) == pytest.approx(950.25)
    assert len(rig.events("sale")) == 3


def test_a_change_in_the_server_reaches_the_app_and_stays_equal(client, rig, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    rig.runner.tick()
    path = client.get(url(rig, "discovery"), headers=rig.h).json()["found"][0]["path"]
    prop = client.post(url(rig, "discovery/propose"), json={"path": path}, headers=rig.h).json()
    client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": prop["tables"]}, headers=rig.h)
    rig.runner.heartbeat()
    rig.tick(2)
    admin = D.connect(spec(rig.kind, *LIVE[rig.kind]["admin"]), readonly=False)
    admin.execute(f"INSERT INTO {D.DIALECTS[rig.kind].quote('tbl_sales')} VALUES (11, '2026-03-06', 100, 'New')")
    admin.execute(f"DELETE FROM {D.DIALECTS[rig.kind].quote('tbl_sales')} WHERE sale_no = 2")
    admin.close()
    rig.tick(2)
    assert sorted(rig.amounts("sale").values()) == pytest.approx([49.99, 100.0, 1200.5]) and rig.summary()["in_sync"]


def test_a_login_that_can_change_data_is_said_so_and_a_wrong_password_is_named(client, rig, tmp_path, monkeypatch):
    D.save_connections(tmp_path, {"shop": {**spec(rig.kind, "writer", "pw2")}})
    rig.runner.tick()
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    path = client.get(url(rig, "discovery"), headers=rig.h).json()["found"][0]["path"]
    prop = client.post(url(rig, "discovery/propose"), json={"path": path}, headers=rig.h).json()
    res = client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": prop["tables"]}, headers=rig.h)
    assert res.status_code == 200, (res.text, prop)
    rig.runner.heartbeat()
    rig.tick(2)
    assert "allowed to change data" in (rig.summary().get("notice") or rig.runner.status.get("notice") or "")
    D.save_connections(tmp_path, {"shop": {**spec(rig.kind, "reader", "WRONG")}})
    rig.tick(1)
    assert rig.runner.status["state"] == "needs_key" and "user name or password" in rig.runner.status["detail"]


def test_a_second_statement_hidden_in_a_reading_is_refused():
    p = {"system": "custom", "label": "x", "profile_version": 1, "discovery": {"files": []}, "signature": [], "money": {"unit": "major", "places": 2},
         "streams": [{"key": "k", "entity": "sale", "event_type": "sale.recorded", "table": "t", "time": "date", "sql": "SELECT 1; DROP TABLE t"}]}
    with pytest.raises(ValueError, match="one statement"):
        S.validate_profile(p)
    ok = {**p, "streams": [{**p["streams"][0], "sql": "SELECT 'a;b' AS id, \"x;y\" AS date FROM t;"}]}
    S.validate_profile(ok)
