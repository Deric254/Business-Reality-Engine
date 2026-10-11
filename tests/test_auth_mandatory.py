import uuid
import pytest
from tests.conftest import ADMIN, SERVICE, register, bearer

USER_GETS = [
    "/api/alerts/?tenant_id={t}",
    "/api/connectors/?tenant_id={t}",
    "/api/entities/?tenant_id={t}",
    "/api/cases/?tenant_id={t}",
    "/api/coverage/?tenant_id={t}",
    "/api/questions/kpis?tenant_id={t}",
    "/api/tenants/",
]


@pytest.mark.parametrize("path", USER_GETS)
def test_no_token_is_401_even_with_service_key(client, tenant_a, path):
    url = path.format(t=tenant_a)
    assert client.get(url).status_code == 401
    assert client.get(url, headers=SERVICE).status_code == 401  # service key must NOT grant tenant data


@pytest.mark.parametrize("path", USER_GETS[:-1])
def test_valid_token_ok_and_cross_tenant_403(client, tenant_a, tenant_b, path):
    _, _, tok = register(client, tenant_a)
    assert client.get(path.format(t=tenant_a), headers=bearer(tok)).status_code == 200
    assert client.get(path.format(t=tenant_b), headers=bearer(tok)).status_code == 403


def test_garbage_and_wrong_type_tokens_rejected(client, tenant_a):
    _, _, tok = register(client, tenant_a)
    url = f"/api/alerts/?tenant_id={tenant_a}"
    assert client.get(url, headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get(url, headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get(url, headers={"Authorization": f"Bearer {tok['refresh_token']}"}).status_code == 401  # refresh != access


def test_tenants_list_scoped_to_own_tenant(client, tenant_a, tenant_b):
    _, _, tok = register(client, tenant_a)
    r = client.get("/api/tenants/", headers=bearer(tok))
    assert r.status_code == 200 and [t["id"] for t in r.json()] == [tenant_a]


def test_connector_create_owner_only(client, tenant_a):
    _, _, owner = register(client, tenant_a, role="owner")
    _, _, staff = register(client, tenant_a, role="staff")
    body = {"tenant_id": tenant_a, "name": "wh", "connector_type": "webhook", "config": {}}
    assert client.post("/api/connectors/", json=body).status_code == 401
    assert client.post("/api/connectors/", json=body, headers=bearer(staff)).status_code == 403
    assert client.post("/api/connectors/", json=body, headers=bearer(owner)).status_code == 200


def test_register_is_privileged(client, tenant_a, tenant_b):
    body = lambda t: {"tenant_id": t, "email": f"{uuid.uuid4().hex[:8]}@example.com", "password": "correct-horse-1", "role": "owner"}
    assert client.post("/api/auth/register", json=body(tenant_a)).status_code == 401            # anonymous
    assert client.post("/api/auth/register", json=body(tenant_a), headers=SERVICE).status_code == 401  # service key not enough
    assert client.post("/api/auth/register", json={**body(tenant_a), "email": f"{uuid.uuid4().hex[:8]}@example.com"}, headers={"x-admin-key": "wrong"}).status_code == 401
    _, _, staff = register(client, tenant_a, role="staff")
    assert client.post("/api/auth/register", json={**body(tenant_a), "email": f"{uuid.uuid4().hex[:8]}@example.com"}, headers=bearer(staff)).status_code == 403
    _, _, owner = register(client, tenant_a, role="owner")
    assert client.post("/api/auth/register", json={**body(tenant_a), "email": f"{uuid.uuid4().hex[:8]}@example.com"}, headers=bearer(owner)).status_code == 200
    # owner of A cannot create users in B
    assert client.post("/api/auth/register", json={**body(tenant_b), "email": f"{uuid.uuid4().hex[:8]}@example.com"}, headers=bearer(owner)).status_code == 403


def test_login_and_refresh_public_and_generic_errors(client, tenant_a):
    email, pw, _ = register(client, tenant_a)
    ok = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert ok.status_code == 200
    bad_pw = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    bad_user = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert bad_pw.status_code == bad_user.status_code == 401 and bad_pw.json() == bad_user.json()
    assert client.post("/api/auth/refresh", json={"refresh_token": ok.json()["refresh_token"]}).status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": ok.json()["access_token"]}).status_code == 401


def _ev(t, c, eid="e-1"):
    return {"tenant_id": t, "connector_id": c, "source": "t", "entity": "sale", "entity_id": eid,
            "event_type": "sale", "timestamp": "2026-01-01T12:00:00", "amount": 10.0}


def test_events_accept_service_key_or_token_only(client, tenant_a, tenant_b):
    _, _, owner = register(client, tenant_a)
    conn = client.post("/api/connectors/", json={"tenant_id": tenant_a, "name": "w", "connector_type": "webhook"}, headers=bearer(owner)).json()["id"]
    assert client.post("/api/events/ingest", json=_ev(tenant_a, conn)).status_code == 401
    assert client.post("/api/events/ingest", json=_ev(tenant_a, conn), headers={"x-api-key": "wrong"}).status_code == 401
    r = client.post("/api/events/ingest", json=_ev(tenant_a, conn), headers=SERVICE)
    assert r.status_code == 200 and r.json()["duplicate"] is False
    r = client.post("/api/events/ingest", json=_ev(tenant_a, conn), headers=bearer(owner))
    assert r.status_code == 200 and r.json()["duplicate"] is True
    # token for A cannot write to B, singly or inside a mixed batch
    assert client.post("/api/events/ingest", json=_ev(tenant_b, conn), headers=bearer(owner)).status_code == 403
    assert client.post("/api/events/ingest/batch", json=[_ev(tenant_a, conn, "e-2"), _ev(tenant_b, conn, "e-3")], headers=bearer(owner)).status_code == 403


def test_system_needs_admin_key(client):
    assert client.get("/api/system/scheduler").status_code in (401, 404, 405)
    assert client.get("/api/system/scheduler", headers=SERVICE).status_code in (401, 404, 405)


def test_health_open(client):
    assert client.get("/api/health/").status_code == 200


def test_webhook_uses_connector_token_not_jwt(client, tenant_a):
    _, _, owner = register(client, tenant_a)
    c = client.post("/api/connectors/", json={"tenant_id": tenant_a, "name": "wh2", "connector_type": "webhook",
        "config": {"entity": "sale", "event_type": "sale", "timestamp_path": "ts", "amount_path": "amt"}}, headers=bearer(owner)).json()
    url = c["webhook_url"]
    r = client.post(url, json={"ts": "2026-01-02T10:00:00", "amt": 5})
    assert r.status_code in (200, 400, 422)  # reached the handler without any JWT/service key
    assert client.post(url[:-3] + "xxx", json={"ts": "2026-01-02T10:00:00", "amt": 5}).status_code in (401, 403, 404)


def test_kpi_cache_is_per_tenant_and_still_authorized(client, tenant_a, tenant_b):
    from app.core import cache
    cache.clear_local()
    _, _, ta = register(client, tenant_a)
    _, _, tb = register(client, tenant_b)
    for _ in range(2):  # second call is served from cache
        assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(ta)).status_code == 200
    # a cached value for tenant A must never be reachable with B's token, nor without a token
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(tb)).status_code == 403
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}").status_code == 401
