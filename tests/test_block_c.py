"""Block C: parsers, database drivers, shared folder, scheduled sync, connector management, local agent, manual capture."""
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.connectors import database_connector as dbc
from app.connectors import parsers
from app.connectors.file_connector import ColumnMapping, dataframe_to_normalized_events, parse_file_to_dataframe, to_number, validate_upload
from app.core import sync as sync_mod
from app.core.db import SessionLocal, engine
from app.models.core import BusinessEvent, Connector
from tests.conftest import _make_tenant, bearer, register

_spec = importlib.util.spec_from_file_location("reality_agent", Path(__file__).resolve().parents[1] / "agent" / "reality_agent.py")
agent = importlib.util.module_from_spec(_spec); sys.modules["reality_agent"] = agent; _spec.loader.exec_module(agent)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("CONNECTOR_FILE_ROOT", str(tmp_path))
    return tmp_path


def make_owner(client, name="c"):
    t = _make_tenant(name)
    _, _, tok = register(client, t, "owner")
    return t, bearer(tok)


def mk(client, t, h, **kw):
    body = {"tenant_id": t, "name": kw.pop("name", "src"), "connector_type": kw.pop("connector_type"), "config": kw.pop("config", {}), **kw}
    return client.post("/api/connectors/", json=body, headers=h)


MAP = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "ts", "amount_col": "amt", "entity_id_col": "id"}


def events_for(db, cid):
    return db.query(BusinessEvent).filter(BusinessEvent.connector_id == cid).all()


# ---------------- parsers ----------------

@pytest.mark.parametrize("doc,n", [
    (b'[{"a":1},{"a":2}]', 2), (b'{"data":[{"a":1}]}', 1), (b'{"rows":[{"a":1},{"a":2},{"a":3}]}', 3),
    (b'{"a":1}', 1), (b'{"a":1}\n{"a":2}\n\n{"a":3}\n', 3), (b'\xef\xbb\xbf[{"a":1}]', 1),
])
def test_json_shapes(doc, n):
    assert len(parsers.parse_json(doc)) == n


def test_json_flattens_nested_and_keeps_deep_values_as_text():
    df = parsers.parse_json(b'[{"id":1,"customer":{"id":"C1","geo":{"city":"Meru"}},"tags":["a","b"]}]')
    assert df.loc[0, "customer.id"] == "C1" and df.loc[0, "customer.geo.city"] == "Meru"
    assert df.loc[0, "tags"] == '["a", "b"]'


@pytest.mark.parametrize("doc", [b"", b"not json", b"[1,2,3]", b'[{"a":1}, 5]', b"[]", b'\xff\xfe'])
def test_json_rejects_bad(doc):
    with pytest.raises(HTTPException) as e:
        parsers.parse_json(doc)
    assert e.value.status_code == 400


def test_json_row_cap(monkeypatch):
    monkeypatch.setattr(parsers, "MAX_ROWS", 3)
    with pytest.raises(HTTPException) as e:
        parsers.parse_json(json.dumps([{"a": i} for i in range(4)]).encode())
    assert e.value.status_code == 413


def test_xml_rows_attributes_and_nested():
    xml = b"""<?xml version="1.0"?><sales>
      <sale id="1"><amount>10.5</amount><customer><code>C1</code></customer></sale>
      <sale id="2"><amount>20</amount><customer><code>C2</code></customer></sale></sales>"""
    df = parsers.parse_xml(xml)
    assert list(df["@id"]) == ["1", "2"] and list(df["customer.code"]) == ["C1", "C2"] and list(df["amount"]) == ["10.5", "20"]


@pytest.mark.parametrize("xml", [
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom">]><r><row><v>&a;</v></row></r>',
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><r><row><v>&a;</v></row></r>',
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><r><row><v>&lol2;</v></row></r>',
])
def test_xml_entities_are_rejected(xml):
    with pytest.raises(HTTPException) as e:
        parsers.parse_xml(xml)
    assert e.value.status_code == 400 and "forbidden" in e.value.detail


@pytest.mark.parametrize("xml", [b"<r>", b"<r/>", b"nonsense"])
def test_xml_malformed_or_empty(xml):
    with pytest.raises(HTTPException):
        parsers.parse_xml(xml)


@pytest.mark.parametrize("delim", [",", ";", "|", "\t"])
def test_txt_delimiters_detected(delim):
    body = delim.join(["id", "amt", "ts"]) + "\n" + delim.join(["1", "10", "2026-01-01"]) + "\n" + delim.join(["2", "20", "2026-01-02"]) + "\n"
    df = parsers.parse_txt(body.encode())
    assert list(df.columns) == ["id", "amt", "ts"] and len(df) == 2


def test_txt_latin1_and_errors():
    assert len(parsers.parse_txt("id;name\n1;Café\n".encode("latin-1"))) == 1
    for bad in (b"", b"   \n", b"justoneword\nanother\n", b"a,b\n"):
        with pytest.raises(HTTPException):
            parsers.parse_txt(bad)


@pytest.mark.parametrize("val,expected", [("1,234.50", 1234.5), (" 12 ", 12.0), (7, 7.0), (None, None), ("", None), ("1 000", 1000.0),
    ("KES 1,500", 1500.0), ("Ksh1500", 1500.0), ("1,500/=", 1500.0), ("$ 20", 20.0), ("(1,500)", -1500.0), ("-", None), ("N/A", None)])
