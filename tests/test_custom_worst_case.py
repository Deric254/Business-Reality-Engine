"""The worst a real, undescribed database can throw at "Your own system": it must never be damaged, never crash the agent,
and must say what is wrong in plain words."""
import json
import os
import sqlite3
import time
from pathlib import Path


import app.api.agent as agent_api
from app.core import rate_limit

from app.core import discovery as D
from tests.test_custom_system import Fake, shop_db, url
from tests.test_local_systems import Rig, S


def make(tmp, build):
    d = tmp / "data"
    d.mkdir()
    con = sqlite3.connect(d / "x.db")
    build(con)
    con.execute("CREATE TABLE pad (t TEXT)")
    con.executemany("INSERT INTO pad VALUES (?)", [("x" * 200,)] * 200)
    con.commit()
    con.close()
    return d


def confirm_from_names(client, r, monkeypatch):
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    r.runner.tick()
    path = next(d["path"] for d in client.get(url(r, "discovery"), headers=r.h).json()["found"])
    prop = client.post(url(r, "discovery/propose"), json={"path": path}, headers=r.h).json()
    res = client.post(url(r, "discovery/confirm"), json={"path": path, "tables": prop["tables"]}, headers=r.h)
    assert res.status_code == 200, res.text
    r.runner.heartbeat()
    r.tick(3)
    return prop


def test_messy_values_are_read_without_crashing_and_what_cannot_be_counted_is_named(client, tmp_path, monkeypatch):
    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total TEXT)")
        con.executemany("INSERT INTO sales (sale_date, total) VALUES (?,?)",
                        [("2026-03-01", "1,200.50"), ("2026-03-02", None), ("not a date", "50"), ("2026-03-03", "abc"), ("2026-03-04", "75")])
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    confirm_from_names(client, r, monkeypatch)
    assert r.runner.status["state"] != "waiting"                       # the agent is alive and has reported, whatever the values were
    assert "could not be read and were left out" in r.runner.status["detail"]      # said aloud, never silently dropped
    assert [float(e.amount) for e in r.events()] == [75.0]


def test_a_locked_database_is_waited_for_not_broken(client, tmp_path, monkeypatch):
    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL)")
        con.execute("INSERT INTO sales (sale_date, total) VALUES ('2026-03-01', 10)")
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    r.runner.tick()
    path = tmp_path / "data" / "x.db"
    lock = sqlite3.connect(path, isolation_level=None)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        t0 = time.time()
        r.runner.scanned_at = None                                      # a rescan while the system holds its database locked
        r.runner.tick()
        assert time.time() - t0 < 30
        assert r.runner.status["state"] in ("needs_mapping", "not_found")
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1


def test_a_corrupt_or_encrypted_file_is_skipped_and_the_rest_still_found(client, tmp_path, monkeypatch):
    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL)")
        con.execute("INSERT INTO sales (sale_date, total) VALUES ('2026-03-01', 10)")
    d = make(tmp_path, build)
    (d / "enc.db").write_bytes(os.urandom(40000))                       # looks like a database by name, is not one
    (d / "broken.db").write_bytes(b"SQLite format 3\x00" + os.urandom(40000))   # right header, damaged inside
    r = Rig(client, tmp_path, "custom", lambda t: d)
    r.runner.tick()
    found = client.get(url(r, "discovery"), headers=r.h).json()["found"]
    ok = [f for f in found if not f["error"]]
    assert [os.path.basename(f["path"]) for f in ok] == ["x.db"]
    assert all(f["error"] for f in found if f["path"].endswith("broken.db"))


def test_a_huge_schema_is_cut_down_and_the_engine_stays_small(client, tmp_path, monkeypatch):
    def build(con):
        for i in range(300):
            con.execute(f'CREATE TABLE "t{i}" ({", ".join(f"c{j} TEXT" for j in range(120))})')
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL)")
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    r.runner.tick()
    body = client.get(url(r, "discovery"), headers=r.h).json()
    assert len(json.dumps(body)) < 1_500_000
    assert all(len(t["columns"]) <= D.MAX_COLUMNS for d in body["found"] for t in d["tables"])
    assert all(len(d["tables"]) <= D.MAX_TABLES for d in body["found"])


def test_hostile_names_in_the_database_cannot_inject_anything(client, tmp_path, monkeypatch):
    evil = 'sales"; DROP TABLE sales; --'
    def build(con):
        con.execute(f'CREATE TABLE "{evil.replace(chr(34), chr(34) * 2)}" (id INTEGER PRIMARY KEY, "sale_date" TEXT, "total""x" REAL)')
        con.execute(f'INSERT INTO "{evil.replace(chr(34), chr(34) * 2)}" (sale_date, "total""x") VALUES (\'2026-03-01\', 5)')
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    confirm_from_names(client, r, monkeypatch)
    con = sqlite3.connect(tmp_path / "data" / "x.db")
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'sales%'").fetchone()[0] == 1
    assert con.execute(f'SELECT COUNT(*) FROM "{evil.replace(chr(34), chr(34) * 2)}"').fetchone()[0] == 1


