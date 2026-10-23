"""Licences: single-use, revocable at once, and the master account sees exactly what was recorded."""
import threading
import uuid

import pytest
from sqlalchemy import text

from app.core import cache
from app.core.db import engine
from tests.conftest import ADMIN, bearer, register
from tests.test_block_c import make_owner


@pytest.fixture()
def master(client):
    t, h = make_owner(client, "master")
    email = client.get("/api/tenants/me", headers=h)            # any call proves the session works
    assert email.status_code == 200
    with engine.connect() as c:
        em = c.execute(text("select email from users where tenant_id = :t"), {"t": t}).scalar()
    assert client.post("/api/master/bootstrap", json={"email": em}, headers=ADMIN).status_code == 200
    return t, h


def issue(client, h, **kw):
    r = client.post("/api/master/licences", json={"count": 1, **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["keys"][0]


def activate(client, key, email=None, password="correct-horse-1"):
    return client.post("/api/licences/activate", json={"licence_key": key, "business_name": "Shop " + uuid.uuid4().hex[:5],
                       "email": email or f"{uuid.uuid4().hex[:8]}@example.com", "password": password})


def test_only_the_master_account_can_see_or_use_the_master_endpoints(client, master):
    _, other = make_owner(client, "ordinary")
    for path in ("/api/master/whoami", "/api/master/overview", "/api/master/accounts"):
        assert client.get(path, headers=other).status_code == 404
        assert client.get(path).status_code == 404
    assert client.post("/api/master/licences", json={"count": 1}, headers=other).status_code == 404
    assert client.post("/api/master/bootstrap", json={"email": "a@b.co"}).status_code in (401, 422)
    assert client.get("/api/master/whoami", headers=master[1]).status_code == 200


def test_a_key_works_exactly_once_and_is_stored_only_as_a_hash(client, master):
    k = issue(client, master[1], label="Mama Njeri shop")
    with engine.connect() as c:
        row = c.execute(text("select key_hash, key_hint, status from licences where id = :i"), {"i": k["id"]}).one()
    assert k["key"] not in row.key_hash and k["key"] not in row.key_hint and len(row.key_hash) == 64 and row.status == "unused"
    first = activate(client, k["key"].lower())                    # typed in lower case from a message: still the same key
    assert first.status_code == 200, first.text
    assert client.get("/api/tenants/me", headers=bearer(first.json())).status_code == 200
    again = activate(client, k["key"])
    assert again.status_code == 400 and "already been used" in again.json()["detail"]
    assert activate(client, "BRE-AAAAA-BBBBB-CCCCC-DDDDD").status_code == 400          # an invented key says the same thing


def test_two_people_submitting_the_same_key_at_the_same_instant_create_one_business(client, master):
    k = issue(client, master[1])
    results = []
    gate = threading.Barrier(6)

    def go():
        gate.wait()
        results.append(activate(client, k["key"]).status_code)
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(results) == [200] + [400] * 5
    with engine.connect() as c:
        assert c.execute(text("select count(*) from licences where tenant_id is not null and id = :i"), {"i": k["id"]}).scalar() == 1


def test_a_bad_activation_does_not_spend_the_key(client, master):
    k = issue(client, master[1])
    taken = f"{uuid.uuid4().hex[:8]}@example.com"
    register(client, make_owner(client, "x")[0], "staff", email=taken)
    assert activate(client, k["key"], email=taken).status_code == 409
    assert activate(client, k["key"], password="short").status_code == 400
    assert activate(client, k["key"]).status_code == 200           # still unused after both failures


def test_revoking_locks_the_business_out_on_the_next_request(client, master, monkeypatch):
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    k = issue(client, master[1])
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    tok = activate(client, k["key"], email=email).json()
    h = bearer(tok)
    assert client.get("/api/tenants/me", headers=h).status_code == 200
    assert client.post("/api/master/licences/%s/revoke" % k["id"], json={"reason": "chargeback"}, headers=master[1]).status_code == 200
    r = client.get("/api/tenants/me", headers=h)
    assert r.status_code == 403 and "revoked" in r.json()["detail"]
    login = client.post("/api/auth/login", json={"email": email, "password": "correct-horse-1"})
    assert login.status_code == 403
    acc = [a for a in client.get("/api/master/accounts", headers=master[1]).json() if a["id"] == k["id"]][0]
    assert acc["status"] == "revoked" and acc["revoked_reason"] == "chargeback"


def test_enforcement_is_off_unless_switched_on_and_never_locks_out_the_master(client, master, monkeypatch):
    _, ordinary = make_owner(client, "nolicence")                # a business created the old way has no licence row
    assert client.get("/api/tenants/me", headers=ordinary).status_code == 200
    monkeypatch.setenv("LICENCE_ENFORCED", "true")
    cache.invalidate_prefix("licence:")
    assert client.get("/api/tenants/me", headers=ordinary).status_code == 403
    assert client.get("/api/tenants/me", headers=master[1]).status_code == 200
    lic = [a for a in client.get("/api/master/accounts", headers=master[1]).json() if a["owner_email"] and a["label"] == "Platform owner"][0]
    assert client.post(f"/api/master/licences/{lic['id']}/revoke", json={}, headers=master[1]).status_code == 400


def test_revenue_is_the_sum_of_recorded_payments_and_nothing_else(client, master):
    before = client.get("/api/master/overview", headers=master[1]).json()
    ref = "MPESA-" + uuid.uuid4().hex[:8]
    a, b = issue(client, master[1], price=1), issue(client, master[1], price=1)
    assert client.post(f"/api/master/licences/{a['id']}/payments", json={"amount": 1}, headers=master[1]).status_code == 400   # not activated yet
    activate(client, a["key"]); activate(client, b["key"])
    assert client.post(f"/api/master/licences/{a['id']}/payments", json={"amount": 1, "reference": ref}, headers=master[1]).status_code == 200
    assert client.post(f"/api/master/licences/{b['id']}/payments", json={"amount": 1, "reference": ref}, headers=master[1]).status_code == 409
    assert client.post(f"/api/master/licences/{a['id']}/payments", json={"amount": 2, "months": 2}, headers=master[1]).status_code == 200
    ov = client.get("/api/master/overview", headers=master[1]).json()
    assert round(ov["collected_total"] - before["collected_total"], 2) == 3.0
    assert ov["paid_up"] - before["paid_up"] == 1 and ov["overdue"] - before["overdue"] == 1          # b has not paid
    rows = {r["id"]: r for r in client.get("/api/master/accounts", headers=master[1]).json()}
    assert rows[a["id"]]["total_paid"] == 3.0 and rows[a["id"]]["payment_state"] == "paid" and rows[b["id"]]["payment_state"] == "unpaid"
    # paying early extends the paid-through date; it never overlaps
    p1 = rows[a["id"]]["paid_through"]
    assert p1 is not None