def test_to_number_ok(val, expected):
    assert to_number(val, "x") == expected


@pytest.mark.parametrize("val", ["abc", "nan", "inf", "1e999", "12abc"])
def test_to_number_rejects(val):
    with pytest.raises(HTTPException) as e:
        to_number(val, "Row 1 amount")
    assert e.value.status_code == 400 and "Row 1 amount" in e.value.detail


def test_text_amounts_flow_through_mapping_not_500():
    df = parse_file_to_dataframe(b"id,amt,ts\n1,\"1,200\",2026-01-01\n", ".txt")
    ev = dataframe_to_normalized_events(df, ColumnMapping(**{**MAP}), "t", "c", "s").events
    assert ev[0]["amount"] == 1200.0
    bad = parse_file_to_dataframe(b"id,amt,ts\n1,twelve,2026-01-01\n", ".txt")
    with pytest.raises(HTTPException) as e:
        dataframe_to_normalized_events(bad, ColumnMapping(**MAP), "t", "c", "s")
    assert e.value.status_code == 400


@pytest.mark.parametrize("name,ok", [("a.json", True), ("a.JSONL", True), ("a.xml", True), ("a.txt", True), ("a.tsv", True),
                                      ("a.exe", False), ("a.csv.exe", False), ("noext", False), ("a.html", False)])
def test_extension_allowlist(name, ok):
    if ok:
        validate_upload(name, b"x")
    else:
        with pytest.raises(HTTPException):
            validate_upload(name, b"x")


@pytest.mark.parametrize("fname,content", [
    ("s.json", b'[{"id":"j1","amt":10,"ts":"2026-01-01T10:00:00"},{"id":"j2","amt":20,"ts":"2026-01-02T10:00:00"}]'),
    ("s.xml", b"<s><r><id>x1</id><amt>10</amt><ts>2026-01-01T10:00:00</ts></r><r><id>x2</id><amt>20</amt><ts>2026-01-02T10:00:00</ts></r></s>"),
    ("s.txt", b"id|amt|ts\nt1|10|2026-01-01 10:00:00\nt2|20|2026-01-02 10:00:00\n"),
])
def test_upload_each_new_format_end_to_end(client, db, fname, content):
    t, h = make_owner(client, "up")
    c = mk(client, t, h, connector_type="file", config=MAP).json()
    r = client.post(f"/api/connectors/{c['id']}/upload", data={"tenant_id": t}, files={"file": (fname, content)}, headers=h)
    assert r.status_code == 200 and r.json()["rows_processed"] == 2 and r.json()["failed"] == 0, r.text
    assert sorted(float(e.amount) for e in events_for(db, c["id"])) == [10.0, 20.0]
    r2 = client.post(f"/api/connectors/{c['id']}/upload", data={"tenant_id": t}, files={"file": (fname, content)}, headers=h)
    assert all(x["duplicate"] for x in r2.json()["results"])                # re-upload is idempotent
    assert len(events_for(db, c["id"])) == 2


# ---------------- database connector ----------------

@pytest.mark.parametrize("q", [
    "SELECT * FROM sales", "select a, b from t where status = 'delete'", "SELECT 1;", "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT updated_at, inserted_by FROM t", "-- note\nSELECT 1", "SELECT \"update\" FROM t", "SELECT a FROM t /* drop table */",
])
def test_read_only_validator_accepts(q):
    dbc._validate_read_only_query(q)


@pytest.mark.parametrize("q", [
    "", "   ", "DELETE FROM t", "INSERT INTO t VALUES (1)", "SELECT 1; DROP TABLE t", "/* x */ DROP TABLE t", "SELECT * INTO new FROM t",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "SELECT 1 -- \n; UPDATE t SET a=1", "select pg_sleep(1); select 2",
    "EXEC xp_cmdshell 'dir'", "CALL proc()", "SELECT 1 UNION SELECT 2; COPY t TO '/tmp/x'", "PRAGMA table_info(t)",
    "SELECT * FROM t WHERE a = 1 /* ' */ ; DROP TABLE t", None,
])
def test_read_only_validator_rejects(q):
    with pytest.raises(HTTPException) as e:
        dbc._validate_read_only_query(q)
    assert e.value.status_code == 400


def test_driver_catalog_is_honest():
    cat = {d["db_type"]: d for d in dbc.driver_catalog()}
    assert set(cat) == {"postgres", "sqlite", "mysql", "mssql", "oracle", "odbc"}
    assert cat["postgres"]["verified"] and cat["sqlite"]["verified"] and cat["mysql"]["verified"] and cat["postgres"]["available"]
    assert not any(cat[k]["verified"] for k in ("mssql", "oracle", "odbc"))        # nothing is called verified until it has been tried live


def test_url_building_keeps_special_characters_in_password():
    cfg = dbc.DatabaseConnectionConfig(db_type="mysql", host="db.example.com", database="shop", username="ro", password="p@ss:w/rd#1", query="select 1")
    url = dbc.build_url(cfg)
    assert url.password == "p@ss:w/rd#1" and url.host == "db.example.com" and url.port == 3306 and url.drivername == "mysql+pymysql"
    again = make_url(url.render_as_string(hide_password=False))
    assert again.password == "p@ss:w/rd#1"


