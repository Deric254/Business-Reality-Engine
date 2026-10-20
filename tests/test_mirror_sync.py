"""A tracked sheet is a mirror: what is edited, added or deleted there is edited, added or deleted here, and nothing a
person cannot see is thrown away."""
import datetime as dt
import json

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.models.core import Connector
from tests.conftest import bearer, register
from tests.test_block_c import make_owner, mk
from tests.test_url_connector import SHEET, server  # noqa: F401  (the live sheet and the server that serves it)

NOW = dt.datetime.utcnow()
CFG = {"auth_type": "none", "entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount", "location_col": "Branch"}


@pytest.fixture(autouse=True)
def allow_http(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "true")


def _day(n, hour=10):
    return f"{NOW - dt.timedelta(days=n):%Y-%m-%d} {hour:02d}:00"


def _sheet(*rows, header="Date,Amount,Branch"):
    SHEET["csv"] = header + "\n" + "".join(",".join(str(c) for c in r) + "\n" for r in rows)


def _source(client, server, name="own", cfg=None, path="/sheet.csv"):
    t, h = make_owner(client, name)
    cid = mk(client, t, h, connector_type="url", config={"url": server + path, **(cfg or CFG)}).json()["id"]
    return t, h, cid, lambda confirm=False, headers=None: client.post(
        f"/api/connectors/{cid}/sync-url?tenant_id={t}" + ("&confirm_replace=true" if confirm else ""), headers=headers or h)


def _revenue(client, t, h):
    return client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["revenue"]["current"]


def _count(t, cid):
    with engine.connect() as c:
        return c.execute(text("select count(*) from business_events where connector_id = :c"), {"c": cid}).scalar()


def test_adding_rows_to_the_sheet_adds_them(client, server):
    t, h, cid, sync = _source(client, server, "m1")
    _sheet((_day(3), 100, "Meru"), (_day(2), 200, "Meru"))
    assert sync().json()["new_rows"] == 2 and _revenue(client, t, h) == 300
    _sheet((_day(3), 100, "Meru"), (_day(2), 200, "Meru"), (_day(1), 400, "Maua"), (_day(1, 15), 50, "Maua"))
    r = sync().json()
    assert (r["new_rows"], r["updated_rows"], r["removed_rows"], r["duplicates"]) == (2, 0, 0, 2) and _revenue(client, t, h) == 750


def test_editing_a_figure_in_the_sheet_changes_it_here(client, server):
    t, h, cid, sync = _source(client, server, "m2")
    _sheet((_day(3), 100, "Meru"), (_day(2), 200, "Meru"), (_day(1), 300, "Meru"))
    sync()
    _sheet((_day(3), 100, "Meru"), (_day(2), 250, "Meru"), (_day(1), 300, "Meru"))             # 200 became 250; no id column
    r = sync().json()
    assert (r["new_rows"], r["removed_rows"]) == (1, 1) and _revenue(client, t, h) == 650 and _count(t, cid) == 3          # replaced, not doubled


def test_editing_a_record_with_an_id_updates_it_in_place(client, server):
    t, h, cid, sync = _source(client, server, "m3", cfg={**CFG, "entity_id_col": "Receipt"})
    _sheet(*[(_day(3), "R1", 100, "Meru"), (_day(2), "R2", 200, "Meru")], header="Date,Receipt,Amount,Branch")
    sync()
    _sheet(*[(_day(3), "R1", 100, "Meru"), (_day(2), "R2", 999, "Meru")], header="Date,Receipt,Amount,Branch")
    r = sync().json()
    assert (r["updated_rows"], r["new_rows"], r["removed_rows"]) == (1, 0, 0) and _revenue(client, t, h) == 1099 and _count(t, cid) == 2


def test_deleting_a_row_from_the_sheet_removes_it(client, server):
    t, h, cid, sync = _source(client, server, "m4")
    _sheet((_day(3), 100, "Meru"), (_day(2), 200, "Meru"), (_day(1), 300, "Meru"))
    sync()
    _sheet((_day(3), 100, "Meru"), (_day(1), 300, "Meru"))
    assert sync().json()["removed_rows"] == 1 and _revenue(client, t, h) == 400


