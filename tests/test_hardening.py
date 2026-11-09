"""Payment controls access, revoked licences can be reinstated, private hosts are blocked by default in production,
and the shared service key cannot write for any business on a production server."""

import uuid

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
    assert client.get("/api/tenants/me", headers=h).status_code == 200               # view-only: reading still works
    r = client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h)
    assert r.status_code == 403 and r.json()["licence"] == {"ok": False, "kind": "overdue", "read_only": True}
    assert client.post(f"/api/master/licences/{k['id']}/payments", json={"amount": 5, "months": 1}, headers=master[1]).status_code == 200
    assert client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h).status_code == 200   # paid: full use resumes at once, no waiting for the cache


def test_grace_period_is_configurable_and_zero_means_none(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1], price=5)
    h = bearer(activate(client, k["key"]).json())
    _backdate_activation(k["id"], 3); _drop_status_cache()
    monkeypatch.setenv("LICENCE_GRACE_DAYS", "7")
    assert client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h).status_code == 200
    monkeypatch.setenv("LICENCE_GRACE_DAYS", "0"); _drop_status_cache()
    assert client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h).status_code == 403


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
    assert client.get("/api/tenants/me", headers=h).status_code == 200
    r = client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h)
    assert r.status_code == 403 and r.json()["licence"]["kind"] == "overdue"


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


# ---- view-only after the grace period ------------------------------------------------------------------------------

OVERDUE_MESSAGE = "Your subscription has expired, so this is view-only. Renew to add or change anything."


def _overdue_business(client, master, monkeypatch, days=30):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    monkeypatch.delenv("LICENCE_GRACE_DAYS", raising=False)
    k = issue(client, master[1], price=5)
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    tok = activate(client, k["key"], email=email).json()
    _backdate_activation(k["id"], days); _drop_status_cache()
    return k, email, tok, bearer(tok)


def test_the_default_grace_is_five_days(client, master, monkeypatch):
    k, _, _, h = _overdue_business(client, master, monkeypatch, days=4)
    assert client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h).status_code == 200
    _backdate_activation(k["id"], 6); _drop_status_cache()
    assert client.patch("/api/tenants/me", json={"area": "Nairobi"}, headers=h).status_code == 403


def test_an_overdue_business_can_sign_in_refresh_read_and_export_but_not_add_or_change(client, master, monkeypatch):
    _, email, tok, h = _overdue_business(client, master, monkeypatch)
    login = client.post("/api/auth/login", json={"email": email, "password": "correct-horse-1"})
    assert login.status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]}).status_code == 200
    h = bearer(login.json())
    tid = client.get("/api/tenants/me", headers=h).json()["id"]
    for path in (f"/api/business/analytics?tenant_id={tid}", f"/api/alerts/?tenant_id={tid}", "/api/licences/mine"):
        assert client.get(path, headers=h).status_code == 200, path
    for method, path, body in (("post", "/api/goals", {"tenant_id": tid, "name": "t", "metric": "sales", "cadence": "month", "target_amount": 100}),
                               ("post", "/api/questions/ask", {"tenant_id": tid, "question": "How are sales?"}),
                               ("post", "/api/detection/run/all", {"tenant_id": tid}),
                               ("post", "/api/demo/samples", {"tenant_id": tid, "kind": "duka"}),
                               ("patch", "/api/tenants/me", {"area": "x"}), ("delete", f"/api/goals/none?tenant_id={tid}", None)):
        r = getattr(client, method)(path, headers=h, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path
        assert r.json() == {"detail": OVERDUE_MESSAGE, "error": OVERDUE_MESSAGE, "licence": {"ok": False, "kind": "overdue", "read_only": True}}
    assert client.post("/api/auth/logout", json={}, headers=h).status_code == 200


def test_a_revoked_business_is_still_fully_locked_and_never_gets_view_only(client, master, monkeypatch):
    k, _, _, h = _overdue_business(client, master, monkeypatch)
    assert client.post(f"/api/master/licences/{k['id']}/revoke", json={}, headers=master[1]).status_code == 200
    r = client.get("/api/tenants/me", headers=h)
    assert r.status_code == 403 and "revoked" in r.json()["detail"] and "licence" not in r.json()


def test_my_licence_reports_the_tenants_own_price_period_and_view_only_state(client, master, monkeypatch):
    k = issue(client, master[1], price=37)
    h = bearer(activate(client, k["key"]).json())
    s = client.get("/api/licences/mine", headers=h).json()
    assert (s["kind"], s["ok"], s["read_only"], s["grace"]) == ("licensed", True, False, True)           # never paid: the period ends at activation
    assert s["licence_id"] == k["id"] and s["amount_cents"] == 3700 and s["currency"] == "USD" and s["interval_months"] == 1
    assert 4 <= s["grace_days_left"] <= 5
    client.post(f"/api/master/licences/{k['id']}/payments", json={"amount": 37}, headers=master[1])
    s = client.get("/api/licences/mine", headers=h).json()
    assert (s["ok"], s["grace"]) == (True, False) and 29 <= s["days_left"] <= 31
    with engine.begin() as c:
        c.execute(text("update licences set paid_through = now() - interval '2 days' where id = :i"), {"i": k["id"]})
    s = client.get("/api/licences/mine", headers=h).json()
    assert (s["ok"], s["grace"], s["read_only"]) == (True, True, False) and 2 <= s["grace_days_left"] <= 4
    with engine.begin() as c:
        c.execute(text("update licences set paid_through = now() - interval '6 days' where id = :i"), {"i": k["id"]})
    s = client.get("/api/licences/mine", headers=h).json()
    assert (s["kind"], s["ok"], s["read_only"]) == ("overdue", False, True)


def test_a_free_licence_has_nothing_to_pay_for(client, master):
    k = issue(client, master[1], price=0)
    h = bearer(activate(client, k["key"]).json())
    assert client.get("/api/licences/mine", headers=h).json() == {"ok": True, "kind": "free", "read_only": False, "grace": False, "payable": False}