@pytest.mark.parametrize("db_type,module", [("mssql", "pyodbc"), ("oracle", "oracledb"), ("odbc", "pyodbc")])
def test_missing_driver_is_a_clear_501_not_a_crash(db_type, module):
    if importlib.util.find_spec(module):
        pytest.skip(f"{module} is installed here")
    cfg = dbc.DatabaseConnectionConfig(db_type=db_type, host="h", database="d", username="u", password="p", query="select 1")
    with pytest.raises(HTTPException) as e:
        dbc.build_url(cfg)
    assert e.value.status_code == 501 and module in e.value.detail


def test_unknown_db_type_and_odbc_string_cannot_be_injected():
    with pytest.raises(HTTPException) as e:
        dbc.build_url(dbc.DatabaseConnectionConfig(db_type="mongodb", host="h", query="select 1"))
    assert e.value.status_code == 400
    s = dbc.odbc_connection_string(dbc.DatabaseConnectionConfig(db_type="odbc", dsn="pos", username="u", password="x};DRIVER={evil", query="select 1"))
    assert s == "DSN={pos};UID={u};PWD={x}};DRIVER={evil}"
    assert dbc.odbc_connection_string(dbc.DatabaseConnectionConfig(db_type="odbc", odbc_driver="SQL Server", host="h", port=1433, database="d", query="select 1")).startswith("DRIVER={SQL Server};SERVER={h};PORT=1433")
    with pytest.raises(HTTPException):
        dbc.odbc_connection_string(dbc.DatabaseConnectionConfig(db_type="odbc", query="select 1"))


def pg_source(db, rows):
    name = f"src_{uuid.uuid4().hex[:10]}"
    db.execute(text(f"CREATE TABLE {name} (id text, ts timestamp, amt numeric)"))
    for i, (ts, amt) in enumerate(rows):
        db.execute(text(f"INSERT INTO {name} VALUES (:i, :t, :a)"), {"i": f"r{i}", "t": ts, "a": amt})
    db.commit()
    return name


def _this_database(password=None):
    """Connection settings for the very database the tests run against, whatever DATABASE_URL names."""
    from sqlalchemy.engine import make_url
    u = make_url(os.environ["DATABASE_URL"])
    return {"db_type": "postgres", "host": u.host, "port": u.port or 5432, "database": u.database, "username": u.username,
            "password": password or u.password}


def test_postgres_sync_end_to_end_encrypted_password_and_idempotent(client, db):
    t, h = make_owner(client, "pg")
    tbl = pg_source(db, [(dt.datetime(2026, 1, 1, 9), 100), (dt.datetime(2026, 1, 2, 9), 250.5)])
    cfg = {**MAP, **_this_database(), "query": f"SELECT id, ts, amt FROM {tbl}"}
    try:
        c = mk(client, t, h, connector_type="database", config=cfg).json()
        stored = db.get(Connector, c["id"]).config["password"]
        assert stored != _this_database()["password"] and stored.startswith("gAAAA")                           # encrypted at rest
        r = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h)
        assert r.status_code == 200 and r.json()["rows_fetched"] == 2 and r.json()["failed"] == 0, r.text
        assert sorted(float(e.amount) for e in events_for(db, c["id"])) == [100.0, 250.5]
        again = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h).json()
        assert all(x["duplicate"] for x in again["results"])
        assert "password" not in json.dumps(client.get(f"/api/connectors/{c['id']}?tenant_id={t}", headers=h).json())
        db.expire_all()
        assert db.get(Connector, c["id"]).last_attempt_at is not None
    finally:
        db.execute(text(f"DROP TABLE IF EXISTS {tbl}")); db.commit()


def test_postgres_connection_failure_is_recorded_without_leaking_credentials(client, db):
    t, h = make_owner(client, "pgbad")
    cfg = {**MAP, **_this_database(password="wrong-password-xyz"), "query": "SELECT 1 AS id, now() AS ts, 1 AS amt"}
    c = mk(client, t, h, connector_type="database", config=cfg).json()
    r = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h)
    assert r.status_code == 502 and "wrong-password-xyz" not in r.text and "postgres" not in r.text.lower().replace("postgresql", "")
    db.expire_all()
    assert db.get(Connector, c["id"]).last_sync_status == "failed"


def make_sqlite(root, tenant, name="pos.db", rows=((("a1", "2026-01-01 10:00:00", 5.0)), ("a2", "2026-01-02 10:00:00", 7.5))):
    d = root / tenant; d.mkdir(exist_ok=True)
    con = sqlite3.connect(d / name)
    con.execute("CREATE TABLE sales (id text, ts text, amt real)")
    con.executemany("INSERT INTO sales VALUES (?,?,?)", rows)
    con.commit(); con.close()


def test_sqlite_sync_inside_tenant_folder_and_isolation(client, db, root):
    t, h = make_owner(client, "sq"); other, h2 = make_owner(client, "sq2")
    make_sqlite(root, t)
    cfg = {**MAP, "db_type": "sqlite", "database": "pos.db", "query": "SELECT id, ts, amt FROM sales"}
    c = mk(client, t, h, connector_type="database", config=cfg)
    assert c.status_code == 200, c.text
    cid = c.json()["id"]
    r = client.post(f"/api/connectors/{cid}/sync-database?tenant_id={t}", headers=h)
    assert r.status_code == 200 and r.json()["rows_processed"] == 2, r.text
    assert mk(client, other, h2, connector_type="database", config=cfg).status_code == 400            # the other business has no such file
    (root / other).mkdir(exist_ok=True)
    assert mk(client, other, h2, connector_type="database", config={**cfg, "database": f"../{t}/pos.db"}).status_code == 400
    assert mk(client, other, h2, connector_type="database", config={**cfg, "database": str(root / t / "pos.db")}).status_code == 400


