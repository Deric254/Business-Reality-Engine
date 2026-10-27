"""Payment controls access, revoked licences can be reinstated, private hosts are blocked by default in production,
and the shared service key cannot write for any business on a production server."""

import pytest
from sqlalchemy import text

from app.core import cache, netguard
from app.core.db import engine
from tests.conftest import SERVICE, bearer, register
from tests.test_licensing import activate, issue, master  # noqa: F401  (master is a fixture)


def _backdate_activation(lic_id, days):
    with engine.begin() as c:
        c.execute(text("update licences set redeemed_at = now() - make_interval(days => :d) where id = :i"), {"d": days, "i": lic_id})


def _drop_status_cache():
    cache.invalidate_prefix("licence:")


# ---- 1. payment controls access ----------------------------------------------------------------------------------

def test_a_paying_business_that_never_pays_is_paused_after_the_grace_period_and_resumes_when_paid(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1], price=5)
    tok = activate(client, k["key"]).json()
    h = bearer(tok)
    assert client.get("/api/tenants/me", headers=h).status_code == 200               # inside the grace period
    _backdate_activation(k["id"], 30); _drop_status_cache()
    r = client.get("/api/tenants/me", headers=h)
    assert r.status_code == 403 and "overdue" in r.json()["detail"]
    assert client.post(f"/api/master/licences/{k['id']}/payments", json={"amount": 5, "months": 1}, headers=master[1]).status_code == 200
    _drop_status_cache()
    assert client.get("/api/tenants/me", headers=h).status_code == 200               # paid: access resumes at once


def test_grace_period_is_configurable_and_zero_means_none(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1], price=5)
    h = bearer(activate(client, k["key"]).json())
    _backdate_activation(k["id"], 3); _drop_status_cache()
    monkeypatch.setenv("LICENCE_GRACE_DAYS", "7")
    assert client.get("/api/tenants/me", headers=h).status_code == 200
    monkeypatch.setenv("LICENCE_GRACE_DAYS", "0"); _drop_status_cache()
    assert client.get("/api/tenants/me", headers=h).status_code == 403


def test_a_free_licence_is_never_paused_for_non_payment(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1], price=0)
    h = bearer(activate(client, k["key"]).json())
    _backdate_activation(k["id"], 400); _drop_status_cache()
    assert client.get("/api/tenants/me", headers=h).status_code == 200


def test_a_paid_up_business_goes_overdue_once_its_paid_period_and_grace_have_passed(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1], price=5)
    h = bearer(activate(client, k["key"]).json())
    assert client.post(f"/api/master/licences/{k['id']}/payments", json={"amount": 5}, headers=master[1]).status_code == 200
    with engine.begin() as c:
        c.execute(text("update licences set paid_through = now() - interval '30 days', redeemed_at = now() - interval '90 days' where id = :i"), {"i": k["id"]})
    _drop_status_cache()
    r = client.get("/api/tenants/me", headers=h)
    assert r.status_code == 403 and "overdue" in r.json()["detail"]


# ---- reinstate -----------------------------------------------------------------------------------------------------

def test_a_revoked_licence_can_be_reinstated_by_the_master_only(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1])
    h = bearer(activate(client, k["key"]).json())
    assert client.post(f"/api/master/licences/{k['id']}/reinstate", json={}, headers=master[1]).status_code == 400    # not revoked
    assert client.post(f"/api/master/licences/{k['id']}/revoke", json={"reason": "late"}, headers=master[1]).status_code == 200
    _drop_status_cache()
    assert client.get("/api/tenants/me", headers=h).status_code == 403
    assert client.post(f"/api/master/licences/{k['id']}/reinstate", json={}, headers=h).status_code == 404            # not the master
    r = client.post(f"/api/master/licences/{k['id']}/reinstate", json={}, headers=master[1])
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.get("/api/tenants/me", headers=h).status_code == 200


def test_reinstating_a_key_revoked_before_use_makes_it_usable_again(client, master):
    k = issue(client, master[1])
    assert client.post(f"/api/master/licences/{k['id']}/revoke", json={}, headers=master[1]).status_code == 200
    assert activate(client, k["key"]).status_code >= 400
    r = client.post(f"/api/master/licences/{k['id']}/reinstate", json={}, headers=master[1])
    assert r.status_code == 200 and r.json()["status"] == "unused"
    assert activate(client, k["key"]).status_code == 200


# ---- 2. private hosts blocked by default in production --------------------------------------------------------------

@pytest.mark.parametrize("env,flag,expected", [
    ("production", None, True),            # the safe default
    ("production", "false", False),        # an explicit single-business opt-out still works
    ("production", "true", True),
    ("development", None, False),          # local development keeps reaching localhost
    ("development", "true", True),
])
def test_private_host_blocking_defaults_on_in_production(monkeypatch, env, flag, expected):
    monkeypatch.setenv("ENV", env)
    if flag is None:
        monkeypatch.delenv("BLOCK_PRIVATE_CONNECTOR_HOSTS", raising=False)
    else:
        monkeypatch.setenv("BLOCK_PRIVATE_CONNECTOR_HOSTS", flag)
    assert netguard._block_private() is expected


# ---- 3. the shared service key ---------------------------------------------------------------------------------------

def _ev(t, c, eid):
    return {"tenant_id": t, "connector_id": c, "source": "t", "entity": "sale", "entity_id": eid,
            "event_type": "sale", "timestamp": "2026-01-01T12:00:00+00:00", "amount": 10.0}


def test_service_key_cannot_write_events_for_a_business_on_a_production_server(client, tenant_a, monkeypatch):
    _, _, owner = register(client, tenant_a)
    conn = client.post("/api/connectors/", json={"tenant_id": tenant_a, "name": "w", "connector_type": "webhook"}, headers=bearer(owner)).json()["id"]
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_SERVICE_KEY_INGEST", raising=False)
    assert client.post("/api/events/ingest", json=_ev(tenant_a, conn, "p1"), headers=SERVICE).status_code == 401
    assert client.post("/api/events/ingest/batch", json=[_ev(tenant_a, conn, "p2")], headers=SERVICE).status_code == 401
    # a business sign-in still works, and the explicit opt-in restores the old behaviour for machine callers
    assert client.post("/api/events/ingest", json=_ev(tenant_a, conn, "p3"), headers=bearer(owner)).status_code == 200
    monkeypatch.setenv("ALLOW_SERVICE_KEY_INGEST", "true")
    assert client.post("/api/events/ingest", json=_ev(tenant_a, conn, "p4"), headers=SERVICE).status_code == 200
