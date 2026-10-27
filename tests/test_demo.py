"""Sample businesses: each hides a real problem that the detectors find; they load only into empty businesses and come out
again cleanly; the master account can create ready-made demo businesses with logins."""
import datetime as dt

import pytest
from sqlalchemy import text

from app.core import demo
from app.core.db import engine
from tests.conftest import bearer, register
from tests.test_block_c import make_owner, mk
from tests.test_licensing import master  # noqa: F401  (a fixture)


def _nairobi(t):
    with engine.begin() as c:
        c.execute(text("update tenants set timezone = 'Africa/Nairobi' where id = :t"), {"t": t})


def _titles(t):
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("select title from alerts where tenant_id = :t"), {"t": t})]


def _load(client, t, h, kind):
    return client.post("/api/demo/samples", json={"tenant_id": t, "kind": kind}, headers=h)


EXPECTED = {
    "duka": "Maua sales are down",
    "pharmacy": "Stock purchases spending is up",
    "workshop": "Kamau Hardware is",
}


@pytest.mark.parametrize("kind", [k.id for k in demo.KINDS])
def test_each_sample_shows_the_problem_it_was_built_around(client, kind):
    t, h = make_owner(client, f"demo-{kind}")
    _nairobi(t)
    r = _load(client, t, h, kind)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind and r.json()["events"] > 500
    assert any(EXPECTED[kind] in title for title in _titles(t)), _titles(t)
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] > 0 and k["expenses"]["current"] > 0
    with engine.connect() as c:
        latest = c.execute(text("select max(timestamp) from business_events where tenant_id = :t"), {"t": t}).scalar()
    assert latest < dt.datetime.utcnow()                                    # nothing in the future, and it ends yesterday or earlier
    assert latest > dt.datetime.utcnow() - dt.timedelta(days=3)


def test_every_listed_sample_has_a_builder_and_the_list_is_served(client):
    t, h = make_owner(client, "demo-list")
    body = client.get(f"/api/demo/samples?tenant_id={t}", headers=h).json()
    assert [s["id"] for s in body["samples"]] == [k.id for k in demo.KINDS] and body["loaded"] is False
    assert set(EXPECTED) == {k.id for k in demo.KINDS}                      # a new sample must come with its own test expectation


def test_a_sample_loads_once_into_an_empty_business_and_comes_out_again(client):
    t, h = make_owner(client, "demo-cycle")
    assert _load(client, t, h, "nope").status_code == 400
    assert _load(client, t, h, "duka").status_code == 200
    assert client.get(f"/api/demo/samples?tenant_id={t}", headers=h).json()["loaded"] is True
    assert _load(client, t, h, "pharmacy").status_code == 409               # not into a business that already has data
    assert client.delete(f"/api/demo/samples?tenant_id={t}", headers=h).status_code == 200
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()
    assert k["revenue"]["current"] == 0                                     # no stale numbers survive the removal
    assert client.get(f"/api/demo/samples?tenant_id={t}", headers=h).json()["loaded"] is False
    assert client.delete(f"/api/demo/samples?tenant_id={t}", headers=h).status_code == 404
    assert _load(client, t, h, "pharmacy").status_code == 200               # and another can take its place


def test_sample_data_is_never_removed_once_real_data_is_mixed_in(client):
    t, h = make_owner(client, "demo-mixed")
    assert _load(client, t, h, "duka").status_code == 200
    conn = mk(client, t, h, connector_type="webhook", config={"entity": "sale", "event_type": "sale.created", "amount_path": "a", "entity_id_path": "i"}).json()
    assert client.post(conn["webhook_url"], json={"i": "real-1", "a": 500}).status_code == 200
    assert client.delete(f"/api/demo/samples?tenant_id={t}", headers=h).status_code == 409


def test_only_the_owner_of_a_business_can_load_or_remove_its_sample(client, tenant_a, tenant_b):
    _, _, owner = register(client, tenant_a, role="owner")
    _, _, staff = register(client, tenant_a, role="staff")
    _, _, other = register(client, tenant_b, role="owner")
    assert _load(client, tenant_a, bearer(staff), "duka").status_code == 403
    assert _load(client, tenant_a, bearer(other), "duka").status_code == 403           # another business's owner
    assert client.post("/api/demo/samples", json={"tenant_id": tenant_a, "kind": "duka"}).status_code == 401
    assert _load(client, tenant_a, bearer(owner), "duka").status_code == 200
    assert client.delete(f"/api/demo/samples?tenant_id={tenant_a}", headers=bearer(staff)).status_code == 403
    assert client.get(f"/api/demo/samples?tenant_id={tenant_a}", headers=bearer(staff)).status_code == 200      # anyone in the business may look


# ---- ready-made demo businesses (master account) ---------------------------------------------------------------------

def test_master_creates_demo_businesses_with_working_logins_and_free_revocable_licences(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    r = client.post("/api/master/demo-businesses", json={}, headers=master[1])
    assert r.status_code == 200, r.text
    made = r.json()["businesses"]
    assert [m["kind"] for m in made] == [k.id for k in demo.KINDS]
    assert len({m["tenant_id"] for m in made}) == len(made) and len({m["email"] for m in made}) == len(made)
    for m in made:
        login = client.post("/api/auth/login", json={"email": m["email"], "password": m["password"]})
        assert login.status_code == 200, login.text                                    # a free licence is never paused
        assert m["events"] > 500
        assert client.get(f"/api/alerts/?tenant_id={m['tenant_id']}", headers=bearer(login.json())).status_code == 200
    first = made[0]
    assert client.post(f"/api/master/licences/{first['licence_id']}/revoke", json={}, headers=master[1]).status_code == 200
    assert client.post("/api/auth/login", json={"email": first["email"], "password": first["password"]}).status_code == 403


def test_master_can_ask_for_just_some_demo_businesses_and_others_cannot_ask_at_all(client, master):
    r = client.post("/api/master/demo-businesses", json={"kinds": ["pharmacy", "pharmacy"]}, headers=master[1])
    assert r.status_code == 200 and [m["kind"] for m in r.json()["businesses"]] == ["pharmacy"]
    assert client.post("/api/master/demo-businesses", json={"kinds": ["nope"]}, headers=master[1]).status_code == 400
    assert client.post("/api/master/demo-businesses", json={"kinds": []}, headers=master[1]).status_code == 422
    _, ordinary = make_owner(client, "not-master")
    assert client.post("/api/master/demo-businesses", json={}, headers=ordinary).status_code == 404
    assert client.post("/api/master/demo-businesses", json={}).status_code == 404
