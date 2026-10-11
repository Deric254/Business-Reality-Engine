"""Shared test setup. Requires a reachable Postgres at DATABASE_URL with
migrations applied (`alembic upgrade head`). Each test run creates its own
uniquely-named tenants, so runs don't collide with each other or real data."""
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/reality_engine")
os.environ["API_KEY"] = "test-service-key"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-test-jwt-secret-1234"
os.environ.setdefault("CONNECTOR_ENCRYPTION_KEY", "kP0kQ0dQ5oJ3m3t0m1o5nB0Zl8m0Y3cQe0n8q9mWk7U=")
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ.pop("REDIS_URL", None)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.db import SessionLocal
from app.models.core import Tenant


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_tenant(name):
    db = SessionLocal()
    try:
        t = Tenant(name=f"{name}-{uuid.uuid4().hex[:8]}")
        db.add(t); db.commit(); db.refresh(t)
        return t.id
    finally:
        db.close()


@pytest.fixture()
def tenant_a():
    return _make_tenant("A")


@pytest.fixture()
def tenant_b():
    return _make_tenant("B")


ADMIN = {"x-admin-key": "test-admin-key"}
SERVICE = {"x-api-key": "test-service-key"}


def register(client, tenant_id, role="owner", email=None, password="correct-horse-1"):
    email = email or f"{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"tenant_id": tenant_id, "email": email, "password": password, "role": role}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return email, password, r.json()


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture(autouse=True)
def _reset_in_memory_limiters():
    """Limiters are process-global; reset so one test's calls never throttle another's.
    (Throttling itself is covered by tests/test_rate_limits.py.)"""
    from app.core import rate_limit as rl
    for lim in (rl.general_limiter, rl.webhook_limiter, rl.auth_limiter):
        if hasattr(lim, "_state"):
            lim._state.clear()
    from app.core import kv
    kv._reset_for_tests()          # lockout counters / revocation entries are process-global too
    yield
