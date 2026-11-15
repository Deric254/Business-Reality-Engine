"""'Your own system': a database nobody described is found by the agent, read by the engine's proposal, confirmed by the owner,
and then synced and verified like any built-in system. The agent and the engine run for real; only the network hop is the test client."""
import json
import sqlite3

import pytest

import app.api.agent as agent_api
from tests.test_local_systems import Bridge, Rig, S, PROFILES, SessionLocal, AgentStatus


class Fake:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply

    def generate_json(self, system_prompt, user_content, max_tokens=400):
        return json.dumps(self.reply)


def shop_db(tmp):
    d = tmp / "data"
    d.mkdir()
    p = d / "duka.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tbl_sales (sale_no INTEGER PRIMARY KEY, sale_date TEXT, grand_total REAL, client TEXT)")
    con.execute("CREATE TABLE supplier_bills (bill_id INTEGER PRIMARY KEY, paid_on TEXT, amount REAL)")
    con.execute("CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO tbl_sales (sale_date, grand_total, client) VALUES (?,?,?)", [("2026-03-01", 1200.5, "Wanjiru"), ("2026-03-02", 300, "Otieno")])
    con.executemany("INSERT INTO supplier_bills (paid_on, amount) VALUES (?,?)", [("2026-03-01", 800), ("2026-03-03", 150.25)])
    con.executemany("INSERT INTO staff (name) VALUES (?)", [("Ann" * 30,)] * 800)
    con.commit()
    con.close()
    return d                                                   # the agent is pointed at the folder: it finds the database itself


@pytest.fixture()
def rig(client, tmp_path):
    return Rig(client, tmp_path, "custom", shop_db)


def url(r, tail):
    return f"/api/agent/{r.cid}/{tail}?tenant_id={r.tenant}"


def tables(**over):
    base = [{"table": "tbl_sales", "role": "sale", "id": "sale_no", "date": "sale_date", "amount": "grand_total", "customer": "client", "reason": "Each row is one sale."},
            {"table": "supplier_bills", "role": "expense", "id": "bill_id", "date": "paid_on", "amount": "amount", "reason": "Bills paid."}]
    return [{**t, **over.get(t["table"], {})} for t in base]


def test_the_agent_finds_the_database_and_says_it_is_waiting_for_the_owner(client, rig):
    rig.runner.tick()
    s = rig.summary()
    assert s["state"] == "needs_mapping" and "review" in s["detail"]
    found = client.get(url(rig, "discovery"), headers=rig.h).json()["found"]
    assert [d["path"] for d in found] == [str(rig.db_path / "duka.db")]
    assert {t["name"] for t in found[0]["tables"]} == {"tbl_sales", "supplier_bills", "staff"}
    assert "Wanjiru" not in json.dumps(found)                  # names and counts only: no row values leave the computer


def test_without_an_ai_the_names_alone_give_a_proposal_that_confirms_and_syncs(client, rig, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    rig.runner.tick()
    path = str(rig.db_path / "duka.db")
    prop = client.post(url(rig, "discovery/propose"), json={"path": path}, headers=rig.h).json()
    assert prop["used_ai"] is False and {t["table"] for t in prop["tables"]} == {"tbl_sales", "supplier_bills"}
    r = client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": prop["tables"], "business": {"kind": "Shop"}}, headers=rig.h)
    assert r.status_code == 200, r.text
    rig.runner.heartbeat()                                     # the confirmed reading arrives with the next answer
    rig.tick(2)
    s = rig.summary()
    assert s["state"] == "connected" and s["in_sync"]
    assert rig.amounts("sale") == {"sale_tbl_sales:1": 1200.5, "sale_tbl_sales:2": 300.0}
    assert sum(rig.amounts("expense").values()) == 950.25


def test_the_ai_reading_is_shown_and_used_when_there_is_one(client, rig, monkeypatch):
    reply = {"business": {"kind": "Hardware shop", "description": "Sells goods and pays suppliers."}, "currency": "KES",
             "tables": [{"table": "tbl_sales", "role": "sale", "id": "sale_no", "date": "sale_date", "amount": "grand_total", "reason": "One sale per row."}]}
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: Fake(reply))
    rig.runner.tick()
    prop = client.post(url(rig, "discovery/propose"), json={"path": str(rig.db_path / "duka.db")}, headers=rig.h).json()
    assert prop["used_ai"] and prop["business"]["kind"] == "Hardware shop" and [t["table"] for t in prop["tables"]] == ["tbl_sales"]
    assert client.get(url(rig, "discovery"), headers=rig.h).json()["proposals"][str(rig.db_path / "duka.db")]["business"]["kind"] == "Hardware shop"


