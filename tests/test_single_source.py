"""One shared dataset: the same record from two sources counts once, places have one spelling, and disconnecting a
source removes its data without losing what another source still holds."""
import datetime as dt

import pytest

from app.core.db import SessionLocal
from app.models.core import BusinessEvent
from tests.conftest import bearer, register
from tests.test_block_c import make_owner, mk

DAY = dt.datetime.utcnow() - dt.timedelta(days=2)
CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total", "entity_id_col": "Receipt",
       "location_col": "Branch"}


@pytest.fixture()
def db():
    s = SessionLocal(); yield s; s.close()


def _csv(*rows):
    return ("Date,Receipt,Total,Branch\n" + "".join(f"{DAY:%Y-%m-%d} 10:00,{r},{a},{b}\n" for r, a, b in rows)).encode()


def _source(client, t, h, name, *rows, cfg=None):
    cid = mk(client, t, h, name=name, connector_type="file", config=cfg or CFG).json()["id"]
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", _csv(*rows), "text/csv")}, headers=h)
    assert r.status_code == 200, r.text
    return cid


def _revenue(client, t, h):
    return client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["revenue"]["current"]


def test_same_record_from_two_sources_counts_once(client):
    t, h = make_owner(client, "sot1")
    a = _source(client, t, h, "POS", ("R1", 100, "Meru"), ("R2", 200, "Meru"))
    b = _source(client, t, h, "Accounting", ("R1", 100, "Meru"), ("R2", 250, "Meru"), ("R3", 50, "Meru"))   # R1 same; R2 other amount; R3 new
    assert _revenue(client, t, h) == 100 + 200 + 250 + 50                                                   # R1 is not counted twice
    got = client.get(f"/api/connectors/contributions?tenant_id={t}", headers=h).json()
    assert got[a] == {"counted": 2, "matched": 0, "rejected": 0} and got[b] == {"counted": 2, "matched": 1, "rejected": 0}


def test_rows_without_a_shared_id_are_never_merged(client):
    t, h = make_owner(client, "sot2")
    cfg = {k: v for k, v in CFG.items() if k != "entity_id_col"}
    _source(client, t, h, "POS", ("x", 100, "Meru"), cfg=cfg)
    _source(client, t, h, "Sheet", ("y", 100, "Meru"), cfg=cfg)                                             # looks alike, cannot be proven the same
    assert _revenue(client, t, h) == 200


def test_places_get_one_spelling_across_sources(client, db):
    t, h = make_owner(client, "sot3")
    _source(client, t, h, "POS", ("R1", 10, "Nairobi"))
    b = _source(client, t, h, "Sheet", ("R9", 10, "  nairobi "), ("R8", 10, "NAIROBI"))
    assert {e.location for e in db.query(BusinessEvent).filter(BusinessEvent.tenant_id == t)} == {"Nairobi"}
    assert client.get(f"/api/connectors/contributions?tenant_id={t}", headers=h).json()[b]["counted"] == 2


def test_disconnect_removes_the_source_and_its_data_only(client, db):
    t, h = make_owner(client, "sot4")
    a = _source(client, t, h, "POS", ("R1", 100, "Meru"), ("R2", 200, "Meru"))
    b = _source(client, t, h, "Accounting", ("R1", 100, "Meru"), ("R3", 50, "Meru"))
    assert client.delete(f"/api/connectors/{a}?tenant_id={t}", headers=h).json() == {"removed_records": 2}
    assert [c["id"] for c in client.get(f"/api/connectors/?tenant_id={t}", headers=h).json()] == [b]
    assert db.query(BusinessEvent).filter(BusinessEvent.connector_id == a).count() == 0
    assert _revenue(client, t, h) == 100 + 50                                                              # R1 stays: Accounting still holds it
    assert client.get(f"/api/connectors/contributions?tenant_id={t}", headers=h).json()[b] == {"counted": 2, "matched": 0, "rejected": 0}
    assert client.delete(f"/api/connectors/{b}?tenant_id={t}", headers=h).status_code == 200
    assert _revenue(client, t, h) == 0 and db.query(BusinessEvent).filter(BusinessEvent.tenant_id == t).count() == 0


def test_three_sources_share_one_counted_copy_when_the_first_goes(client):
    t, h = make_owner(client, "sot5")
    a = _source(client, t, h, "POS", ("R1", 100, "Meru"))
    _source(client, t, h, "Accounting", ("R1", 100, "Meru"))
    _source(client, t, h, "Sheet", ("R1", 100, "Meru"))
    assert _revenue(client, t, h) == 100
    client.delete(f"/api/connectors/{a}?tenant_id={t}", headers=h)
    assert _revenue(client, t, h) == 100                                                                   # one copy takes over, not two


def test_only_the_owner_can_disconnect_and_only_in_their_business(client, tenant_a, tenant_b):
    t, h = make_owner(client, "sot6")
    a = _source(client, t, h, "POS", ("R1", 100, "Meru"))
    _, _, staff = register(client, t, role="staff")
    assert client.delete(f"/api/connectors/{a}?tenant_id={t}", headers=bearer(staff)).status_code == 403
    other_t, other_h = make_owner(client, "sot7")
    assert client.delete(f"/api/connectors/{a}?tenant_id={other_t}", headers=other_h).status_code == 404
    assert client.delete(f"/api/connectors/{a}?tenant_id={t}", headers=h).status_code == 200