def test_an_unchanged_sheet_changes_nothing_and_still_counts_as_checked(client, server, db_session=None):
    t, h, cid, sync = _source(client, server, "m5")
    _sheet((_day(3), 100, "Meru"), (_day(2), 200, "Meru"))
    sync()
    db = SessionLocal(); db.get(Connector, cid).last_sync_at = NOW - dt.timedelta(days=3); db.commit()
    r = sync().json()
    assert (r["new_rows"], r["updated_rows"], r["removed_rows"], r["duplicates"]) == (0, 0, 0, 2)
    db.expire_all(); c = db.get(Connector, cid)
    assert c.last_sync_status == "success" and (dt.datetime.utcnow() - c.last_sync_at).total_seconds() < 60
    db.close()


def test_a_sheet_that_suddenly_looks_completely_different_is_not_applied_until_confirmed(client, server):
    t, h, cid, sync = _source(client, server, "m6")
    _sheet(*[(_day(1 + i % 20), 100 + i, "Meru") for i in range(30)])
    sync()
    before = _revenue(client, t, h)
    _sheet((_day(1), 5, "Other"), (_day(2), 6, "Other"))                                       # wrong tab, or someone emptied it
    r = sync()
    assert r.status_code == 409 and r.json()["detail"].startswith("This source changed a lot") and "30 of its 30" in r.json()["detail"]
    assert _revenue(client, t, h) == before and _count(t, cid) == 30                            # nothing was touched
    _, _, staff = register(client, t, role="staff")
    assert sync(confirm=True, headers=bearer(staff)).status_code == 403                         # only the owner can replace
    assert sync(confirm=True).json()["removed_rows"] == 30 and _revenue(client, t, h) == 11


def test_a_json_api_is_never_mirrored_because_it_may_only_show_the_latest_records(client, server):
    cfg = {"auth_type": "none", "entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount", "entity_id_col": "Id"}
    t, h, cid, sync = _source(client, server, "m7", cfg=cfg, path="/sheet.json")
    SHEET["json"] = json.dumps([{"Id": i, "Date": _day(i), "Amount": 10 * i} for i in (1, 2, 3)])
    sync()
    SHEET["json"] = json.dumps([{"Id": 3, "Date": _day(3), "Amount": 30}])                     # the API now lists only the newest
    r = sync().json()
    assert r["removed_rows"] == 0 and _count(t, cid) == 3


def test_a_rejected_record_is_named_with_its_reason_and_fixing_it_at_the_source_fixes_it_here(client, server):
    t, h, cid, sync = _source(client, server, "m8")
    future = f"{NOW + dt.timedelta(days=30):%Y-%m-%d} 09:00"
    _sheet((_day(2), 100, "Meru"), (future, 5000, "Maua"))
    r = sync().json()
    assert r["rejected_total"] == 1 and r["rejected_examples"][0]["reasons"] == ["the date is in the future"]
    assert r["rejected_examples"][0]["amount"] == 5000.0 and r["rejected_examples"][0]["branch"] == "Maua" and _revenue(client, t, h) == 100
    listed = client.get(f"/api/connectors/{cid}/rejected?tenant_id={t}", headers=h).json()
    assert listed["total"] == 1 and listed["records"][0]["when"].startswith(f"{NOW + dt.timedelta(days=30):%Y-%m-%d}")
    _sheet((_day(2), 100, "Meru"), (_day(1), 5000, "Maua"))                                    # the date is corrected in the sheet
    r = sync().json()
    assert (r["rejected_total"], r["updated_rows"], r["removed_rows"], r["new_rows"]) == (0, 0, 1, 1) and _revenue(client, t, h) == 5100


def test_money_in_and_out_columns_work_for_a_tracked_sheet(client, server):
    cfg = {"auth_type": "none", "entity": "sale", "event_type": "sale.created", "timestamp_col": "Completion Time", "amount_in_col": "Paid In", "amount_out_col": "Withdrawn"}
    t, h, cid, sync = _source(client, server, "m9", cfg=cfg)
    _sheet((_day(2), 1500, ""), (_day(1), "", 400), header="Completion Time,Paid In,Withdrawn")
    sync()
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] == 1500 and k["expenses"]["current"] == 400