def test_sqlite_is_read_only_even_if_query_check_were_bypassed(root, db):
    t = _make_tenant("sqro"); make_sqlite(root, t)
    cfg = {"db_type": "sqlite", "database": "pos.db", "query": "SELECT 1"}
    dbc.fetch_rows(cfg, t)
    con = sqlite3.connect(root / t / "pos.db"); con.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO sales VALUES ('x','y',1)")


def test_file_connectors_disabled_without_root(client, monkeypatch):
    monkeypatch.delenv("CONNECTOR_FILE_ROOT", raising=False)
    t, h = make_owner(client, "noroot")
    r = mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox"})
    assert r.status_code == 501 and "CONNECTOR_FILE_ROOT" in r.text


def test_private_hosts_blocked_when_switched_on(client, monkeypatch):
    t, h = make_owner(client, "priv")
    cfg = {**MAP, "db_type": "postgres", "host": "127.0.0.1", "database": "d", "username": "u", "password": "p", "query": "SELECT 1"}
    assert mk(client, t, h, connector_type="database", config=cfg).status_code == 200
    monkeypatch.setenv("BLOCK_PRIVATE_CONNECTOR_HOSTS", "true")
    r = mk(client, t, h, connector_type="database", config=cfg)
    assert r.status_code == 400 and "private" in r.text
    assert mk(client, t, h, connector_type="sftp", config={"host": "localhost", "username": "u", "password": "p", "remote_directory": "/"}).status_code == 400
    assert mk(client, t, h, connector_type="email", config={"imap_host": "10.0.0.5", "username": "u", "password": "p"}).status_code == 400


def test_email_defaults_to_encrypted_and_sftp_supports_pinning():
    from app.connectors.email_connector import EmailConnectionConfig
    from app.connectors.sftp_connector import SftpConnectionConfig
    e = EmailConnectionConfig(imap_host="h", username="u", password="p")
    assert e.use_ssl is True and e.imap_port == 993
    assert "host_key_sha256" in SftpConnectionConfig.model_fields


# ---------------- shared folder ----------------

def put(root, tenant, name, content, folder="inbox"):
    d = root / tenant / folder; d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(content if isinstance(content, bytes) else content.encode())


def folder_connector(client, t, h, **extra):
    return mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox", **extra}).json()["id"]


def test_shared_folder_tracks_content_hash(client, db, root):
    t, h = make_owner(client, "fold")
    (root / t / "inbox").mkdir(parents=True)
    cid = folder_connector(client, t, h)
    url = f"/api/connectors/{cid}/sync-folder?tenant_id={t}"
    put(root, t, "mon.csv", "id,amt,ts\nf1,10,2026-01-01 09:00:00\nf2,20,2026-01-01 10:00:00\n")
    put(root, t, "notes.docx", "ignored")
    r = client.post(url, headers=h).json()
    assert r["files_found"] == 1 and r["files"][0]["rows_processed"] == 2
    assert client.post(url, headers=h).json()["files_found"] == 0                               # unchanged: skipped
    put(root, t, "mon-copy.csv", "id,amt,ts\nf1,10,2026-01-01 09:00:00\nf2,20,2026-01-01 10:00:00\n")
    assert client.post(url, headers=h).json()["files_found"] == 0                               # same content, new name: skipped
    put(root, t, "mon.csv", "id,amt,ts\nf1,10,2026-01-01 09:00:00\nf2,20,2026-01-01 10:00:00\nf3,30,2026-01-01 11:00:00\n")
    r3 = client.post(url, headers=h).json()
    assert r3["files_found"] == 1 and r3["files"][0]["rows_processed"] == 3                     # corrected file is picked up...
    assert len(events_for(db, cid)) == 3                                                         # ...without duplicating rows already ingested


def test_shared_folder_bad_file_reported_once_not_retried_and_does_not_stop_others(client, db, root):
    t, h = make_owner(client, "fold2")
    cid = (root / t / "inbox").mkdir(parents=True) or folder_connector(client, t, h)
    put(root, t, "bad.csv", "id,amt,ts\nb1,notanumber,2026-01-01\n")
    put(root, t, "good.csv", "id,amt,ts\ng1,5,2026-01-01 09:00:00\n")
    url = f"/api/connectors/{cid}/sync-folder?tenant_id={t}"
    r = client.post(url, headers=h).json()
    by = {f["filename"]: f for f in r["files"]}
    assert "error" in by["bad.csv"] and by["good.csv"]["rows_processed"] == 1
    assert client.post(url, headers=h).json()["files_found"] == 0


