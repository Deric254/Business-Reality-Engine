"""Token revocation, single-use refresh, lockout, password change, audit log, security headers."""
import uuid


from app.core import kv, lockout
from app.core.db import SessionLocal
from app.models.core import AuditLog, RevokedToken, User
from tests.conftest import ADMIN, bearer, register


def login(client, email, pw="correct-horse-1"):
    return client.post("/api/auth/login", json={"email": email, "password": pw})


def kpis(client, t, tokens):
    return client.get(f"/api/questions/kpis?tenant_id={t}", headers=bearer(tokens)).status_code


def audit_actions(tenant, action_prefix=""):
    db = SessionLocal()
    try:
        return [a.action for a in db.query(AuditLog).filter(AuditLog.tenant_id == tenant).order_by(AuditLog.ts)
                if a.action.startswith(action_prefix)]
    finally:
        db.close()


# ---------- logout / revocation ----------
def test_logout_revokes_access_and_refresh_immediately(client, tenant_a):
    email, pw, _ = register(client, tenant_a)
    tk = login(client, email).json()
    assert kpis(client, tenant_a, tk) == 200
    r = client.post("/api/auth/logout", json={"refresh_token": tk["refresh_token"]}, headers=bearer(tk))
    assert r.status_code == 200
    assert kpis(client, tenant_a, tk) == 401                                   # access token dead at once
    assert client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]}).status_code == 401
    assert "auth.logout" in audit_actions(tenant_a)


def test_logout_requires_auth_and_cannot_revoke_someone_elses_refresh(client, tenant_a):
    e1, _, _ = register(client, tenant_a); e2, _, _ = register(client, tenant_a)
    t1, t2 = login(client, e1).json(), login(client, e2).json()
    assert client.post("/api/auth/logout", json={}).status_code == 401
    client.post("/api/auth/logout", json={"refresh_token": t2["refresh_token"]}, headers=bearer(t1))
    assert client.post("/api/auth/refresh", json={"refresh_token": t2["refresh_token"]}).status_code == 200   # untouched


def test_refresh_tokens_are_single_use_and_reuse_revokes_everything(client, tenant_a):
    email, _, _ = register(client, tenant_a)
    tk = login(client, email).json()
    rotated = client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]})
    assert rotated.status_code == 200
    new = rotated.json()
    assert kpis(client, tenant_a, new) == 200
    # the old refresh token is replayed (as a thief would): rejected AND the whole family is burned
    assert client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]}).status_code == 401
    assert kpis(client, tenant_a, new) == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]}).status_code == 401
    assert "auth.refresh_reuse_detected" in audit_actions(tenant_a)
    assert login(client, email).status_code == 200                               # the real user can sign in again
    assert kpis(client, tenant_a, login(client, email).json()) == 200


def test_concurrent_refresh_only_one_wins(client, tenant_a):
    import threading
    email, _, _ = register(client, tenant_a)
    tk = login(client, email).json()
    codes = []
    def go():
        codes.append(client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]}).status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes.count(200) == 1 and codes.count(401) == 5


def test_logout_all_kills_every_session_but_not_new_logins(client, tenant_a):
    email, _, _ = register(client, tenant_a)
    a, b = login(client, email).json(), login(client, email).json()
    assert client.post("/api/auth/logout-all", headers=bearer(a)).status_code == 200
    assert kpis(client, tenant_a, a) == 401 and kpis(client, tenant_a, b) == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": b["refresh_token"]}).status_code == 401
    assert kpis(client, tenant_a, login(client, email).json()) == 200


def test_logout_all_survives_kv_flush_for_refresh_tokens(client, tenant_a):
    """The durable (database) cutoff still blocks refresh when the fast store has been emptied."""
    email, _, _ = register(client, tenant_a)
    tk = login(client, email).json()
    client.post("/api/auth/logout-all", headers=bearer(tk))
    kv._reset_for_tests()
    assert client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]}).status_code == 401


def test_tokens_without_jti_are_rejected(client, tenant_a):
    import jwt as pyjwt, os, datetime as dt
    email, _, _ = register(client, tenant_a)
    uid = SessionLocal().query(User).filter_by(email=email).first().id
    old = pyjwt.encode({"sub": uid, "tenant_id": tenant_a, "role": "owner", "type": "access",
                        "exp": dt.datetime.utcnow() + dt.timedelta(minutes=5)}, os.environ["JWT_SECRET_KEY"], algorithm="HS256")
    assert client.get(f"/api/questions/kpis?tenant_id={tenant_a}", headers={"Authorization": f"Bearer {old}"}).status_code == 401


# ---------- change password ----------
def test_change_password_flow(client, tenant_a):
    email, pw, _ = register(client, tenant_a)
    tk = login(client, email).json()
    h = bearer(tk)
    assert client.post("/api/auth/change-password", json={"current_password": "nope-nope-1", "new_password": "brand-new-pass-9"}, headers=h).status_code == 401
    assert client.post("/api/auth/change-password", json={"current_password": pw, "new_password": "short"}, headers=h).status_code == 400
    assert client.post("/api/auth/change-password", json={"current_password": pw, "new_password": pw}, headers=h).status_code == 400
    assert client.post("/api/auth/change-password", json={"current_password": pw, "new_password": "x" * 73}, headers=h).status_code == 400
    assert client.post("/api/auth/change-password", json={"current_password": pw, "new_password": "brand-new-pass-9"}).status_code == 401
    r = client.post("/api/auth/change-password", json={"current_password": pw, "new_password": "brand-new-pass-9"}, headers=h)
    assert r.status_code == 200
    assert kpis(client, tenant_a, tk) == 401                                     # old session dead
    assert kpis(client, tenant_a, r.json()) == 200                               # fresh pair works
    assert login(client, email, pw).status_code == 401
    assert login(client, email, "brand-new-pass-9").status_code == 200
    assert "auth.change_password" in audit_actions(tenant_a)