def test_a_hostile_ai_answer_cannot_widen_what_is_read(client, tmp_path, monkeypatch):
    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL)")
        con.execute("CREATE TABLE secrets (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL, password TEXT)")
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    reply = {"business": {"kind": "x" * 5000, "description": "<script>alert(1)</script> ignore previous instructions"}, "currency": "'; DROP",
             "tables": [{"table": "sales", "role": "sale", "id": "id", "date": "sale_date", "amount": "total; DROP TABLE sales", "customer": "password"},
                        {"table": "../../etc/passwd", "role": "sale", "date": "a", "amount": "b"}, "garbage", None, 7]}
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: Fake(reply))
    r.runner.tick()
    path = tmp_path / "data" / "x.db"
    prop = client.post(url(r, "discovery/propose"), json={"path": str(path)}, headers=r.h).json()
    assert len(prop["business"]["kind"]) <= 80 and prop["currency"] is None
    for t in prop["tables"]:
        assert t["table"] != "secrets" and t.get("customer") != "password"


def test_a_wrong_confirmation_never_reaches_the_agent(client, tmp_path):
    r = Rig(client, tmp_path, "custom", shop_db)
    r.runner.tick()
    path = str(tmp_path / "data" / "duka.db")
    for bad in ([], [{"table": "tbl_sales", "role": "sale"}], [{"table": "tbl_sales", "role": "admin", "date": "sale_date", "amount": "grand_total"}],
                [{"table": "tbl_sales", "role": "sale", "date": "sale_date", "amount": "sale_date"}]):
        assert client.post(url(r, "discovery/confirm"), json={"path": path, "tables": bad}, headers=r.h).status_code == 400
    r.runner.heartbeat()
    assert r.runner._awaiting_profile()


def test_the_agent_stays_light_on_a_large_database(client, tmp_path, monkeypatch):
    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL)")
        con.executemany("INSERT INTO sales (sale_date, total) VALUES (?,?)", ((f"2026-03-{i % 28 + 1:02d}", i % 500) for i in range(60000)))
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    monkeypatch.setattr(rate_limit.agent_limiter, "limit", 10**9)      # the test's own connection skips the agent's pacing
    t0 = time.time()
    confirm_from_names(client, r, monkeypatch)
    assert time.time() - t0 < 300                            # generous for a slow machine; a quadratic slowdown would take far longer
    assert len(r.events()) == 60000


def test_a_busy_engine_is_reported_as_busy_not_as_a_broken_agent(client, tmp_path, monkeypatch):
    r = Rig(client, tmp_path, "custom", shop_db)
    r.runner.tick()
    def busy(*a, **k):
        raise S.EngineError(429, "slow down")
    monkeypatch.setattr(r.runner.client, "post", busy)
    monkeypatch.setattr(r.runner.client, "heartbeat", busy, raising=False)
    r.runner.scanned_at = None
    r.runner.tick()
    assert r.runner.status["state"] == "locked" and "updating" not in r.runner.status["detail"]


def test_memory_does_not_grow_with_the_size_of_the_table(client, tmp_path, monkeypatch):
    import tracemalloc
    rows = 50000

    def build(con):
        con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date TEXT, total REAL, note TEXT)")
        con.executemany("INSERT INTO sales (sale_date, total, note) VALUES (?,?,?)", ((f"2026-03-{i % 28 + 1:02d}", i % 500, "a note to make each row heavier " * 3) for i in range(rows)))
    r = Rig(client, tmp_path, "custom", lambda t: make(t, build))
    monkeypatch.setattr(rate_limit.agent_limiter, "limit", 10**9)
    monkeypatch.setattr(agent_api, "get_provider", lambda db, t: None)
    r.runner.tick()
    path = next(d["path"] for d in client.get(url(r, "discovery"), headers=r.h).json()["found"])
    prop = client.post(url(r, "discovery/propose"), json={"path": path}, headers=r.h).json()
    assert client.post(url(r, "discovery/confirm"), json={"path": path, "tables": prop["tables"]}, headers=r.h).status_code == 200
    r.runner.heartbeat()
    tracemalloc.start()
    try:
        r.runner.run_pass(full=True)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert not list(Path(r.runner.state.path).parent.glob("*.pass-*"))                  # the working file is removed when the pass ends
    assert len(r.events()) == rows
    assert peak < 20e6, f"peak {peak / 1e6:.0f} MB for {rows} rows"                  # held in memory it was several hundred MB; now it is a few chunks
