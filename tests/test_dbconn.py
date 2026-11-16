"""Server databases through the agent's driver layer. The live tests need a PostgreSQL and a MariaDB/MySQL server to talk to and are
skipped when DBCONN_TEST_PG / DBCONN_TEST_MYSQL (JSON: {"admin": {...connection...}}) are not set; the rest run anywhere."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import dbconn as D  # noqa: E402

LIVE = {k: json.loads(os.environ[v]) for k, v in (("postgres", "DBCONN_TEST_PG"), ("mysql", "DBCONN_TEST_MYSQL")) if os.environ.get(v)}


def spec(kind, user, password, **over):
    base = LIVE[kind]["server"]
    return {"kind": kind, "name": "shop", **base, "user": user, "password": password, **over}


@pytest.fixture(params=sorted(LIVE) or [pytest.param(None, marks=pytest.mark.skip("no database server configured"))])
def kind(request):
    k = request.param
    admin = D.connect(spec(k, *LIVE[k]["admin"]), readonly=False)
    q = D.DIALECTS[k].quote
    for t in ("tbl_sales", "supplier_bills", 'odd"name'):
        admin.execute(f"DROP TABLE IF EXISTS {q(t)}")
    admin.execute(f"CREATE TABLE {q('tbl_sales')} (sale_no INTEGER PRIMARY KEY, sale_date VARCHAR(20), grand_total NUMERIC(12,2), client VARCHAR(50))")
    admin.execute(f"CREATE TABLE {q('supplier_bills')} (bill_id INTEGER PRIMARY KEY, paid_on DATE, amount NUMERIC(12,2))")
    admin.execute(f"CREATE TABLE {q(chr(111) + 'dd' + chr(34) + 'name')} (id INTEGER PRIMARY KEY, v INTEGER)")
    admin.execute(f"INSERT INTO {q('tbl_sales')} VALUES (1, '2026-03-01', 1200.50, 'Wanjiru'), (2, '2026-03-02', 300, 'Otieno')")
    admin.execute(f"INSERT INTO {q('supplier_bills')} VALUES (1, '2026-03-01', 800), (2, '2026-03-03', 150.25)")
    if k == "postgres":
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader")
        admin.execute("GRANT ALL ON ALL TABLES IN SCHEMA public TO writer")
    else:
        admin.execute("FLUSH PRIVILEGES")
    admin.close()
    return k


def reader(kind, **over):
    return spec(kind, "reader", "pw1", **over)


def test_describing_gives_names_types_keys_and_counts_and_no_values(kind):
    c = D.connect(reader(kind))
    try:
        d = c.describe()
    finally:
        c.close()
    assert d["error"] is None and d["source"]["kind"] == kind
    t = {x["table"]: x for x in d["tables"]}
    assert {"tbl_sales", "supplier_bills"} <= set(t)
    assert [col["name"] for col in t["tbl_sales"]["columns"]] == ["sale_no", "sale_date", "grand_total", "client"]
    assert [col["name"] for col in t["tbl_sales"]["columns"] if col["pk"]] == ["sale_no"]
    assert "Wanjiru" not in json.dumps(d)
    assert t["tbl_sales"]["rows"] is not None


def test_samples_only_when_asked_and_clipped(kind):
    c = D.connect(reader(kind))
    try:
        d = c.describe(samples=2, sample_chars=4)
    finally:
        c.close()
    client = next(col for x in d["tables"] if x["table"] == "tbl_sales" for col in x["columns"] if col["name"] == "client")
    assert client["samples"] and all(len(v) <= 4 for v in client["samples"])


def test_the_session_cannot_change_data_even_for_a_login_that_could(kind):
    c = D.connect(spec(kind, "writer", "pw2"))
    try:
        assert c.writable() is True
        with pytest.raises(Exception):
            c.execute(f"INSERT INTO {c.dialect.quote('supplier_bills')} VALUES (99, '2026-04-01', 1)")
        assert c.execute(f"SELECT COUNT(*) FROM {c.dialect.quote('supplier_bills')}").fetchone()[0] == 2
    finally:
        c.close()
    r = D.connect(reader(kind))
    try:
        assert r.writable() is False
    finally:
        r.close()


def test_problems_are_named_in_plain_words(kind):
    for over, want in (({"password": "wrong"}, "login"), ({"database": "no_such_db"}, "missing"), ({"port": 1}, "unreachable")):
        with pytest.raises(D.DbProblem) as e:
            D.connect({**reader(kind), **over}, timeout=3)
        assert e.value.kind == want, (over, e.value.message)
        assert len(e.value.message) > 30


def test_statements_compile_check_and_parameters_work(kind):
    c = D.connect(reader(kind))
    try:
        q = c.dialect.quote
        good = f"SELECT {q('sale_no')} AS id FROM {q('tbl_sales')} WHERE {q('sale_no')} > ?"
        c.compile_check(good.replace("?", "0"))
        assert [r[0] for r in c.execute(good + " ORDER BY 1", (1,)).fetchall()] == [2]
        with pytest.raises(Exception):
            c.compile_check(f"SELECT {q('nope')} FROM {q('tbl_sales')}")
    finally:
        c.close()


def test_odd_table_names_are_quoted_not_executed(kind):
    c = D.connect(reader(kind))
    try:
        d = c.describe()
        assert 'odd"name' in {t["table"] for t in d["tables"]}
        assert c.execute(f"SELECT COUNT(*) FROM {c.dialect.quote('odd' + chr(34) + 'name')}").fetchone()[0] == 0
    finally:
        c.close()


def test_the_connection_file_is_private_and_holds_the_secret_only_here(tmp_path):
    D.save_connections(tmp_path, {"shop": {"kind": "mysql", "host": "db", "password": "s3cret"}, "bad": {"kind": "oracle"}})
    assert (tmp_path / "connections.json").stat().st_mode & 0o077 == 0
    assert list(D.load_connections(tmp_path)) == ["shop"]
    assert "s3cret" not in json.dumps(D.public(D.load_connections(tmp_path)["shop"]))
    assert D.load_connections(tmp_path / "missing") == {}


def test_dialects_quote_and_limit_the_way_each_database_expects():
    assert D.DIALECTS["mysql"].quote("a`b") == "`a``b`"
    assert D.DIALECTS["mssql"].quote("a]b") == "[a]]b]"
    assert D.DIALECTS["mssql"].limited("x", "[t]", 5, "a > 1") == "SELECT TOP 5 x FROM [t] WHERE a > 1"
    assert D.DIALECTS["postgres"].limited("x", '"t"', 5) == 'SELECT x FROM "t" LIMIT 5'
    assert D.DIALECTS["mssql"].table("dbo", "Sales") == "[dbo].[Sales]"
    assert D.dialect_for({"kind": "odbc", "connection_string": "Driver={Microsoft Access Driver (*.mdb)};DBQ=x.mdb"}).name == "access"
    assert D.convert_placeholders("a = ? AND b LIKE '%?%'", "format") == "a = %s AND b LIKE '%%?%%'"
    assert D.convert_placeholders("a = ?", "qmark") == "a = ?"


def test_a_missing_driver_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    with pytest.raises(D.DbProblem) as e:
        D.connect({"kind": "odbc", "connection_string": "x"})
    assert e.value.kind == "driver" and "pip install pyodbc" in e.value.message


def test_a_server_that_is_not_on_this_network_is_encrypted_by_default():
    assert D._LOCAL.match("localhost") and D._LOCAL.match("192.168.1.20") and D._LOCAL.match("SERVER01")
    assert not D._LOCAL.match("db.example.com") and not D._LOCAL.match("34.12.9.1")


def test_servers_on_this_computer_are_found_without_trying_any_login():
    if not LIVE:
        pytest.skip("no database server configured")
    found = {(s["kind"], s["port"]) for s in D.find_servers(("127.0.0.1",)) if s["kind"] != "odbc"}
    assert {(k, LIVE[k]["server"]["port"]) for k in LIVE} <= found
    assert D.find_servers(("127.0.0.1",), wait=0.05) is not None and D.own_network_hosts() is not None


def test_the_databases_on_a_server_are_listed_for_choosing_system_ones_left_out(kind):
    names = {d["name"] for d in D.list_databases({**reader(kind), "database": None})}
    assert "duka" in names and not names & {"information_schema", "mysql", "performance_schema", "template0", "template1"}


def test_listing_databases_names_a_wrong_password_in_plain_words(kind):
    with pytest.raises(D.DbProblem) as e:
        D.list_databases({**reader(kind), "password": "wrong"})
    assert e.value.kind == "login"