def test_overlong_password_is_a_clean_error_not_a_500(client, tenant_a):
    r = client.post("/api/auth/register", json={"tenant_id": tenant_a, "email": f"{uuid.uuid4().hex[:8]}@example.com", "password": "p" * 100}, headers=ADMIN)
    assert r.status_code == 400
    email, _, _ = register(client, tenant_a)
    assert login(client, email, "p" * 100).status_code == 401


# ---------- lockout ----------
def test_lockout_after_repeated_failures_and_reset_on_success(client, tenant_a):
    email, pw, _ = register(client, tenant_a)
    for _ in range(lockout.MAX_FAILURES - 1):
        assert login(client, email, "wrong-pass-1").status_code == 401
    assert login(client, email, pw).status_code == 200                           # success resets the counter
    from app.core.rate_limit import auth_limiter
    auth_limiter._state.clear()
    for _ in range(lockout.MAX_FAILURES):
        login(client, email, "wrong-pass-1")
    from app.core.rate_limit import auth_limiter
    auth_limiter._state.clear()                                                   # isolate the lockout from the per-IP throttle
    r = login(client, email, pw)                                                  # even the RIGHT password is refused now
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    acts = audit_actions(tenant_a)
    assert "auth.login_blocked" in acts and "auth.login_failed" in acts
    assert "retry" in r.json()["detail"].lower() or "many" in r.json()["detail"].lower()
    kv.delete(f"bre:login:lock:{email.lower()}")                                  # lock expiry
    assert login(client, email, pw).status_code == 200


def test_lockout_applies_to_unknown_emails_identically(client):
    ghost = f"ghost{uuid.uuid4().hex[:6]}@example.com"
    codes = [login(client, ghost, "whatever-1").status_code for _ in range(lockout.MAX_FAILURES + 1)]
    assert codes[:lockout.MAX_FAILURES] == [401] * lockout.MAX_FAILURES and codes[-1] == 429   # no enumeration signal


def test_lockout_is_per_email(client, tenant_a):
    e1, p1, _ = register(client, tenant_a); e2, p2, _ = register(client, tenant_a)
    for _ in range(lockout.MAX_FAILURES):
        login(client, e1, "wrong-pass-1")
    assert login(client, e1, p1).status_code == 429 and login(client, e2, p2).status_code == 200


# ---------- audit ----------
def test_audit_trail_records_actions_without_secrets_and_is_owner_only_and_isolated(client, tenant_a, tenant_b):
    owner, _, tk = register(client, tenant_a); h = bearer(tk)
    staff_email, _, stk = register(client, tenant_a, role="staff")
    client.post("/api/monitoring/metrics", json={"tenant_id": tenant_a, "entity": "refund", "direction": "rise"}, headers=h)
    ch = client.post("/api/notifications/channels", json={"tenant_id": tenant_a, "channel_type": "email", "target": "a@example.com"}, headers=h).json()
    client.delete(f"/api/notifications/channels/{ch['id']}?tenant_id={tenant_a}", headers=h)
    rows = client.get(f"/api/audit/?tenant_id={tenant_a}", headers=h)
    assert rows.status_code == 200
    actions = [r["action"] for r in rows.json()]
    assert {"metric.create", "channel.create", "channel.delete", "auth.register"} <= set(actions)
    assert all("secret" not in str(r["detail"]).lower() and "password" not in str(r["detail"]).lower() for r in rows.json())
    assert client.get(f"/api/audit/?tenant_id={tenant_a}&action=metric", headers=h).json()[0]["action"].startswith("metric")
    assert client.get(f"/api/audit/?tenant_id={tenant_a}", headers=bearer(stk)).status_code == 403     # owner only
    assert client.get(f"/api/audit/?tenant_id={tenant_a}").status_code == 401
    _, _, tkb = register(client, tenant_b)
    assert client.get(f"/api/audit/?tenant_id={tenant_a}", headers=bearer(tkb)).status_code == 403
    assert client.get(f"/api/audit/?tenant_id={tenant_b}", headers=bearer(tkb)).json() != rows.json()
    assert client.get(f"/api/audit/?tenant_id={tenant_a}&limit=0", headers=h).status_code == 422


def test_audit_failure_never_breaks_the_request(client, tenant_a, monkeypatch):
    from app.core import audit as A
    email, _, _ = register(client, tenant_a)
    monkeypatch.setattr(A, "AuditLog", lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert login(client, email).status_code == 200


# ---------- headers ----------
def test_security_headers(client):
    r = client.get("/api/health/")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "no-referrer"
    a = client.post("/api/auth/login", json={"email": "x@example.com", "password": "whatever-1"})
    assert a.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in client.get("/docs").headers          # swagger needs its own scripts


def test_revoked_rows_purge(client, tenant_a):
    from app.core import revocation
    email, _, _ = register(client, tenant_a)
    tk = login(client, email).json()
    client.post("/api/auth/refresh", json={"refresh_token": tk["refresh_token"]})
    db = SessionLocal()
    try:
        assert db.query(RevokedToken).count() >= 1
        assert revocation.purge_expired(db) >= 0
    finally:
        db.rollback(); db.close()
