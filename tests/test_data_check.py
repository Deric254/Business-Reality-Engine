"""When the dashboard shows nothing, the app says exactly why."""
import datetime as dt
import subprocess
import sys

import pytest

from app.core import schema
from tests.test_block_c import make_owner, mk
from tests.test_mirror_sync import SHEET, _day, _sheet, server  # noqa: F401

CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount", "location_col": "Branch"}


@pytest.fixture(autouse=True)
def allow_http(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "true")


def _check(client, t, h, days=30):
    return client.get(f"/api/business/data-check?tenant_id={t}&period_days={days}", headers=h).json()


def _texts(r, level=None):
    return [f["text"] for f in r["findings"] if level in (None, f["level"])]


def test_nothing_connected_says_so(client):
    t, h = make_owner(client, "dc1")
    r = _check(client, t, h)
    assert r["verdict"] == "problem" and r["headline"].startswith("Something is stopping") and "No source is connected yet." in _texts(r)


def test_a_source_that_was_never_synced_and_one_that_returned_nothing_are_named(client, server):
    t, h = make_owner(client, "dc2")
    cid = mk(client, t, h, name="My sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    r = _check(client, t, h)
    assert any(f["source"] == "My sheet" and "never been synced" in f["text"] for f in r["findings"])
    _sheet()                                                                                     # only a header: the sheet is empty
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    r = _check(client, t, h)
    assert r["sources"][0]["status"] == "failed" and r["sources"][0]["error"]
    assert any("The last sync failed" in x for x in _texts(r, "problem"))


def test_a_failed_sync_keeps_the_reason_and_a_good_one_clears_it(client, server):
    t, h = make_owner(client, "dc3")
    cid = mk(client, t, h, connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    SHEET["csv"] = "Date,Amount,Branch\nnot a date,5,Meru\nalso not,6,Meru\n"
    assert client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h).status_code == 400
    src = _check(client, t, h)["sources"][0]
    assert src["status"] == "failed" and "mapping is probably wrong" in src["error"]
    assert client.get(f"/api/connectors/?tenant_id={t}", headers=h).json()[0]["last_sync_error"] == src["error"]
    _sheet((_day(2), 100, "Meru"))
    assert client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h).status_code == 200
    src = _check(client, t, h)["sources"][0]
    assert src["status"] == "success" and src["error"] is None and src["counted"] == 1


def test_records_left_out_are_counted_with_their_reason(client, server):
    t, h = make_owner(client, "dc4")
    cid = mk(client, t, h, name="Sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((f"{dt.datetime.utcnow() + dt.timedelta(days=40):%Y-%m-%d} 10:00", 900, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    r = _check(client, t, h)
    assert any("1 record left out of your totals: the date is in the future (1)" in x for x in _texts(r, "problem"))     # all of it was left out


def test_data_that_is_older_than_the_period_is_explained_and_a_wider_period_is_suggested(client, server):
    t, h = make_owner(client, "dc5")
    cid = mk(client, t, h, name="Old sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((_day(60), 100, "Meru"), (_day(75), 200, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    r = _check(client, t, h)
    assert r["headline"] == "Your data is there, but not in the period shown." and r["range"]["in_view"] == 0 and r["range"]["suggest_days"] == 90
    assert any("none of your records are in it" in x for x in _texts(r)) and any("none fall in the last 30 days" in x for x in _texts(r))
    wide = _check(client, t, h, days=90)
    assert wide["range"]["in_view"] == 2 and wide["verdict"] == "ok"                              # the AI note is information, not a problem


def test_a_mapping_that_found_no_amounts_is_called_out(client, server):
    t, h = make_owner(client, "dc6")
    cid = mk(client, t, h, name="Sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **{**CFG, "amount_col": "Missing"}}).json()["id"]
    _sheet((_day(2), 100, "Meru"), (_day(1), 200, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    assert any("have no amount" in x for x in _texts(_check(client, t, h), "warning"))


def test_a_healthy_business_is_told_so_and_the_ai_status_is_reported(client, server):
    t, h = make_owner(client, "dc7")
    cid = mk(client, t, h, name="Sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((_day(2), 100, "Meru"), (_day(1), 200, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    r = _check(client, t, h)
    assert r["range"]["in_view"] == 2 and not _texts(r, "problem") and not _texts(r, "warning")
    assert r["ai_connected"] is False and any("AI assistant is not connected" in x for x in _texts(r, "info"))


def test_the_check_is_private_to_the_business(client):
    t, h = make_owner(client, "dc8")
    _, other_h = make_owner(client, "dc9")
    assert client.get(f"/api/business/data-check?tenant_id={t}", headers=other_h).status_code in (401, 403, 404)


def test_the_expected_schema_revision_is_the_newest_migration():
    head = subprocess.run([sys.executable, "-m", "alembic", "heads"], capture_output=True, text=True).stdout.split()[0]
    assert head == schema.EXPECTED_REVISION                      # add a migration, update app/core/schema.py


def test_health_reports_whether_the_database_is_current(client):
    body = client.get("/api/health/").json()
    assert body["status"] == "ok" and body["schema"]["current"] is True


def test_the_last_7_days_view_suggests_only_a_wider_period(client, server):
    t, h = make_owner(client, "dc7")
    cid = mk(client, t, h, name="Recent sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((_day(10), 100, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    narrow = _check(client, t, h, days=7)
    assert narrow["range"]["in_view"] == 0 and narrow["range"]["suggest_days"] == 30
    assert _check(client, t, h, days=30)["range"]["suggest_days"] is None          # something is in view: nothing to suggest


def test_a_negative_amount_is_counted_and_only_hinted_at(client, server):
    """A refund or credit is real data: it stays in the totals, and the check points it out without calling it a problem."""
    t, h = make_owner(client, "dcneg")
    cid = mk(client, t, h, name="Sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((_day(3), 1000, "Meru"), (_day(2), -200, "Meru"), (_day(1), 500, "Meru"))
    assert client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h).status_code == 200
    r = _check(client, t, h)
    assert r["sources"][0]["counted"] == 3 and r["sources"][0]["rejected"] == 0                 # nothing was left out
    assert any("1 sale or expense record has a negative amount" in x for x in _texts(r, "info"))
    assert r["verdict"] == "ok"                                                                 # a hint, not a warning
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] == 1300                                                      # 1000 - 200 + 500


def test_each_source_lists_its_totals_for_the_period_so_they_can_be_compared_with_the_app(client, server):
    t, h = make_owner(client, "dc9")
    cid = mk(client, t, h, name="Shop sheet", connector_type="url", config={"url": server + "/sheet.csv", "auth_type": "none", **CFG}).json()["id"]
    _sheet((_day(2), 100, "Meru"), (_day(1), 250, "Meru"), (_day(60), 999, "Meru"))
    client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    r = _check(client, t, h)
    src = r["sources"][0]
    assert src["sales"] == 350 and src["expenses"] == 0 and src["refunds"] == 0           # the record from 60 days ago is outside the period
    assert "currency" in r
    note = next(f for f in r["findings"] if "business's own time" in f["text"])
    assert "not subtracted again" in note["action"]
