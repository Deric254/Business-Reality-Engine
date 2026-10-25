"""An owner who is losing money must be told, from the first days of data; a flat business must not be told its sales
fell just because today is not over."""
import datetime as dt
import threading

import pytest

from app.core import detection_kick
from app.core.db import SessionLocal
from app.core.detectors import run_tenant_cycle
from app.core.scheduler import _metrics_for_tenant
from tests.test_block_c import make_owner, mk

NOW = dt.datetime.utcnow()
SALES = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Amount"}
COSTS = {"entity": "expense", "event_type": "expense.created", "timestamp_col": "Date", "amount_col": "Amount"}


def _load(client, t, h, name, cfg, days, per_day):
    cid = mk(client, t, h, name=name, connector_type="file", config=cfg).json()["id"]
    rows = "".join(f"{NOW - dt.timedelta(days=d):%Y-%m-%d} 12:00,{per_day(d)}\n" for d in range(days, 0, -1))
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", ("Date,Amount\n" + rows).encode(), "text/csv")}, headers=h).status_code == 200


def _cycle(client, t, h):
    db = SessionLocal(); run_tenant_cycle(db, t, _metrics_for_tenant(db, t)); db.close()
    return client.get(f"/api/alerts/?tenant_id={t}", headers=h).json()


def _shop(client, name, days, sales, costs):
    t, h = make_owner(client, name)
    _load(client, t, h, "POS", SALES, days, sales)
    if costs:
        _load(client, t, h, "Costs", COSTS, days, costs)
    return t, h


@pytest.mark.parametrize("days", [3, 10, 56])
def test_spending_far_above_sales_is_flagged_whatever_the_history(client, days):
    t, h = _shop(client, f"loss{days}", days, lambda d: 3000, lambda d: 6000)
    loss = [a for a in _cycle(client, t, h) if a["title"] == "Spending exceeds sales"]
    assert len(loss) == 1 and loss[0]["severity"] == "high"
    detail = client.get(f"/api/alerts/{loss[0]['id']}/detail?tenant_id={t}", headers=h).json()
    assert "a loss of" in detail["what_happened"] or "more than you sold" in detail["what_happened"]


def test_a_small_loss_is_flagged_less_urgently(client):
    t, h = _shop(client, "loss-small", 10, lambda d: 3000, lambda d: 3300)
    assert [a["severity"] for a in _cycle(client, t, h) if a["title"] == "Spending exceeds sales"] == ["medium"]


def test_no_loss_alert_when_no_sales_are_recorded_at_all(client):
    t, h = make_owner(client, "loss-nosales")
    _load(client, t, h, "Costs", COSTS, 10, lambda d: 5000)
    assert not [a for a in _cycle(client, t, h) if a["title"] == "Spending exceeds sales"]          # the sales source is probably not connected


def test_a_profitable_business_gets_no_loss_alert_and_the_alert_clears_when_it_recovers(client):
    t, h = _shop(client, "loss-recover", 10, lambda d: 3000, lambda d: 6000)
    assert any(a["title"] == "Spending exceeds sales" for a in _cycle(client, t, h))
    _load(client, t, h, "Big sale", SALES, 10, lambda d: 9000)                                      # sales now far above spending
    after = _cycle(client, t, h)
    assert not [a for a in after if a["title"] == "Spending exceeds sales" and a["state"] in ("new", "open", "acknowledged")]


def test_a_flat_profitable_week_is_not_called_a_drop_because_today_is_unfinished(client):
    t, h = _shop(client, "flat", 56, lambda d: 5000, lambda d: 2000)                                # data ends yesterday, as with any sync
    titles = [a["title"] for a in _cycle(client, t, h)]
    assert not [x for x in titles if "down" in x.lower() or "fall" in x.lower() or x == "Spending exceeds sales"], titles


def test_a_real_collapse_is_still_flagged(client):
    t, h = _shop(client, "collapse", 56, lambda d: 5000 if d > 7 else 2000, lambda d: 1000)
    assert any("down" in a["title"].lower() for a in _cycle(client, t, h))


def test_new_records_trigger_detection_soon_after_and_a_burst_costs_one_extra_run(client, monkeypatch):
    runs, first = [], threading.Event()
    monkeypatch.setenv("DETECT_AFTER_INGEST", "true")
    monkeypatch.setattr(detection_kick, "MIN_GAP_SECONDS", 0.3)
    monkeypatch.setattr(detection_kick, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(detection_kick, "_execute", lambda tenant_id: (runs.append(tenant_id), first.set()))
    t, h = make_owner(client, "kick")
    _load(client, t, h, "POS", SALES, 5, lambda d: 100)
    assert first.wait(5) and runs == [t]                                                            # new records: a run, straight away
    for _ in range(6):                                                                              # a burst of arrivals
        detection_kick.kick({t})
    threading.Event().wait(1.0)
    assert runs == [t, t]                                                                           # one more run for the whole burst


def test_detection_after_new_data_can_be_turned_off(client, monkeypatch):
    runs = []
    monkeypatch.setenv("DETECT_AFTER_INGEST", "false")
    monkeypatch.setattr(detection_kick, "_execute", lambda tenant_id: runs.append(tenant_id))
    t, h = make_owner(client, "kick-off")
    _load(client, t, h, "POS", SALES, 5, lambda d: 100)
    threading.Event().wait(0.3)
    assert runs == []
