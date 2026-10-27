"""The MySQL database connector against a real MySQL/MariaDB server. Skipped unless MYSQL_TEST_URL is set, for example
mysql+pymysql://bre:bre-pass@127.0.0.1:3306/shop (the user needs CREATE, INSERT and DROP on that database). CI starts a
MySQL service and sets it, so this is what backs the 'verified' flag on the mysql driver."""
import datetime as dt
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.connectors.database_connector import DRIVERS
from tests.test_block_c import MAP, db, events_for, make_owner, mk  # noqa: F401  (db is a fixture)

URL = os.environ.get("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MYSQL_TEST_URL to run against a real MySQL or MariaDB server")


@pytest.fixture()
def table():
    eng = create_engine(URL)
    name = f"sales_{uuid.uuid4().hex[:8]}"
    with eng.begin() as c:
        c.execute(text(f"CREATE TABLE {name} (id VARCHAR(20) PRIMARY KEY, ts DATETIME, amt DECIMAL(12,2))"))
        for i, (when, amount) in enumerate([(dt.datetime(2026, 1, 1, 9), 100), (dt.datetime(2026, 1, 2, 9), 250.5)]):
            c.execute(text(f"INSERT INTO {name} VALUES (:i, :t, :a)"), {"i": f"r{i}", "t": when, "a": amount})
    yield name
    with eng.begin() as c:
        c.execute(text(f"DROP TABLE IF EXISTS {name}"))
    eng.dispose()


def _cfg(query, password=None):
    u = make_url(URL)
    return {**MAP, "db_type": "mysql", "host": u.host, "port": u.port or 3306, "database": u.database, "username": u.username,
            "password": password or u.password, "query": query}


def test_a_mysql_source_syncs_encrypts_its_password_and_is_idempotent(client, db, table):
    from app.models.core import Connector
    t, h = make_owner(client, "mysql")
    c = mk(client, t, h, connector_type="database", config=_cfg(f"SELECT id, ts, amt FROM {table}")).json()
    assert db.get(Connector, c["id"]).config["password"].startswith("gAAAA")
    r = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h)
    assert r.status_code == 200 and r.json()["rows_fetched"] == 2 and r.json()["failed"] == 0, r.text
    assert sorted(float(e.amount) for e in events_for(db, c["id"])) == [100.0, 250.5]
    again = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h)
    assert again.status_code == 200 and len(events_for(db, c["id"])) == 2


def test_the_mysql_session_is_read_only_even_if_a_write_got_past_the_query_checks(table):
    from app.connectors import database_connector as dc
    engine = dc._build_engine(dc.DatabaseConnectionConfig(**_cfg(f"SELECT id, ts, amt FROM {table}")))
    try:
        with engine.connect() as conn:
            for stmt in dc._READ_ONLY_SQL["mysql"]:
                conn.execute(text(stmt))
            with pytest.raises(Exception):
                conn.execute(text(f"DELETE FROM {table}"))
    finally:
        engine.dispose()


def test_a_write_query_is_refused_before_it_reaches_the_server(client, table):
    t, h = make_owner(client, "mysqlw")
    r = mk(client, t, h, connector_type="database", config=_cfg(f"DELETE FROM {table}"))
    assert r.status_code == 400 and "read-only" in r.text.lower()
    with create_engine(URL).connect() as conn:
        assert conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 2          # nothing was deleted


def test_a_wrong_password_is_a_clear_error_that_never_echoes_the_password(client, table):
    t, h = make_owner(client, "mysqlbad")
    c = mk(client, t, h, connector_type="database", config=_cfg(f"SELECT id, ts, amt FROM {table}", password="wrong-pass-xyz")).json()
    r = client.post(f"/api/connectors/{c['id']}/sync-database?tenant_id={t}", headers=h)
    assert r.status_code == 502 and "wrong-pass-xyz" not in r.text


def test_the_mysql_driver_is_marked_verified():
    assert DRIVERS["mysql"].verified is True