def test_a_reading_naming_things_that_are_not_there_is_refused(client, rig):
    rig.runner.tick()
    path = str(rig.db_path / "duka.db")
    bad = client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": [{"table": "ghost", "role": "sale", "date": "d", "amount": "a"}]}, headers=rig.h)
    assert bad.status_code == 400
    bad = client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": tables(tbl_sales={"amount": "no_such_column"})[:1]}, headers=rig.h)
    assert bad.status_code == 400
    other = client.post(url(rig, "discovery/confirm"), json={"path": "/etc/passwd", "tables": tables()}, headers=rig.h)
    assert other.status_code == 404                            # only a database the agent actually described can be chosen
    assert rig.events() == []


def test_a_restarted_agent_gets_the_confirmed_reading_back_without_asking_again(client, rig, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    rig.runner.tick()
    path = str(rig.db_path / "duka.db")
    prop = client.post(url(rig, "discovery/propose"), json={"path": path}, headers=rig.h).json()
    client.post(url(rig, "discovery/confirm"), json={"path": path, "tables": prop["tables"]}, headers=rig.h)
    fresh = S.SystemRunner(rig.cfg, {k: v for k, v in rig.entry.items() if k != "path"}, PROFILES["custom"], S.StateDB(str(rig.tmp / "s2.db")),
                           Bridge(client, rig.cid, rig.code["token"]), log=lambda *a: None)
    rig.runner = fresh
    rig.tick(3)
    assert rig.summary()["state"] == "connected" and len(rig.events()) == 4


def test_a_rescan_is_done_once_and_cleared(client, rig):
    rig.runner.tick()
    assert client.post(url(rig, "discovery/scan"), headers=rig.h).json()["ok"]
    assert client.get(url(rig, "discovery"), headers=rig.h).json()["scan_requested"] is True
    rig.runner.heartbeat()
    rig.runner.tick()
    assert client.get(url(rig, "discovery"), headers=rig.h).json()["scan_requested"] is False


def test_a_damaged_reading_sent_to_the_agent_is_refused_and_nothing_is_read(client, rig):
    rig.runner.tick()
    db = SessionLocal()
    try:
        row = db.get(AgentStatus, rig.cid)
        bad = {"system": "custom", "label": "x", "profile_version": 1, "discovery": {"files": [str(rig.db_path / "duka.db")]}, "signature": [], "money": {"unit": "major", "places": 2},
               "streams": [{"key": "k", "entity": "sale", "event_type": "sale.recorded", "table": "t", "time": "date", "sql": "DELETE FROM tbl_sales"}]}
        row.commands = {"profile": bad}
        db.commit()
    finally:
        db.close()
    rig.runner.heartbeat()
    rig.tick(2)
    assert rig.runner._awaiting_profile() and rig.events() == []
    assert sqlite3.connect(rig.db_path / "duka.db").execute("SELECT COUNT(*) FROM tbl_sales").fetchone()[0] == 2


def test_nothing_found_is_stated_with_what_to_do(client, tmp_path):
    empty = Rig(client, tmp_path, "custom", lambda t: (t / "none").mkdir() or (t / "none"))
    empty.runner.tick()
    s = empty.summary()
    assert s["state"] == "not_found" and "Set the folder" in s["detail"]


def test_only_the_owner_can_confirm_and_other_businesses_cannot_see_it(client, rig):
    rig.runner.tick()
    other_tenant, other_h = __import__("tests.test_block_c", fromlist=["make_owner"]).make_owner(client, "custom")
    assert client.get(f"/api/agent/{rig.cid}/discovery?tenant_id={other_tenant}", headers=other_h).status_code == 404
    assert client.post(f"/api/agent/{rig.cid}/discovery/confirm?tenant_id={other_tenant}", json={"path": "x", "tables": []}, headers=other_h).status_code == 404