def test_times_without_a_timezone_are_the_business_clock_not_utc(client, server):
    t, h, cid, sync = _source(client, server, "m10")
    assert client.patch("/api/tenants/me", json={"timezone": "Africa/Nairobi"}, headers=h).status_code == 200
    soon = (NOW + dt.timedelta(hours=1)).replace(microsecond=0)                                # one hour ahead of UTC: still in the past in Nairobi (UTC+3)
    _sheet((f"{soon:%Y-%m-%d %H:%M:%S}", 700, "Meru"))
    r = sync().json()
    assert r["rejected_total"] == 0 and r["new_rows"] == 1
    with engine.connect() as c:
        stored = c.execute(text("select timestamp from business_events where connector_id = :c"), {"c": cid}).scalar()
    assert stored == soon.replace(second=soon.second) - dt.timedelta(hours=3)


def test_a_leftover_lock_cannot_block_a_source_forever(client, server):
    t, h, cid, sync = _source(client, server, "m11")
    _sheet((_day(2), 100, "Meru"))
    sync()                                                                                    # a first sync sets last_attempt_at
    holder = engine.connect()
    holder.execute(text("select pg_advisory_lock(hashtext(:k))"), {"k": f"sync:{cid}"}); holder.commit()
    try:
        assert sync().status_code == 409                                                       # a sync that just ran: assume it is still running
        db = SessionLocal(); db.get(Connector, cid).last_attempt_at = NOW - dt.timedelta(minutes=30); db.commit(); db.close()
        assert sync().status_code == 200                                                       # nothing has started for 30 minutes: the lock is stale
    finally:
        holder.execute(text("select pg_advisory_unlock(hashtext(:k))"), {"k": f"sync:{cid}"}); holder.commit(); holder.close()


def test_uploading_a_corrected_file_with_ids_updates_and_replace_makes_the_file_the_whole_truth(client):
    t, h = make_owner(client, "m12")
    cid = mk(client, t, h, connector_type="file", config={**CFG, "entity_id_col": "Receipt"}).json()["id"]
    up = lambda body, **extra: client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t, **extra}, files={"file": ("s.csv", body.encode(), "text/csv")}, headers=h)
    head = "Date,Receipt,Amount,Branch\n"
    assert up(head + f"{_day(3)},R1,100,Meru\n{_day(2)},R2,200,Meru\n").json()["new_rows"] == 2
    r = up(head + f"{_day(3)},R1,100,Meru\n{_day(2)},R2,260,Meru\n{_day(1)},R3,300,Meru\n").json()          # R2 corrected, R3 new
    assert (r["updated_rows"], r["new_rows"], r["removed_rows"]) == (1, 1, 0) and _revenue(client, t, h) == 660
    r = up(head + f"{_day(1)},R3,300,Meru\n", replace="true").json()                                        # this file is everything
    assert r["removed_rows"] == 2 and _revenue(client, t, h) == 300
    _, _, staff = register(client, t, role="staff")
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t, "replace": "true"}, files={"file": ("s.csv", b"x", "text/csv")}, headers=bearer(staff)).status_code == 403


def test_a_record_another_source_also_held_survives_when_it_is_deleted_here(client, server):
    t, h, cid, sync = _source(client, server, "m13", cfg={**CFG, "entity_id_col": "Receipt"})
    other = mk(client, t, h, name="Till", connector_type="file", config={**CFG, "entity_id_col": "Receipt"}).json()["id"]
    _sheet((_day(3), "R1", 100, "Meru"), (_day(2), "R2", 200, "Meru"), header="Date,Receipt,Amount,Branch")
    sync()
    csv = f"Date,Receipt,Amount,Branch\n{_day(3)},R1,100,Meru\n"
    client.post(f"/api/connectors/{other}/upload", data={"tenant_id": t}, files={"file": ("t.csv", csv.encode(), "text/csv")}, headers=h)
    assert _revenue(client, t, h) == 300                                                       # R1 counted once across the two
    _sheet((_day(2), "R2", 200, "Meru"), header="Date,Receipt,Amount,Branch")                  # R1 deleted from the sheet
    assert sync().json()["removed_rows"] == 1 and _revenue(client, t, h) == 300                # the till still holds R1, so it counts