def test_shared_folder_ignores_symlinks_escaping_and_big_files(client, root, monkeypatch):
    t, h = make_owner(client, "fold3")
    d = root / t / "inbox"; d.mkdir(parents=True)
    secret = root / "secret.csv"; secret.write_text("id,amt,ts\ns1,1,2026-01-01\n")
    os.symlink(secret, d / "link.csv")
    put(root, t, "empty.csv", b"")
    cid = folder_connector(client, t, h)
    r = client.post(f"/api/connectors/{cid}/sync-folder?tenant_id={t}", headers=h).json()
    names = {f["filename"]: f for f in r["files"]}
    assert "link.csv" not in names and "skipped: empty" in names["empty.csv"]["error"]


@pytest.mark.parametrize("path", ["../other", "/etc", "inbox/../../x", "~/x", "..", "a\x00b", ""])
def test_shared_folder_path_traversal_refused(client, root, path):
    t, h = make_owner(client, "trav")
    (root / t / "inbox").mkdir(parents=True)
    assert mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": path}).status_code == 400


def test_shared_folder_symlinked_directory_cannot_escape(client, root):
    t, h = make_owner(client, "trav2")
    (root / t).mkdir()
    outside = root / "outside"; outside.mkdir()
    os.symlink(outside, root / t / "inbox")
    assert mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox"}).status_code == 400


# ---------------- connector management ----------------

def test_create_validation(client):
    t, h = make_owner(client, "val")
    assert mk(client, t, h, connector_type="telepathy").status_code == 400
    assert mk(client, t, h, connector_type="file", expected_frequency="sometimes").status_code == 400
    assert mk(client, t, h, connector_type="file", name="  ").status_code == 400
    assert mk(client, t, h, connector_type="database", config={"db_type": "postgres", "host": "h", "query": "DROP TABLE x"}).status_code == 400
    assert mk(client, t, h, connector_type="database", config={"db_type": "nosql", "query": "select 1"}).status_code == 400
    assert mk(client, t, h, connector_type="file", sync_interval_minutes=10).status_code == 400      # not pull-type
    assert mk(client, t, h, connector_type="file").status_code == 200


