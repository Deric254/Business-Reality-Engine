"""The guided connect flow's server side: testing a live source without storing anything, and replacing a lost link."""
import datetime as dt

import pytest
from sqlalchemy import text

from app.core.db import engine
from tests.conftest import bearer, register
from tests.test_block_c import make_owner, mk


def _nairobi(t):
    with engine.begin() as c:
        c.execute(text("update tenants set timezone = 'Africa/Nairobi' where id = :t"), {"t": t})


def _count(connector_id):
    with engine.connect() as c:
        return c.execute(text("select count(*) from business_events where connector_id = :c"), {"c": connector_id}).scalar()


def _mpesa(client, t, h):
    return mk(client, t, h, connector_type="webhook", name="M-Pesa payments", config={"preset": "mpesa_c2b"}).json()


def test_the_test_shows_what_a_payment_would_become_and_stores_nothing(client):
    t, h = make_owner(client, "flow1")
    _nairobi(t)
    c = _mpesa(client, t, h)
    r = client.post(f"/api/connectors/{c['id']}/test-event?tenant_id={t}", headers=h)
    assert r.status_code == 200, r.text
    got = r.json()["would_create"]
    assert (got["entity"], got["amount"], got["customer_id"]) == ("sale", 150.0, "254700000000")
    assert got["time"].endswith("+03:00")                                          # shown on the business's own clock
    assert abs(dt.datetime.fromisoformat(got["time"]).astimezone(dt.timezone.utc).replace(tzinfo=None) - dt.datetime.utcnow()).total_seconds() < 120
    assert _count(c["id"]) == 0                                                    # nothing was stored


def test_the_test_accepts_an_example_and_explains_a_bad_one(client):
    t, h = make_owner(client, "flow2")
    c = _mpesa(client, t, h)
    good = client.post(f"/api/connectors/{c['id']}/test-event?tenant_id={t}", json={"payload": {"TransID": "X1", "TransTime": "20261020123045", "TransAmount": "99.50"}}, headers=h)
    assert good.status_code == 200 and good.json()["would_create"]["amount"] == 99.5 and good.json()["would_create"]["entity_id"] == "X1"
    bad = client.post(f"/api/connectors/{c['id']}/test-event?tenant_id={t}", json={"payload": {"TransID": "X1", "TransTime": "yesterday", "TransAmount": "1"}}, headers=h)
    assert bad.status_code == 400 and "TransTime" in bad.json()["detail"]


def test_a_live_feed_that_is_not_m_pesa_needs_an_example_and_other_sources_cannot_be_tested(client):
    t, h = make_owner(client, "flow3")
    feed = mk(client, t, h, connector_type="webhook", config={"entity": "sale", "event_type": "sale.created", "amount_path": "a", "entity_id_path": "i"}).json()
    assert client.post(f"/api/connectors/{feed['id']}/test-event?tenant_id={t}", headers=h).status_code == 400
    shown = client.post(f"/api/connectors/{feed['id']}/test-event?tenant_id={t}", json={"payload": {"i": "z", "a": 7}}, headers=h)
    assert shown.status_code == 200 and shown.json()["would_create"]["amount"] == 7.0
    sheet = mk(client, t, h, connector_type="file", config={}).json()
    assert client.post(f"/api/connectors/{sheet['id']}/test-event?tenant_id={t}", headers=h).status_code == 400


def test_only_the_owner_of_that_business_can_test_or_replace_a_link(client, tenant_a, tenant_b):
    _, _, owner = register(client, tenant_a, role="owner")
    _, _, staff = register(client, tenant_a, role="staff")
    _, _, other = register(client, tenant_b, role="owner")
    c = _mpesa(client, tenant_a, bearer(owner))
    for who, expected in ((staff, 403), (other, 403)):
        assert client.post(f"/api/connectors/{c['id']}/test-event?tenant_id={tenant_a}", headers=bearer(who)).status_code == expected
        assert client.post(f"/api/connectors/{c['id']}/new-link?tenant_id={tenant_a}", headers=bearer(who)).status_code == expected
    assert client.post(f"/api/connectors/{c['id']}/new-link?tenant_id={tenant_a}").status_code == 401
    # a business cannot reach another business's source by naming its own tenant
    assert client.post(f"/api/connectors/{c['id']}/new-link?tenant_id={tenant_b}", headers=bearer(other)).status_code == 404


def test_a_lost_link_is_replaced_the_old_one_stops_and_the_new_one_works(client):
    t, h = make_owner(client, "flow5")
    c = _mpesa(client, t, h)
    old = c["webhook_url"]
    payload = {"TransID": "R1", "TransTime": "20261020123045", "TransAmount": "10.00"}
    assert client.post(old, json=payload).status_code == 200
    r = client.post(f"/api/connectors/{c['id']}/new-link?tenant_id={t}", headers=h)
    assert r.status_code == 200 and r.json()["webhook_url"] != old and r.json()["webhook_url"].startswith(f"/api/webhooks/{c['id']}/")
    assert client.post(old, json={**payload, "TransID": "R2"}).status_code == 404                  # the old link no longer works
    assert client.post(r.json()["webhook_url"], json={**payload, "TransID": "R3"}).status_code == 200
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from audit_log where action = 'connector.new_link' and tenant_id = :t"), {"t": t}).scalar() == 1


def test_only_a_live_feed_has_a_link_to_replace(client):
    t, h = make_owner(client, "flow6")
    sheet = mk(client, t, h, connector_type="file", config={}).json()
    assert client.post(f"/api/connectors/{sheet['id']}/new-link?tenant_id={t}", headers=h).status_code == 400
