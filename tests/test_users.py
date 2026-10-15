"""User management: owner-only, tenant-scoped, temp passwords, forced change, last-owner guard."""
import uuid

from app.core.db import SessionLocal
from app.models.core import User
from tests.conftest import bearer, register


def em():
    return f"{uuid.uuid4().hex[:8]}@example.com"


def login(client, email, pw):
    return client.post("/api/auth/login", json={"email": email, "password": pw})


def invite(client, h, t, email=None, role="staff"):
    r = client.post("/api/users/", json={"tenant_id": t, "email": email or em(), "role": role}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_invite_flow_temp_password_forces_change(client, tenant_a):
    owner, _, otk = register(client, tenant_a); h = bearer(otk)
    new = invite(client, h, tenant_a)
    assert new["role"] == "staff" and new["must_change_password"] and len(new["temporary_password"]) >= 12
    r = login(client, new["email"], new["temporary_password"])
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    tk = r.json()
    # while on the temporary password: nothing works except changing it
    for url in (f"/api/questions/kpis?tenant_id={tenant_a}", f"/api/alerts/?tenant_id={tenant_a}", "/api/tenants/", "/api/tenants/me"):
        assert client.get(url, headers=bearer(tk)).status_code == 403, url
    assert client.post("/api/auth/logout", json={}, headers=bearer(tk)).status_code == 200     # logout is allowed
    tk = login(client, new["email"], new["temporary_password"]).json()
    c = client.post("/api/auth/change-password", json={"current_password": new["temporary_password"], "new_password": "my-own-pass-77"}, headers=bearer(tk))
    assert c.status_code == 200 and c.json()["must_change_password"] is False
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(c.json())).status_code == 200
    assert login(client, new["email"], new["temporary_password"]).status_code == 401
    # a refresh while still on the temp password stays restricted too
    n2 = invite(client, h, tenant_a)
    t2 = login(client, n2["email"], n2["temporary_password"]).json()
    rr = client.post("/api/auth/refresh", json={"refresh_token": t2["refresh_token"]}).json()
    assert rr["must_change_password"] is True and client.get("/api/tenants/me", headers=bearer(rr)).status_code == 403


def test_listing_hides_hashes_and_marks_me(client, tenant_a):
    owner, _, otk = register(client, tenant_a); h = bearer(otk)
    invite(client, h, tenant_a)
    rows = client.get(f"/api/users/?tenant_id={tenant_a}", headers=h).json()
    assert len(rows) == 2 and sum(r["is_me"] for r in rows) == 1
    assert all("password_hash" not in r and "temporary_password" not in r for r in rows)


def test_staff_cannot_manage_and_other_tenants_are_isolated(client, tenant_a, tenant_b):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    s_email, _, stk = register(client, tenant_a, role="staff")
    victim = invite(client, h, tenant_a)
    assert client.get(f"/api/users/?tenant_id={tenant_a}", headers=bearer(stk)).status_code == 403
    assert client.post("/api/users/", json={"tenant_id": tenant_a, "email": em()}, headers=bearer(stk)).status_code == 403
    assert client.delete(f"/api/users/{victim['id']}?tenant_id={tenant_a}", headers=bearer(stk)).status_code == 403
    _, _, btk = register(client, tenant_b)
    bh = bearer(btk)
    assert client.get(f"/api/users/?tenant_id={tenant_a}", headers=bh).status_code == 403
    # tenant B's owner naming tenant B but tenant A's user id: indistinguishable from "no such user"
    for method, url in (("delete", f"/api/users/{victim['id']}?tenant_id={tenant_b}"),
                        ("post", f"/api/users/{victim['id']}/reset-password?tenant_id={tenant_b}")):
        assert getattr(client, method)(url, headers=bh).status_code == 404
    assert client.patch(f"/api/users/{victim['id']}?tenant_id={tenant_b}", json={"role": "owner"}, headers=bh).status_code == 404
    assert client.get(f"/api/users/?tenant_id={tenant_a}").status_code == 401


def test_duplicate_email_and_validation(client, tenant_a, tenant_b):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    e = em(); invite(client, h, tenant_a, e)
    assert client.post("/api/users/", json={"tenant_id": tenant_a, "email": e}, headers=h).status_code == 409
    _, _, btk = register(client, tenant_b)
    assert client.post("/api/users/", json={"tenant_id": tenant_b, "email": e}, headers=bearer(btk)).status_code == 409   # globally unique, generic message
    assert client.post("/api/users/", json={"tenant_id": tenant_a, "email": "not-an-email"}, headers=h).status_code == 422
    assert client.post("/api/users/", json={"tenant_id": tenant_a, "email": em(), "role": "admin"}, headers=h).status_code == 422