def test_connector_management_is_owner_only_and_tenant_scoped(client):
    t = _make_tenant("own"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    assert mk(client, t, bearer(staff), connector_type="file").status_code == 403
    cid = mk(client, t, bearer(owner), connector_type="file", config=MAP).json()["id"]
    assert client.patch(f"/api/connectors/{cid}?tenant_id={t}", json={"name": "x"}, headers=bearer(staff)).status_code == 403
    other, ho = make_owner(client, "own2")
    assert client.patch(f"/api/connectors/{cid}?tenant_id={other}", json={"name": "x"}, headers=ho).status_code == 404
    assert client.get(f"/api/connectors/{cid}?tenant_id={other}", headers=ho).status_code == 404


def test_patch_rules(client, db):
    t, h = make_owner(client, "patch")
    pg = {**MAP, "db_type": "postgres", "host": "localhost", "database": "d", "username": "u", "password": "secret1", "query": "SELECT 1"}
    cid = mk(client, t, h, connector_type="database", config=pg).json()["id"]
    p = lambda body: client.patch(f"/api/connectors/{cid}?tenant_id={t}", json=body, headers=h)
    assert p({"name": " "}).status_code == 400
    assert p({"expected_frequency": "x"}).status_code == 400
    assert p({"sync_interval_minutes": 1}).status_code == 400 and p({"sync_interval_minutes": 99999}).status_code == 400
    r = p({"sync_interval_minutes": 15, "name": "Renamed", "expected_frequency": "periodic"}); assert r.status_code == 200
    assert r.json()["sync_interval_minutes"] == 15 and r.json()["name"] == "Renamed"
    assert p({"config": {**pg, "query": "DELETE FROM x"}}).status_code == 400
    assert p({"config": {**pg, "password": "newsecret"}}).status_code == 200
    db.expire_all()
    stored = db.get(Connector, cid).config["password"]
    assert stored.startswith("gAAAA") and stored != "newsecret"
    assert p({"clear_sync_interval": True}).json()["sync_interval_minutes"] is None
    assert p({"is_active": False}).json()["is_active"] is False
    assert client.post(f"/api/connectors/{cid}/sync-database?tenant_id={t}", headers=h).status_code == 409


def test_patch_config_keeps_webhook_token(client, db):
    t, h = make_owner(client, "patchwh")
    wh = {"entity": "sale", "event_type": "sale.created", "amount_path": "amount"}
    made = mk(client, t, h, connector_type="webhook", config=wh).json()
    r = client.patch(f"/api/connectors/{made['id']}?tenant_id={t}", json={"config": {**wh, "location_path": "shop"}}, headers=h)
    assert r.status_code == 200
    assert client.post(made["webhook_url"], json={"amount": 5}).status_code == 200              # old token still works


def test_sync_endpoint_wrong_type_400(client):
    t, h = make_owner(client, "wrongtype")
    cid = mk(client, t, h, connector_type="file").json()["id"]
    assert client.post(f"/api/connectors/{cid}/sync-database?tenant_id={t}", headers=h).status_code == 400


# ---------------- scheduled / locked sync ----------------

def test_sync_lock_blocks_concurrent_run(client, db, root):
    t, h = make_owner(client, "lock")
    (root / t / "inbox").mkdir(parents=True)
    cid = folder_connector(client, t, h)
    holder = engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": f"sync:{cid}"})
    try:
        r = client.post(f"/api/connectors/{cid}/sync-folder?tenant_id={t}", headers=h)
        assert r.status_code == 409 and "already running" in r.text
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": f"sync:{cid}"}); holder.close()
    assert client.post(f"/api/connectors/{cid}/sync-folder?tenant_id={t}", headers=h).status_code == 200      # lock released afterwards


def test_due_connectors_and_run_due_syncs(client, db, root):
    t, h = make_owner(client, "due")
    (root / t / "inbox").mkdir(parents=True)
    put(root, t, "a.csv", "id,amt,ts\nd1,10,2026-01-01 09:00:00\n")
    cid = mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox"}, sync_interval_minutes=15).json()["id"]
    manual = folder_connector(client, t, h)                                   # no interval: never scheduled
    now = dt.datetime.utcnow()
    assert {c.id for c in sync_mod.due_connectors(db, now)} >= {cid} and manual not in {c.id for c in sync_mod.due_connectors(db, now)}
    stats = sync_mod.run_due_syncs(db, now)
    assert stats["ok"] >= 1 and len(events_for(db, cid)) == 1
    db.expire_all()
    assert cid not in {c.id for c in sync_mod.due_connectors(db, now + dt.timedelta(minutes=14))}              # not due again yet
    assert cid in {c.id for c in sync_mod.due_connectors(db, now + dt.timedelta(minutes=16))}


def test_scheduled_failure_is_recorded_and_isolated(client, db, root):
    t, h = make_owner(client, "dfail")
    (root / t / "inbox").mkdir(parents=True)
    bad = mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox"}, sync_interval_minutes=5).json()["id"]
    put(root, t, "ok.csv", "id,amt,ts\nz1,1,2026-01-01 09:00:00\n")
    good = mk(client, t, h, connector_type="shared_folder", config={**MAP, "path": "inbox"}, sync_interval_minutes=5, name="g2").json()["id"]
    import shutil; shutil.rmtree(root / t / "inbox")                               # both break...
    (root / t / "inbox").mkdir()
    # ... then only `good` has data again; `bad` points at a missing sub-path
    row = db.get(Connector, bad); row.config = {**row.config, "path": "gone"}; db.commit()
    put(root, t, "ok.csv", "id,amt,ts\nz1,1,2026-01-01 09:00:00\n")
    stats = sync_mod.run_due_syncs(db, dt.datetime.utcnow() + dt.timedelta(minutes=1))
    assert stats["failed"] >= 1 and stats["ok"] >= 1
    db.expire_all()
    assert db.get(Connector, bad).last_sync_status == "failed" and len(events_for(db, good)) == 1


def test_scheduler_registers_sync_job():
    from app.core import scheduler
    scheduler.start_scheduler(60)
    try:
        assert {j.id for j in scheduler._scheduler.get_jobs()} >= {"detection_cycle", "notification_dispatch", "connector_sync"}
    finally:
        scheduler.stop_scheduler()


# ---------------- webhook batch / local agent ----------------

AGENT_MAP = {"entity": "sale", "event_type": "sale.created", "timestamp_path": "ts", "amount_path": "amount", "entity_id_path": "id",
             "location_path": "shop"}


def agent_connector(client, t, h):
    r = mk(client, t, h, connector_type="local_agent", config=AGENT_MAP, name="Till PC").json()
    return r["id"], r["webhook_token"], r["webhook_url"]


def test_batch_endpoint_accepts_dedups_and_reports_rejects(client, db):
    t, h = make_owner(client, "batch")
    cid, token, url = agent_connector(client, t, h)
    body = {"events": [{"id": "e1", "amount": 10, "ts": "2026-01-01T10:00:00", "shop": "Meru"},
                       {"id": "e2", "amount": 20, "ts": "2026-01-01T11:00:00"},
                       {"id": "e3", "amount": 30, "ts": "not-a-date"}, "oops", {"id": "e1", "amount": 10, "ts": "2026-01-01T10:00:00"}]}
    r = client.post(f"{url}/batch", json=body).json()
    assert r["received"] == 5 and r["accepted"] == 3 and r["duplicates"] == 1
    assert sorted(x["index"] for x in r["rejected"]) == [2, 3]
    assert len(events_for(db, cid)) == 2
    assert client.post(f"{url}/batch", json={"events": []}).json() == {"received": 0, "accepted": 0, "duplicates": 0, "failed_validation": 0, "rejected": []}


def test_batch_endpoint_security_and_limits(client):
    t, h = make_owner(client, "batch2")
    cid, token, url = agent_connector(client, t, h)
    assert client.post(f"/api/webhooks/{cid}/wrong-token/batch", json={"events": []}).status_code == 404
    assert client.post(f"/api/webhooks/{uuid.uuid4()}/{token}/batch", json={"events": []}).status_code == 404
    assert client.post(f"{url}/batch", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert client.post(f"{url}/batch", json=[1, 2]).status_code == 400
    assert client.post(f"{url}/batch", json={"events": "x"}).status_code == 400
    assert client.post(f"{url}/batch", json={"events": [{"id": str(i), "amount": 1} for i in range(501)]}).status_code == 413
    fc = mk(client, t, h, connector_type="file", config=MAP).json()["id"]
    assert client.post(f"/api/webhooks/{fc}/{token}/batch", json={"events": []}).status_code == 404     # other connector types refuse
    client.patch(f"/api/connectors/{cid}?tenant_id={t}", json={"is_active": False}, headers=h)
    assert client.post(f"{url}/batch", json={"events": []}).status_code == 409


def test_batch_events_land_in_the_connectors_own_tenant(client, db):
    ta, ha = make_owner(client, "iso1"); tb, hb = make_owner(client, "iso2")
    cid, _, url = agent_connector(client, ta, ha)
    client.post(f"{url}/batch", json={"events": [{"id": "x", "amount": 1, "ts": "2026-01-01T10:00:00"}]})
    assert db.query(BusinessEvent).filter(BusinessEvent.tenant_id == tb).count() == 0
    assert db.query(BusinessEvent).filter(BusinessEvent.tenant_id == ta).count() == 1


class ClientPost:
    """Lets the stdlib agent talk to the in-process app."""
    def __init__(self, client):
        self.client, self.calls = client, 0

    def __call__(self, url, body):
        self.calls += 1
        path = url[url.index("/api/"):]
        r = self.client.post(path, content=body, headers={"content-type": "application/json"})
        return r.status_code, r.json()


def agent_cfg(tmp_path, cid, token, source, **kw):
    return {"base_url": "https://engine.example", "connector_id": cid, "token": token, "state_file": str(tmp_path / "state.json"),
            "source": source, **kw}


def test_agent_csv_folder_incremental_and_state_only_after_delivery(client, db, tmp_path):
    t, h = make_owner(client, "ag1")
    cid, token, _ = agent_connector(client, t, h)
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.csv").write_text("id,amount,ts,shop\nc1,10,2026-01-01T10:00:00,Meru\nc2,20,2026-01-01T11:00:00,Meru\n")
    cfg = agent_cfg(tmp_path, cid, token, {"type": "csv_folder", "path": str(tmp_path / "x"), "pattern": "*.csv"})
    post = ClientPost(client)
    assert agent.run_once(cfg, post=post)["accepted"] == 2
    assert agent.run_once(cfg, post=post) == {"read": 0, "sent": 0}                           # same file: not re-read
    (tmp_path / "x" / "b.csv").write_text("id,amount,ts,shop\nc3,30,2026-01-02T10:00:00,Nairobi\n")
    assert agent.run_once(cfg, post=post)["accepted"] == 1
    assert len(events_for(db, cid)) == 3


def test_agent_does_not_advance_state_when_delivery_fails(client, tmp_path):
    t, h = make_owner(client, "ag2")
    cid, token, _ = agent_connector(client, t, h)
    (tmp_path / "x").mkdir(); (tmp_path / "x" / "a.csv").write_text("id,amount,ts\nq1,1,2026-01-01T10:00:00\n")
    cfg = agent_cfg(tmp_path, cid, token, {"type": "csv_folder", "path": str(tmp_path / "x")})
    sleeps = []
    with pytest.raises(RuntimeError, match="5 attempts"):
        agent.run_once(cfg, post=lambda u, b: (503, {}), sleep=sleeps.append)
    assert sleeps == [1, 2, 4, 8, 16] and not (tmp_path / "state.json").exists()
    assert agent.run_once(cfg, post=ClientPost(client))["accepted"] == 1                         # next pass delivers it


def test_agent_network_errors_retry_and_client_errors_do_not(tmp_path):
    cfg = agent_cfg(tmp_path, "c", "t", {"type": "command", "argv": [sys.executable, "-c", "print('{\"id\":\"1\"}')"]})
    calls = {"n": 0}

    def flaky(url, body):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("down")
        return 200, {"accepted": 1}
    assert agent.run_once(cfg, post=flaky, sleep=lambda s: None)["accepted"] == 1 and calls["n"] == 3
    with pytest.raises(RuntimeError, match="HTTP 404"):
        agent.run_once(cfg, post=lambda u, b: (404, {"detail": "Not found"}), sleep=lambda s: None)


def test_agent_sqlite_incremental(client, db, tmp_path):
    t, h = make_owner(client, "ag3")
    cid, token, _ = agent_connector(client, t, h)
    p = tmp_path / "till.db"
    con = sqlite3.connect(p); con.execute("CREATE TABLE s (n integer, id text, amount real, ts text, shop text)")
    con.executemany("INSERT INTO s VALUES (?,?,?,?,?)", [(1, "s1", 5, "2026-01-01T10:00:00", "A"), (2, "s2", 6, "2026-01-01T11:00:00", "A")]); con.commit()
    cfg = agent_cfg(tmp_path, cid, token, {"type": "sqlite", "path": str(p), "query": "SELECT * FROM s WHERE n > :since", "incremental_column": "n"})
    post = ClientPost(client)
    assert agent.run_once(cfg, post=post)["accepted"] == 2
    assert agent.run_once(cfg, post=post)["read"] == 0
    con.execute("INSERT INTO s VALUES (3,'s3',7,'2026-01-02T10:00:00','B')"); con.commit(); con.close()
    assert agent.run_once(cfg, post=post)["accepted"] == 1 and len(events_for(db, cid)) == 3


def test_agent_command_source_for_legacy_apps(client, db, tmp_path):
    t, h = make_owner(client, "ag4")
    cid, token, _ = agent_connector(client, t, h)
    script = tmp_path / "export.py"
    script.write_text("import json\nfor i in range(3):\n    print(json.dumps({'id': f'k{i}', 'amount': i+1, 'ts': '2026-01-03T10:00:00', 'shop': 'Till 1'}))\nprint()\n")
    cfg = agent_cfg(tmp_path, cid, token, {"type": "command", "argv": [sys.executable, str(script)]})
    assert agent.run_once(cfg, post=ClientPost(client))["accepted"] == 3
    assert agent.run_once(cfg, post=ClientPost(client))["duplicates"] == 3                       # command has no state: server dedups
    assert {e.location for e in events_for(db, cid)} == {"Till 1"}


@pytest.mark.parametrize("code,msg", [("import sys; sys.exit(3)", "exited with 3"), ("print('not json')", "not JSON"), ("print('[1]')", "not a JSON object")])
def test_agent_command_failures_surface(tmp_path, code, msg):
    cfg = agent_cfg(tmp_path, "c", "t", {"type": "command", "argv": [sys.executable, "-c", code]})
    with pytest.raises(RuntimeError, match=msg):
        agent.run_once(cfg, post=lambda u, b: (200, {}))


def test_agent_guards(tmp_path):
    with pytest.raises(ValueError, match="plain http"):
        agent.http_post("http://engine.example/api/x", b"{}")
    with pytest.raises(ValueError, match="argv"):
        agent.read_command({"argv": "rm -rf /"}, {})
    with pytest.raises(ValueError, match="unknown source"):
        agent.run_once({"source": {"type": "ftp"}, "state_file": str(tmp_path / "s.json")})
    cfg = agent_cfg(tmp_path, "c", "t", {"type": "command", "argv": [sys.executable, "-c", "print('{\"id\":1}')"]})
    assert agent.run_once(cfg, dry_run=True) == {"read": 1, "dry_run": True}
    assert not (tmp_path / "state.json").exists()


def test_agent_batches_are_capped_at_500(tmp_path):
    sizes = []
    cfg = agent_cfg(tmp_path, "c", "t", {"type": "command", "argv": [sys.executable, "-c", "print('{}')"]}, batch_size=100000)
    agent.send_rows(cfg, [{}] * 1200, post=lambda u, b: (sizes.append(len(json.loads(b)["events"])) or (200, {})))
    assert sizes == [500, 500, 200]


# ---------------- manual capture ----------------

def test_manual_capture_flow(client, db):
    t = _make_tenant("cap"); _, _, owner = register(client, t, "owner"); _, _, staff = register(client, t, "staff")
    body = {"tenant_id": t, "entries": [
        {"entity": "sale", "amount": 150, "location": "Meru", "customer": "Amina", "product": "Tea", "client_ref": "r1", "note": "ledger p3"},
        {"entity": "expense", "amount": 40.5, "supplier": "Acme", "currency": "kes"}]}
    r = client.post("/api/capture/", json=body, headers=bearer(staff))
    assert r.status_code == 200 and r.json()["entered"] == 2 and r.json()["failed_validation"] == 0, r.text
    again = client.post("/api/capture/", json=body, headers=bearer(staff)).json()
    assert again["duplicates"] == 1                                                              # the entry with a client_ref is idempotent
    conns = db.query(Connector).filter(Connector.tenant_id == t, Connector.connector_type == "manual").all()
    assert len(conns) == 1 and conns[0].name == "Manual capture"
    ev = db.query(BusinessEvent).filter(BusinessEvent.connector_id == conns[0].id, BusinessEvent.entity == "sale").first()
    assert ev.metadata_json["human_entered"] is True and ev.metadata_json["note"] == "ledger p3" and ev.metadata_json["entered_by"]
    assert ev.resolved_customer_id and ev.resolved_product_id and ev.validation_status == "validated"


def test_manual_capture_validation_and_isolation(client, db):
    t = _make_tenant("cap2"); _, _, owner = register(client, t, "owner")
    h = bearer(owner)
    ok = {"entity": "sale", "amount": 5}
    assert client.post("/api/capture/", json={"tenant_id": t, "entries": [{**ok, "entity": "refund"}]}, headers=h).status_code == 400
    for bad in ({**ok, "amount": 0}, {**ok, "amount": -5}, {**ok, "amount": 1e13}, {**ok, "currency": "KENYA"}, {**ok, "quantity": 0}):
        assert client.post("/api/capture/", json={"tenant_id": t, "entries": [bad]}, headers=h).status_code == 422
    assert client.post("/api/capture/", json={"tenant_id": t, "entries": []}, headers=h).status_code == 422
    assert client.post("/api/capture/", json={"tenant_id": t, "entries": [ok] * 101}, headers=h).status_code == 422
    assert client.post("/api/capture/", json={"tenant_id": t, "entries": [ok]}).status_code == 401
    other = _make_tenant("cap3"); _, _, o2 = register(client, other, "owner")
    assert client.post("/api/capture/", json={"tenant_id": t, "entries": [ok]}, headers=bearer(o2)).status_code == 403
    fut = (dt.datetime.utcnow() + dt.timedelta(days=3)).isoformat()
    r = client.post("/api/capture/", json={"tenant_id": t, "entries": [{**ok, "timestamp": fut}]}, headers=h).json()
    assert r["failed_validation"] == 1                                                            # future-dated entries are flagged, not trusted
    assert db.query(BusinessEvent).filter(BusinessEvent.tenant_id == other).count() == 0
