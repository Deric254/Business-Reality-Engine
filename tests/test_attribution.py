"""The owner must be told WHERE a change happened (which branch, which product), not only that it did, and a source
that is simply a file someone uploaded must never be reported as 'out of date'."""
import datetime as dt

import pytest

from app.core.coverage import _status_for_connector
from app.core.db import SessionLocal
from app.core.detection import explain_deviation
from app.models.core import Connector, DataCoverageStatus
from tests.test_block_c import make_owner, mk

NOW = dt.datetime.utcnow()
CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total", "location_col": "Branch", "product_id_col": "Product"}


@pytest.fixture()
def db():
    s = SessionLocal(); yield s; s.close()


def _load(client, t, h, rows, cfg=CFG):
    cid = mk(client, t, h, name=f"s{abs(hash(str(rows))) % 9999}", connector_type="file", config=cfg).json()["id"]
    csv = "Date,Total,Branch,Product\n" + "".join(f"{NOW - dt.timedelta(days=d):%Y-%m-%d} 10:00,{a},{b},{p}\n" for d, a, b, p in rows)
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", csv.encode(), "text/csv")}, headers=h).status_code == 200


def _slump(client, t, h):
    """Last week vs the week before: Meru steady, Maua collapses; sugar collapses, oil steady."""
    _load(client, t, h, [(10, 1000, "Meru", "Oil"), (10, 1000, "Maua", "Sugar"), (10, 500, "Meru", "Sugar"), (10, 500, "Maua", "Oil"),
                         (3, 1000, "Meru", "Oil"), (3, 300, "Maua", "Sugar"), (3, 500, "Meru", "Sugar"), (3, 200, "Maua", "Oil")])


def test_alert_drivers_name_the_branch_and_product_behind_the_drop(client, db):
    t, h = make_owner(client, "attr1"); _slump(client, t, h)
    _, drivers = explain_deviation(db, t, "sale", NOW - dt.timedelta(days=7), NOW, NOW - dt.timedelta(days=14), NOW - dt.timedelta(days=7), rising=False)
    names = [d["driver"] for d in drivers]
    assert names[0] == "Branch: Maua fell" and any(n.startswith("Product: ") for n in names)
    maua = next(d for d in drivers if d["driver"] == "Branch: Maua fell")
    assert "1,500 -> 500 (-67%)" in maua["evidence"] and "100% of the fall" in maua["evidence"]
    assert "Branch: Meru fell" not in names                                                        # Meru did not fall


def test_questions_say_where_sales_moved_and_rank_branches(client):
    t, h = make_owner(client, "attr2"); _slump(client, t, h)
    ask = lambda q: client.post("/api/questions/ask", json={"tenant_id": t, "question": q}, headers=h).json()
    a = ask("How are sales this week?")
    assert a["intent"] == "sales" and "Where it moved: Branch Maua down 67%" in a["answer"]
    b = ask("Which branch is doing worst this week?")
    assert b["intent"] == "branches" and b["answered"]
    assert "Meru sold the most (1,500, +0% compared with the 7 days before)" in b["answer"] and "Maua the least (500, -67% compared with the 7 days before)" in b["answer"]
    assert "Biggest fall: Maua, down 67% from 1,500 to 500." in b["answer"]


def test_a_cost_rise_names_the_category_not_a_product(client, db):
    t, h = make_owner(client, "attr4")
    cfg = {**CFG, "entity": "expense", "event_type": "expense.created"}
    _load(client, t, h, [(10, 1000, "Meru", "Wages"), (10, 500, "Meru", "Transport"), (3, 1000, "Meru", "Wages"), (3, 1500, "Meru", "Transport")], cfg=cfg)
    _, drivers = explain_deviation(db, t, "expense", NOW - dt.timedelta(days=7), NOW, NOW - dt.timedelta(days=14), NOW - dt.timedelta(days=7), rising=True)
    assert drivers[0]["driver"] == "Category: Transport rose" and "categories that rose" in drivers[0]["evidence"]
    a = client.post("/api/questions/ask", json={"tenant_id": t, "question": "How are expenses this week?"}, headers=h).json()
    assert "Category Transport up 200%" in a["answer"]


def test_branch_question_with_a_single_branch_says_so(client):
    t, h = make_owner(client, "attr3")
    _load(client, t, h, [(3, 100, "Meru", "Oil"), (4, 200, "Meru", "Sugar")])
    a = client.post("/api/questions/ask", json={"tenant_id": t, "question": "Which branch is best?"}, headers=h).json()
    assert "only one branch (Meru)" in a["answer"] and "nothing to compare" in a["answer"]


def _connector(**kw):
    return Connector(tenant_id="t", name="x", connector_type=kw.pop("connector_type", "file"), config={}, **kw)


def test_uploaded_files_and_typed_entries_are_never_stale():
    old = NOW - dt.timedelta(days=40)
    assert _status_for_connector(_connector(), old, NOW) == DataCoverageStatus.recent
    assert _status_for_connector(_connector(), None, NOW) == DataCoverageStatus.not_connected


def test_a_source_we_pull_is_current_while_pulling_works_even_if_nothing_new_arrived():
    c = _connector(connector_type="url", sync_interval_minutes=60, expected_frequency="periodic", last_sync_status="success", last_attempt_at=NOW - dt.timedelta(minutes=30))
    assert _status_for_connector(c, NOW - dt.timedelta(days=2), NOW) == DataCoverageStatus.live                # closed on Sunday
    c.last_sync_status = "failed"
    assert _status_for_connector(c, NOW - dt.timedelta(minutes=5), NOW) == DataCoverageStatus.stale
    c.last_sync_status, c.last_attempt_at = "success", NOW - dt.timedelta(hours=10)
    assert _status_for_connector(c, NOW - dt.timedelta(minutes=5), NOW) == DataCoverageStatus.stale            # the scheduler stopped


def test_a_realtime_source_still_goes_stale_when_it_goes_quiet():
    c = _connector(connector_type="webhook", expected_frequency="realtime")
    assert _status_for_connector(c, NOW - dt.timedelta(hours=30), NOW) == DataCoverageStatus.stale