def test_role_change_guards_and_session_revocation(client, tenant_a):
    owner, _, otk = register(client, tenant_a); h = bearer(otk)
    me_id = next(r["id"] for r in client.get(f"/api/users/?tenant_id={tenant_a}", headers=h).json() if r["is_me"])
    assert client.patch(f"/api/users/{me_id}?tenant_id={tenant_a}", json={"role": "staff"}, headers=h).status_code == 400   # not yourself
    n = invite(client, h, tenant_a)
    ntk = client.post("/api/auth/change-password", json={"current_password": n["temporary_password"], "new_password": "my-own-pass-77"},
                      headers=bearer(login(client, n["email"], n["temporary_password"]).json())).json()
    assert client.get(f"/api/users/?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 403      # staff
    assert client.patch(f"/api/users/{n['id']}?tenant_id={tenant_a}", json={"role": "owner"}, headers=h).json()["role"] == "owner"
    assert client.get(f"/api/users/?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 401      # old session (old role) revoked
    t2 = login(client, n["email"], "my-own-pass-77").json()
    assert client.get(f"/api/users/?tenant_id={tenant_a}", headers=bearer(t2)).status_code == 200      # now an owner
    # two owners: the new one may demote the original, but nobody may demote the LAST owner
    assert client.patch(f"/api/users/{me_id}?tenant_id={tenant_a}", json={"role": "staff"}, headers=bearer(t2)).status_code == 200
    only = next(r["id"] for r in client.get(f"/api/users/?tenant_id={tenant_a}", headers=bearer(t2)).json() if r["is_me"])
    assert only == n["id"]
    other = invite(client, bearer(t2), tenant_a)
    assert client.delete(f"/api/users/{other['id']}?tenant_id={tenant_a}", headers=bearer(t2)).status_code == 200


def test_cannot_remove_or_demote_last_owner(client, tenant_a):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    # a second owner exists only as staff -> promote, then try to strip the sole remaining owner via that account
    n = invite(client, h, tenant_a)
    me_id = next(r["id"] for r in client.get(f"/api/users/?tenant_id={tenant_a}", headers=h).json() if r["is_me"])
    assert client.delete(f"/api/users/{me_id}?tenant_id={tenant_a}", headers=h).status_code == 400            # self
    # make the invited user an owner, demote the first, then the second is the sole owner
    client.patch(f"/api/users/{n['id']}?tenant_id={tenant_a}", json={"role": "owner"}, headers=h)
    t2 = client.post("/api/auth/change-password", json={"current_password": n["temporary_password"], "new_password": "my-own-pass-77"},
                     headers=bearer(login(client, n["email"], n["temporary_password"]).json())).json()
    client.patch(f"/api/users/{me_id}?tenant_id={tenant_a}", json={"role": "staff"}, headers=bearer(t2))
    # sole owner (n) cannot delete/demote themselves (self rule), and the demoted first user is staff, so cannot act
    assert client.delete(f"/api/users/{n['id']}?tenant_id={tenant_a}", headers=bearer(t2)).status_code == 400
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.tenant_id == tenant_a, User.role == "owner").count() == 1
    finally:
        db.close()


def test_reset_password_revokes_sessions_and_forces_change(client, tenant_a):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    n = invite(client, h, tenant_a)
    ntk = client.post("/api/auth/change-password", json={"current_password": n["temporary_password"], "new_password": "my-own-pass-77"},
                      headers=bearer(login(client, n["email"], n["temporary_password"]).json())).json()
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 200
    r = client.post(f"/api/users/{n['id']}/reset-password?tenant_id={tenant_a}", headers=h)
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 401
    assert login(client, n["email"], "my-own-pass-77").status_code == 401
    assert login(client, n["email"], r.json()["temporary_password"]).json()["must_change_password"] is True
    me_id = next(x["id"] for x in client.get(f"/api/users/?tenant_id={tenant_a}", headers=h).json() if x["is_me"])
    assert client.post(f"/api/users/{me_id}/reset-password?tenant_id={tenant_a}", headers=h).status_code == 400


def test_delete_user_kills_their_session_immediately(client, tenant_a):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    n = invite(client, h, tenant_a)
    ntk = client.post("/api/auth/change-password", json={"current_password": n["temporary_password"], "new_password": "my-own-pass-77"},
                      headers=bearer(login(client, n["email"], n["temporary_password"]).json())).json()
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 200
    assert client.delete(f"/api/users/{n['id']}?tenant_id={tenant_a}", headers=h).status_code == 200
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers=bearer(ntk)).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": ntk["refresh_token"]}).status_code == 401
    assert login(client, n["email"], "my-own-pass-77").status_code == 401
    assert client.delete(f"/api/users/{n['id']}?tenant_id={tenant_a}", headers=h).status_code == 404


def test_audit_entries_never_contain_the_temporary_password(client, tenant_a):
    _, _, otk = register(client, tenant_a); h = bearer(otk)
    n = invite(client, h, tenant_a)
    client.post(f"/api/users/{n['id']}/reset-password?tenant_id={tenant_a}", headers=h)
    rows = client.get(f"/api/audit/?tenant_id={tenant_a}&action=user", headers=h).json()
    acts = {r["action"] for r in rows}
    assert {"user.create", "user.reset_password"} <= acts
    assert n["temporary_password"] not in str(rows)
