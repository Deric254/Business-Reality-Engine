"""smePro, Imara and Pharmacy ERP: the agent reads each system's real database layout, the engine keeps an exact copy,
and the link's health is stated and alerted. The agent runs for real here; only the network hop is replaced by the test client."""
import datetime as dt
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import systems as S  # noqa: E402

from app.core import systems as server
from app.core.db import SessionLocal
from app.models.agent import AgentStatus
from app.models.alerts import Alert
from app.models.core import BusinessEvent as E, Connector
from tests.test_block_c import make_owner

FIX = Path(__file__).parent / "fixtures"
PROFILES = S.load_profiles()


class Bridge:
    """The agent's engine client, wired straight into the test client."""
    def __init__(self, client, cid, token):
        self.client, self.cid, self.token = client, cid, token

    def post(self, what, body, timeout=60, attempts=4):
        r = self.client.post(f"/api/agent/{self.cid}/{what}", json=body, headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code != 200:
            raise S.EngineError(r.status_code, r.json().get("detail", ""))
        return r.json()


class Rig:
    def __init__(self, client, tmp_path, system, make_db):
        self.client, self.system, self.tmp = client, system, tmp_path
        self.tenant, self.h = make_owner(client, system)
        self.db_path = make_db(tmp_path)
        r = client.post("/api/agent/connect", json={"tenant_id": self.tenant, "system": system}, headers=self.h)
        assert r.status_code == 200, r.text
        self.code = S.decode_codes(r.json()["code"])[0]
        self.cid = self.code["connector_id"]
        self.entry = {"system": system, "connector_id": self.cid, "token": self.code["token"], "path": str(self.db_path)}
        self.cfg = {"poll_seconds": 0, "heartbeat_seconds": 0, "force_pass_seconds": 0}
        self.state = S.StateDB(str(tmp_path / "state.db"))
        self.runner = S.SystemRunner(self.cfg, self.entry, PROFILES[system], self.state, Bridge(client, self.cid, self.code["token"]), log=lambda *a: None)

    def tick(self, n=1):
        for _ in range(n):
            self.runner.last_pass_at = 0                      # a pass is due: the test is the clock
            self.runner.tick()

    def check_now(self):
        r = self.client.post(f"/api/agent/{self.cid}/check?tenant_id={self.tenant}", headers=self.h)
        assert r.status_code == 200
        self.runner.heartbeat()
        self.tick()

    def summary(self):
        rows = self.client.get(f"/api/agent/?tenant_id={self.tenant}", headers=self.h).json()
        return next(x for x in rows if x["system"] == self.system)

    def events(self, entity=None):
        db = SessionLocal()
        try:
            q = db.query(E).filter(E.connector_id == self.cid)
            return sorted((e for e in q if entity in (None, e.entity)), key=lambda e: e.entity_id)
        finally:
            db.close()

    def amounts(self, entity):
        return {e.entity_id: float(e.amount) for e in self.events(entity)}


def imara_db(tmp):
    p = tmp / "imara.db"
    con = sqlite3.connect(p)
    con.executescript((FIX / "imara_schema.sql").read_text())
    con.execute("INSERT INTO schema_migrations (id, version) VALUES ('018-fifo-cost-attribution', '18')")
    con.execute("INSERT INTO config (key, value) VALUES ('currency', 'KES')")
    con.execute("INSERT INTO invoices (id, invoice_number, invoice_date, customer_name, status, total_amount, created_by) VALUES (1, 'INV-1', '2026-10-01', 'Mama Jane', 'partial_payment', 5000, 1)")
    con.execute("INSERT INTO invoice_items (invoice_id, description, quantity, unit_price, line_total) VALUES (1, 'Binding wire', 10, 500, 5000)")
    for i, amt in ((1, 2000.0), (2, 1500.5)):
        con.execute("INSERT INTO invoice_payments (id, invoice_id, payment_date, amount, recorded_by, created_at) VALUES (?, 1, '2026-10-0' || ?, ?, 1, '2026-10-0' || ? || ' 09:00:00')", (i, i + 1, amt, i + 1))
    con.execute("INSERT INTO payments (id, payment_date, category, payee_name, amount, recorded_by) VALUES (1, '2026-10-03', 'other', 'Fuel', 800, 1)")
    con.commit()
    con.close()
    return p


def pharmacy_db(tmp):
    p = tmp / "pharmacy.db"
    con = sqlite3.connect(p)
    con.executescript((FIX / "pharmacy_schema.sql").read_text())
    con.execute("INSERT INTO alembic_version VALUES ('0043_purchase_restock_snapshot')")
    con.execute("INSERT INTO business_config (id, currency) VALUES (1, 'KES')")
    con.execute("INSERT INTO products (id, name) VALUES (1, 'Panadol')")
    con.execute("INSERT INTO sales (id, cashier_user_id, subtotal, total_amount, created_at, customer_id) VALUES (1, 1, 10000, 10000, '2026-10-20 08:30:00', NULL)")
    con.execute("INSERT INTO sale_items (id, sale_id, product_id, batch_id, quantity, unit_price, line_total, unit_cost, qty_refunded) VALUES (1, 1, 1, 1, 4, 2500, 10000, 1000, 1)")
    con.execute("INSERT INTO refunds (id, sale_id, processed_by_user_id, reason, method, total_amount, created_at) VALUES (1, 1, 1, 'DAMAGED', 'CASH', 2500, '2026-10-21 10:00:00')")
    con.execute("INSERT INTO refund_items (id, refund_id, sale_item_id, product_id, batch_id, quantity, unit_price, line_total) VALUES (1, 1, 1, 1, 1, 1, 2500, 2500)")
    con.execute("INSERT INTO sales (id, cashier_user_id, subtotal, total_amount, created_at) VALUES (2, 1, 5050, 5050, '2026-10-22 11:00:00')")
    con.commit()
    con.close()
    return p


@pytest.fixture()
def imara(client, tmp_path):
    return Rig(client, tmp_path, "imara", imara_db)


def test_connects_syncs_and_states_it(client, imara):
    imara.check_now()
    s = imara.summary()
    assert s["state"] == "connected" and s["in_sync"] and s["headline"] == "Connected and synced"
    assert imara.amounts("sale") == {"receipts:1": 2000.0, "receipts:2": 1500.5}
    assert imara.amounts("expense") == {"payments:1": 800.0}
    assert s["status"]["path"] == str(imara.db_path) and s["records"] == 3
    assert s["verify"]["match"] is True


def test_edits_and_deletes_follow_and_totals_stay_equal(client, imara):
    imara.tick()
    con = sqlite3.connect(imara.db_path)
    con.execute("UPDATE invoice_payments SET amount = 2100 WHERE id = 1")
    con.execute("DELETE FROM invoice_payments WHERE id = 2")
    con.execute("INSERT INTO invoice_payments (id, invoice_id, payment_date, amount, recorded_by, created_at) VALUES (3, 1, '2026-10-05', 300, 1, '2026-10-05 10:00:00')")
    con.commit(); con.close()
    imara.tick()
    assert imara.amounts("sale") == {"receipts:1": 2100.0, "receipts:3": 300.0}
    assert imara.summary()["verify"]["match"] is True


def test_cancelled_invoice_leaves_the_books(client, imara):
    imara.tick()
    con = sqlite3.connect(imara.db_path)
    con.execute("UPDATE invoices SET status = 'cancelled' WHERE id = 1")
    con.commit(); con.close()
    imara.tick()
    assert imara.events("sale") == [] and len(imara.events("expense")) == 1


def test_not_found_is_said_plainly(client, tmp_path):
    rig = Rig(client, tmp_path, "imara", imara_db)
    rig.db_path.unlink()
    rig.check_now()
    s = rig.summary()
    assert s["state"] == "not_found" and "not found" in s["headline"].lower() and rig.events() == []


def test_not_this_system_and_garbage_are_refused(client, tmp_path):
    rig = Rig(client, tmp_path, "pharmacy", imara_db)             # Imara's file given to the pharmacy connector
    rig.check_now()
    assert rig.summary()["state"] == "not_this_system" and rig.events() == []
    rig.db_path.write_bytes(b"not a database at all" * 100)
    rig.check_now()
    assert rig.summary()["state"] == "not_this_system"


def test_pharmacy_cents_net_of_refunds_and_utc(client, tmp_path):
    rig = Rig(client, tmp_path, "pharmacy", pharmacy_db)
    rig.tick()
    assert rig.amounts("sale") == {"sales:1": 75.0, "sales:2": 50.5}          # 100.00 less the 25.00 refunded; 5050 cents
    assert rig.amounts("refund") == {"refunds:1": 25.0}
    first = next(e for e in rig.events("sale") if e.entity_id == "sales:1")
    assert first.timestamp == dt.datetime(2026, 10, 20, 8, 30) and first.quantity == 3     # an exact UTC moment
    assert rig.summary()["verify"]["match"] is True


def test_a_later_refund_changes_the_sale_it_belongs_to(client, tmp_path):
    rig = Rig(client, tmp_path, "pharmacy", pharmacy_db)
    rig.tick()
    con = sqlite3.connect(rig.db_path)
    con.execute("INSERT INTO refunds (id, sale_id, processed_by_user_id, reason, method, total_amount, created_at) VALUES (2, 2, 1, 'OTHER', 'CASH', 1000, '2026-10-23 10:00:00')")
    con.commit(); con.close()
    rig.tick()
    assert rig.amounts("sale")["sales:2"] == 40.5 and rig.amounts("refund")["refunds:2"] == 10.0


def test_engine_copy_that_drifts_is_repaired_and_strays_removed(client, imara):
    imara.tick()
    db = SessionLocal()
    db.execute(text("DELETE FROM business_events WHERE connector_id = :c AND entity_id = 'receipts:1'"), {"c": imara.cid})
    db.commit(); db.close()
    imara.check_now()                                           # the totals no longer match: the engine says so
    imara.tick(2)                                               # and the agent resends everything once
    assert imara.amounts("sale") == {"receipts:1": 2000.0, "receipts:2": 1500.5}
    assert imara.summary()["verify"]["match"] is True
    db = SessionLocal()                                         # a record the system never had
    c = db.get(Connector, imara.cid)
    db.execute(text("INSERT INTO business_events (id, tenant_id, connector_id, source, entity, entity_id, event_type, timestamp, amount, validation_status, metadata_json) "
                    "VALUES (gen_random_uuid(), :t, :c, 'x', 'sale', 'receipts:99', 'sale', now(), 7, 'validated', '{}')"), {"t": c.tenant_id, "c": imara.cid})
    db.commit(); db.close()
    imara.runner.force_full = True
    imara.tick()
    assert "receipts:99" not in imara.amounts("sale") and imara.summary()["verify"]["match"] is True


def test_large_removal_waits_for_the_owner(client, tmp_path):
    def many(tmp):
        p = imara_db(tmp)
        con = sqlite3.connect(p)
        for i in range(10, 40):
            con.execute("INSERT INTO invoice_payments (id, invoice_id, payment_date, amount, recorded_by, created_at) VALUES (?, 1, '2026-10-06', 10, 1, '2026-10-06 10:00:00')", (i,))
        con.commit(); con.close()
        return p
    rig = Rig(client, tmp_path, "imara", many)
    rig.tick()
    assert len(rig.events("sale")) == 32
    con = sqlite3.connect(rig.db_path)
    con.execute("DELETE FROM invoice_payments WHERE id >= 10")
    con.commit(); con.close()
    rig.tick()
    assert len(rig.events("sale")) == 32 and rig.summary()["state"] == "needs_confirmation"
    r = client.post(f"/api/agent/{rig.cid}/confirm?tenant_id={rig.tenant}", headers=rig.h)
    assert r.status_code == 200
    rig.runner.heartbeat()
    rig.tick(2)
    assert len(rig.events("sale")) == 2 and rig.summary()["state"] == "connected"


def test_a_changed_layout_is_reported_never_read_as_empty(client, imara):
    imara.tick()
    con = sqlite3.connect(imara.db_path)
    con.execute("ALTER TABLE invoice_payments RENAME COLUMN amount TO paid_amount")
    con.commit(); con.close()
    imara.tick()
    s = imara.summary()
    assert s["state"] == "schema_changed" and len(imara.events("sale")) == 2         # what was there is untouched


def test_newer_version_is_a_notice_not_a_stop(client, imara):
    con = sqlite3.connect(imara.db_path)
    con.execute("INSERT INTO schema_migrations (id) VALUES ('025-something-new')")
    con.commit(); con.close()
    imara.tick()
    s = imara.summary()
    assert s["state"] == "connected" and "updated" in (s["notice"] or "")


def test_location_set_by_hand_and_cleared(client, tmp_path):
    rig = Rig(client, tmp_path, "imara", imara_db)
    rig.entry.pop("path")                                        # discovery finds nothing in this sandbox...
    rig.runner.homes = [tmp_path / "nobody"]
    rig.tick()
    assert rig.summary()["state"] == "not_found"
    r = client.post(f"/api/agent/{rig.cid}/settings?tenant_id={rig.tenant}", json={"path": str(rig.db_path)}, headers=rig.h)
    assert r.status_code == 200
    rig.runner.heartbeat()
    rig.tick(2)
    assert rig.summary()["state"] == "connected" and len(rig.events("sale")) == 2


def test_discovery_finds_the_default_places(tmp_path):
    home = tmp_path / "Users" / "shop"
    (home / ".imara").mkdir(parents=True)
    db = home / ".imara" / "imara.db"
    db.write_bytes(b"x")
    other = tmp_path / "Users" / "boss" / ".imara"
    other.mkdir(parents=True)
    (other / "imara.db").write_bytes(b"yy")
    found = S.locate(PROFILES["imara"], homes=[home, tmp_path / "Users" / "boss"])
    assert found["path"] in (db, other / "imara.db") and len(found["candidates"]) == 2
    assert S.locate(PROFILES["pharmacy"], homes=[home])["path"] is None
    assert str(S.locate(PROFILES["smepro"], homes=[home])["searched"][0]).replace("\\", "/").endswith("com.smepro.app/erp.db")


def test_systems_are_connected_independently(client, tmp_path):
    a = Rig(client, tmp_path, "imara", imara_db)
    a.tick()
    rows = client.get(f"/api/agent/?tenant_id={a.tenant}", headers=a.h).json()
    assert {r["system"]: r["connected"] for r in rows} == {"smepro": False, "imara": True, "pharmacy": False}


def test_wrong_token_and_other_tenants_get_nothing(client, imara, tmp_path):
    r = client.post(f"/api/agent/{imara.cid}/heartbeat", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 404
    assert client.post(f"/api/agent/{imara.cid}/heartbeat", json={}).status_code == 404
    assert client.get(f"/api/agent/?tenant_id={imara.tenant}").status_code == 401
    t2, h2 = make_owner(client, "intruder")
    assert client.get(f"/api/agent/?tenant_id={imara.tenant}", headers=h2).status_code in (401, 403)
    assert client.post(f"/api/agent/{imara.cid}/check?tenant_id={t2}", headers=h2).status_code == 404
    assert client.delete(f"/api/agent/{imara.cid}?tenant_id={t2}", headers=h2).status_code == 404


def test_reconnect_makes_the_old_code_useless(client, imara):
    r = client.post("/api/agent/connect", json={"tenant_id": imara.tenant, "system": "imara"}, headers=imara.h)
    assert r.json()["connector_id"] == imara.cid
    old = client.post(f"/api/agent/{imara.cid}/heartbeat", json={}, headers={"Authorization": f"Bearer {imara.code['token']}"})
    assert old.status_code == 404
    new = S.decode_codes(r.json()["code"])[0]
    assert client.post(f"/api/agent/{imara.cid}/heartbeat", json={}, headers={"Authorization": f"Bearer {new['token']}"}).status_code == 200


def test_bad_records_are_refused_whole(client, imara):
    h = {"Authorization": f"Bearer {imara.code['token']}"}
    for bad in ({"entity": "sale"}, {"entity": "payroll", "entity_id": "x:1", "event_type": "e", "timestamp": "2026-01-01T00:00:00Z"},
                {"entity": "sale", "entity_id": "x:1", "event_type": "sale", "timestamp": "not a date"}):
        assert client.post(f"/api/agent/{imara.cid}/sync", json={"events": [bad]}, headers=h).status_code == 422
    assert imara.events() == []


def test_a_down_link_alerts_and_clears_itself(client, imara):
    imara.tick()
    db = SessionLocal()
    now = dt.datetime.utcnow()
    row = db.get(AgentStatus, imara.cid)
    assert server.detect_system_links(db, imara.tenant, now) == []               # healthy: nothing
    row.last_seen_at = now - dt.timedelta(minutes=30)                            # the computer went off
    db.commit()
    raised = server.detect_system_links(db, imara.tenant, now)
    assert len(raised) == 1 and "not reaching" in raised[0].title
    assert server.summary(db, db.get(Connector, imara.cid), now)["state"] == "offline"
    db.close()
    imara.tick()                                                                 # it came back
    db = SessionLocal()
    assert server.detect_system_links(db, imara.tenant) == []
    open_ = [a for a in db.query(Alert).filter(Alert.tenant_id == imara.tenant) if a.evidence.get("dedup_key") == f"system_link:{imara.cid}"]
    assert open_ and all(a.state.value == "resolved" for a in open_)
    db.close()


def test_a_system_that_stays_unfound_alerts(client, tmp_path):
    rig = Rig(client, tmp_path, "imara", imara_db)
    rig.db_path.unlink()
    rig.tick()
    db = SessionLocal()
    row = db.get(AgentStatus, rig.cid)
    row.problem_since = dt.datetime.utcnow() - dt.timedelta(minutes=20)
    db.commit()
    assert len(server.detect_system_links(db, rig.tenant)) == 1
    db.close()


def test_silence_detector_leaves_system_links_alone(client, imara):
    from app.core.detectors import detect_data_silence
    imara.tick()
    db = SessionLocal()
    assert detect_data_silence(db, imara.tenant, dt.datetime.utcnow() + dt.timedelta(days=30)) == []
    db.close()


def test_disconnect_removes_everything(client, imara):
    imara.tick()
    assert client.delete(f"/api/agent/{imara.cid}?tenant_id={imara.tenant}", headers=imara.h).status_code == 200
    assert imara.events() == []
    assert client.get(f"/api/agent/?tenant_id={imara.tenant}", headers=imara.h).json()[1]["connected"] is False


def test_the_whole_loop_runs_threaded(client, imara):
    imara.cfg.update(poll_seconds=0.05)
    stop = threading.Event()
    cfg = {**imara.cfg, "base_url": "http://localhost", "systems": [imara.entry], "state_file": str(imara.tmp / "loop.db")}
    import unittest.mock as m
    with m.patch.object(S, "EngineClient", lambda *a, **k: Bridge(client, imara.cid, imara.code["token"])):
        t = threading.Thread(target=S.run_loop, args=(cfg, PROFILES, stop, lambda *a: None, True))
        t.start(); t.join(30); stop.set()
    assert len(imara.events("sale")) == 2


def test_code_roundtrip_and_rejection():
    code = S.decode_codes("bre1." + __import__("base64").urlsafe_b64encode(b'{"u":"https://x","c":"1","t":"t","s":"imara"}').decode().rstrip("="))[0]
    assert code["system"] == "imara"
    several = "bre2." + __import__("base64").urlsafe_b64encode(b'{"u":"https://x","e":[{"c":"1","t":"t","s":"imara"},{"c":"2","t":"u","s":"smepro"}]}').decode().rstrip("=")
    assert [d["system"] for d in S.decode_codes(several)] == ["imara", "smepro"]
    for bad in ("hello", "bre1.@@@", "bre1." + "e30"):
        with pytest.raises(ValueError):
            S.decode_codes(bad)


# ---- smePro: encrypted, several businesses, minor units by currency ---------------------------------------------------

sqlcipher3 = pytest.importorskip("sqlcipher3")
KEY = "ab" * 32


def smepro_db(tmp):
    p = tmp / "erp.db"
    con = sqlcipher3.connect(str(p))
    con.execute(f"PRAGMA key = \"x'{KEY}'\"")
    con.executescript((FIX / "smepro_schema.sql").read_text())
    con.execute("INSERT INTO _schema_version (version) VALUES (43)")
    con.execute("INSERT INTO businesses (id, name, currency, created_at, updated_at) VALUES ('b1', 'Shop A', 'KES', '2026-01-01', '2026-01-01')")
    con.execute("INSERT INTO businesses (id, name, currency, created_at, updated_at) VALUES ('b2', 'Shop B', 'UGX', '2026-01-02', '2026-01-02')")
    def sale(i, b, rev, day="2026-10-20", at="2026-10-20T07:15:00Z"):
        con.execute("INSERT INTO module_sales (id, business_id, item_name, quantity, revenue, sale_date, cost_at_sale, discount_amount, created_at, updated_at) VALUES (?, ?, 'Soap', 2, ?, ?, 100, 0, ?, ?)", (i, b, rev, day, at, at))
    sale("s1", "b1", 123456)
    sale("s2", "b1", 5000, "2026-10-19", "2026-10-21T01:00:00Z")           # back-dated entry: the day the owner recorded wins
    sale("u1", "b2", 7000)
    con.execute("INSERT INTO module_accounting (id, business_id, description, entry_type, category, amount, transaction_date, created_at, updated_at) VALUES ('a1', 'b1', 'Rent', 'expense', 'Rent', 30000, '2026-10-02', '2026-10-02T06:00:00Z', '2026-10-02T06:00:00Z')")
    con.execute("INSERT INTO module_accounting (id, business_id, description, entry_type, category, amount, transaction_date, created_at, updated_at) VALUES ('a2', 'b1', 'Purchase received — PO-1', 'expense', 'Purchasing', 9999, '2026-10-02', '2026-10-02T06:00:00Z', '2026-10-02T06:00:00Z')")
    con.commit(); con.close()
    (tmp / "erp.db.key").write_text(KEY)
    return p


def test_smepro_encrypted_scoped_and_not_double_counted(client, tmp_path):
    rig = Rig(client, tmp_path, "smepro", smepro_db)
    rig.tick()
    assert rig.summary()["state"] == "needs_choice"                    # two businesses: the owner chooses, nothing is guessed
    assert rig.events() == []
    r = client.post(f"/api/agent/{rig.cid}/settings?tenant_id={rig.tenant}", json={"scope_id": "b1"}, headers=rig.h)
    assert r.status_code == 200
    rig.runner.heartbeat()
    rig.tick(2)
    assert rig.summary()["state"] == "connected"
    assert rig.amounts("sale") == {"sales:s1": 1234.56, "sales:s2": 50.0}      # minor units of KES
    assert rig.amounts("expense") == {"bookkeeping:a1": 300.0}                  # the auto-posted purchase is not counted twice
    s2 = next(e for e in rig.events("sale") if e.entity_id == "sales:s2")
    assert s2.timestamp.date() == dt.date(2026, 10, 19) or s2.timestamp.date() == dt.date(2026, 10, 18)


def test_smepro_without_its_key_says_so(client, tmp_path):
    rig = Rig(client, tmp_path, "smepro", smepro_db)
    (tmp_path / "erp.db.key").unlink()
    rig.tick()
    assert rig.summary()["state"] == "needs_key" and rig.events() == []
