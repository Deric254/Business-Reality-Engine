"""Block D: Slack / Teams / SMS / WhatsApp, quiet hours, digests, filters, forgot-password and invitations.
All receivers are real local servers (SMTP + HTTP); nothing is mocked inside the application."""
import base64, json, os, re, threading, uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from app.core import notifications as N
from app.core.db import SessionLocal
from app.models.alerts import Alert, AlertSeverity
from app.models.config import NotificationChannel, NotificationDelivery
from app.models.core import PasswordResetToken, User
from tests.conftest import ADMIN, bearer, register
from tests.test_notifications import smtp, make_alert, dispatch, add_channel  # noqa: F401  (fixtures + helpers)


class _Recv(BaseHTTPRequestHandler):
    got, status = [], 200
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Recv.got.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(_Recv.status); self.end_headers()
    def log_message(self, *a): pass


@pytest.fixture(scope="module")
def http():
    srv = HTTPServer(("127.0.0.1", 0), _Recv)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["ALLOW_PRIVATE_WEBHOOK_TARGETS"] = "true"
    os.environ.update(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="tok", TWILIO_SMS_FROM="+15550001111",
                      TWILIO_WHATSAPP_FROM="+15550002222", TWILIO_API_BASE=f"http://127.0.0.1:{srv.server_port}")
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    os.environ["ALLOW_PRIVATE_WEBHOOK_TARGETS"] = "false"


@pytest.fixture(autouse=True)
def _clean(http):
    _Recv.got.clear(); _Recv.status = 200
    db = SessionLocal()
    db.query(NotificationDelivery).delete(); db.query(NotificationChannel).delete(); db.commit(); db.close()


def owner(client, tenant):
    return register(client, tenant, "owner")[2]


def status_of(alert_id):
    db = SessionLocal()
    try:
        d = db.query(NotificationDelivery).filter_by(alert_id=alert_id).first()
        return (d.status, d.attempts, d.next_attempt_at, d.last_error) if d else None
    finally:
        db.close()


# ---------- new channel types ----------
def test_slack_and_teams_payloads(client, tenant_a, http):
    tok = owner(client, tenant_a)
    r1 = add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/slack"); assert r1.status_code == 200, r1.text
    assert r1.json()["signing_secret"] is None
    assert add_channel(client, tok, tenant_a, channel_type="teams", target=http + "/teams").status_code == 200
    make_alert(tenant_a)
    assert dispatch()["sent"] == 2
    by = {g["path"]: json.loads(g["body"]) for g in _Recv.got}
    assert "[CRITICAL] Sales dropped" in by["/slack"]["text"] and "Check the till." in by["/slack"]["text"]
    card = by["/teams"]["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["content"]["body"][0]["text"].startswith("[CRITICAL]")
    assert "\n" not in by["/slack"]["text"].split("\n")[0]


def test_sms_and_whatsapp_via_twilio(client, tenant_a, http):
    tok = owner(client, tenant_a)
    assert add_channel(client, tok, tenant_a, channel_type="sms", target="+254 712-345-678").json()["target"] == "+254712345678"
    assert add_channel(client, tok, tenant_a, channel_type="whatsapp", target="+254722000111").status_code == 200
    make_alert(tenant_a)
    assert dispatch()["sent"] == 2
    forms = {}
    for g in _Recv.got:
        assert g["path"] == "/2010-04-01/Accounts/ACtest/Messages.json"
        assert base64.b64decode(g["headers"]["Authorization"].split()[1]) == b"ACtest:tok"
        f = {k: v[0] for k, v in parse_qs(g["body"].decode()).items()}
        forms[f["To"]] = f
    assert forms["+254712345678"]["From"] == "+15550001111" and len(forms["+254712345678"]["Body"]) <= 320
    assert forms["whatsapp:+254722000111"]["From"] == "whatsapp:+15550002222"


def test_twilio_failure_retries_then_dead(client, tenant_a, http):
    tok = owner(client, tenant_a)
    add_channel(client, tok, tenant_a, channel_type="sms", target="+254712345678")
    aid = make_alert(tenant_a); _Recv.status = 500
    assert dispatch()["retry"] == 1
    s, attempts, _, err = status_of(aid); assert (s, attempts) == ("pending", 1) and "HTTP 500" in err
    assert "tok" not in err                                                   # no credential in the stored error


def test_twilio_unconfigured_is_a_clear_delivery_error(client, tenant_a, http, monkeypatch):
    tok = owner(client, tenant_a)
    add_channel(client, tok, tenant_a, channel_type="whatsapp", target="+254712345678")
    aid = make_alert(tenant_a)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    dispatch()
    assert "not configured" in status_of(aid)[3]


def test_channel_validation(client, tenant_a, http):
    tok = owner(client, tenant_a)
    bad = [
        dict(channel_type="sms", target="0712345678"), dict(channel_type="sms", target="+1"), dict(channel_type="whatsapp", target="abc"),
        dict(channel_type="sms", target="+254712345678", digest_mode="daily"),
        dict(channel_type="slack", target=http + "/x", quiet_start="22:00"),
        dict(channel_type="slack", target=http + "/x", quiet_start="25:00", quiet_end="06:00"),
        dict(channel_type="slack", target=http + "/x", quiet_start="2200", quiet_end="0600"),
        dict(channel_type="slack", target=http + "/x", digest_hour=24),
        dict(channel_type="slack", target=http + "/x", filter_kinds=["nonsense"]),
        dict(channel_type="slack", target="not a url"), dict(channel_type="teams", target="ftp://x.example.com/y"),
        dict(channel_type="email", target="nope"),
    ]
    for b in bad:
        r = add_channel(client, tok, tenant_a, **b)
        assert r.status_code == 400, (b, r.status_code, r.text)
    assert client.post("/api/notifications/channels", json={"tenant_id": tenant_a, "channel_type": "pager", "target": "x"}, headers=bearer(tok)).status_code == 422


def test_slack_url_blocks_private_when_not_allowed(client, tenant_a, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "false")
    tok = owner(client, tenant_a)
    assert add_channel(client, tok, tenant_a, channel_type="slack", target="https://127.0.0.1/x").status_code == 400
    assert add_channel(client, tok, tenant_a, channel_type="teams", target="https://169.254.169.254/latest").status_code == 400


def test_channel_test_endpoint_for_new_types(client, tenant_a, http):
    tok = owner(client, tenant_a)
    cid = add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s").json()["id"]
    assert client.post(f"/api/notifications/channels/{cid}/test?tenant_id={tenant_a}", headers=bearer(tok)).json() == {"ok": True}
    assert "Test alert" in json.loads(_Recv.got[-1]["body"])["text"]
    cid2 = add_channel(client, tok, tenant_a, channel_type="sms", target="+254712345678").json()["id"]
    assert client.post(f"/api/notifications/channels/{cid2}/test?tenant_id={tenant_a}", headers=bearer(tok)).json() == {"ok": True}
    _Recv.status = 500
    assert client.post(f"/api/notifications/channels/{cid2}/test?tenant_id={tenant_a}", headers=bearer(tok)).json()["ok"] is False


# ---------- quiet hours ----------
def test_quiet_hour_math():
    t = lambda h, m=0: datetime(2026, 10, 13, h, m)
    assert N.in_quiet_hours(t(23), "22:00", "06:00") and N.in_quiet_hours(t(3), "22:00", "06:00")
    assert not N.in_quiet_hours(t(6), "22:00", "06:00") and not N.in_quiet_hours(t(12), "22:00", "06:00")
    assert N.in_quiet_hours(t(13), "12:00", "14:00") and not N.in_quiet_hours(t(14), "12:00", "14:00")
    assert not N.in_quiet_hours(t(3), None, None) and not N.in_quiet_hours(t(3), "05:00", "05:00")
    assert N.quiet_until(t(23), "06:00") == datetime(2026, 10, 14, 6, 0)
    assert N.quiet_until(t(3), "06:00") == datetime(2026, 10, 13, 6, 0)


def _window_around_now():
    now = datetime.utcnow()
    return (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")


def test_quiet_hours_defer_non_critical_but_never_critical(client, tenant_a, http):
    tok = owner(client, tenant_a); qs, qe = _window_around_now()
    add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s", min_severity="medium", quiet_start=qs, quiet_end=qe)
    low = make_alert(tenant_a, AlertSeverity.high, "High one"); crit = make_alert(tenant_a, AlertSeverity.critical, "Crit one")
    st = dispatch(); assert st["sent"] == 1 and st["deferred"] == 1
    assert status_of(crit)[0] == "sent"
    s, attempts, nxt, err = status_of(low)
    assert (s, attempts, err) == ("pending", 0, None) and nxt > datetime.utcnow() + timedelta(minutes=30)   # waits, no attempt burned
    db = SessionLocal(); db.query(NotificationDelivery).filter_by(alert_id=low).update({"next_attempt_at": datetime.utcnow() - timedelta(seconds=1)})
    db.query(NotificationChannel).update({"quiet_start": None, "quiet_end": None}); db.commit(); db.close()
    assert dispatch()["sent"] == 1 and status_of(low)[0] == "sent"


def test_prefs_update_and_clear(client, tenant_a, http):
    tok = owner(client, tenant_a); h = bearer(tok)
    cid = add_channel(client, tok, tenant_a, channel_type="email", target="a@example.com").json()["id"]
    u = f"/api/notifications/channels/{cid}?tenant_id={tenant_a}"
    r = client.patch(u, json={"quiet_start": "22:00", "quiet_end": "06:00", "digest_mode": "daily", "digest_hour": 5,
                              "filter_kinds": ["trend"], "filter_entities": ["sale"]}, headers=h).json()
    assert (r["quiet_start"], r["digest_mode"], r["digest_hour"], r["filter_kinds"]) == ("22:00", "daily", 5, ["trend"])
    assert client.patch(u, json={"quiet_start": "23:00"}, headers=h).status_code == 200               # end kept from before
    assert client.patch(u, json={"quiet_end": None, "quiet_start": "23:00"}, headers=h).status_code == 400
    assert client.patch(u, json={"clear_quiet_hours": True, "clear_filters": True}, headers=h).json()["quiet_start"] is None
    r = client.get(f"/api/notifications/channels?tenant_id={tenant_a}", headers=h).json()[0]
    assert r["filter_kinds"] is None and r["filter_entities"] is None
    assert client.patch(u, json={"digest_hour": 99}, headers=h).status_code == 400
    # a webhook channel can't be switched to digest mode
    wid = add_channel(client, tok, tenant_a, channel_type="webhook", target=http + "/w").json()["id"]
    assert client.patch(f"/api/notifications/channels/{wid}?tenant_id={tenant_a}", json={"digest_mode": "daily"}, headers=h).status_code == 400


# ---------- digests ----------
def _digest_channel(client, tok, tenant, **kw):
    return add_channel(client, tok, tenant, min_severity="informational", digest_mode="daily", digest_hour=0, **kw).json()["id"]


def test_digest_groups_low_urgency_and_sends_urgent_immediately(client, tenant_a, smtp):
    tok = owner(client, tenant_a); smtp.messages.clear()
    cid = _digest_channel(client, tok, tenant_a, target="digest@example.com")
    urgent = make_alert(tenant_a, AlertSeverity.critical, "Urgent thing")
    m1 = make_alert(tenant_a, AlertSeverity.medium, "Medium A"); m2 = make_alert(tenant_a, AlertSeverity.opportunity, "Opp B")
    st = dispatch()
    assert st["sent"] == 1 and st["held"] == 2 and st["digests"] == 1
    subjects = [re.search(rb"Subject: (.*)", m.content).group(1).decode() for m in smtp.messages]
    assert any("Urgent thing" in s for s in subjects) and any(s.startswith("Daily digest: 2 updates") for s in subjects)
    body = [_text(m) for m in smtp.messages if b"Daily digest" in m.content][0]
    assert "Medium A" in body and "Opp B" in body and "Urgent thing" not in body
    assert status_of(m1)[0] == "sent" and status_of(m2)[0] == "sent"
    n = len(smtp.messages)
    dispatch(); assert len(smtp.messages) == n                       # once per day: no second digest


def test_digest_waits_for_its_hour_and_does_not_resend(client, tenant_a, http):
    tok = owner(client, tenant_a)
    cid = add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s", min_severity="informational",
                      digest_mode="daily", digest_hour=9).json()["id"]
    a = make_alert(tenant_a, AlertSeverity.medium, "Later"); 
    db = SessionLocal(); N.enqueue_deliveries(db); N.process_deliveries(db)
    day = datetime(2030, 1, 1)
    assert N.send_due_digests(db, day.replace(hour=8)) == 0 and not _Recv.got
    assert N.send_due_digests(db, day.replace(hour=9, minute=5)) == 1
    assert "Daily digest: 1 update" in json.loads(_Recv.got[-1]["body"])["text"]
    assert N.send_due_digests(db, day.replace(hour=10)) == 0 and len(_Recv.got) == 1
    b = make_alert(tenant_a, AlertSeverity.medium, "Next day"); N.enqueue_deliveries(db); N.process_deliveries(db)
    assert N.send_due_digests(db, day.replace(day=2, hour=9)) == 1 and len(_Recv.got) == 2
    db.close()


def test_digest_failure_keeps_items_and_retries_later(client, tenant_a, http):
    tok = owner(client, tenant_a)
    add_channel(client, tok, tenant_a, channel_type="teams", target=http + "/t", min_severity="informational", digest_mode="daily", digest_hour=0)
    a = make_alert(tenant_a, AlertSeverity.informational, "Keep me")
    db = SessionLocal(); N.enqueue_deliveries(db); N.process_deliveries(db)
    now = datetime(2030, 1, 1, 10, 0); _Recv.status = 500
    assert N.send_due_digests(db, now) == 0 and status_of(a)[0] == "held" and "digest" in status_of(a)[3]
    _Recv.status = 200
    assert N.send_due_digests(db, now + timedelta(minutes=5)) == 0            # throttled
    assert N.send_due_digests(db, now + timedelta(minutes=31)) == 1 and status_of(a)[0] == "sent"
    db.close()


def test_two_workers_never_send_the_same_digest(client, tenant_a, http):
    tok = owner(client, tenant_a)
    add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s", min_severity="informational", digest_mode="daily", digest_hour=0)
    for i in range(3): make_alert(tenant_a, AlertSeverity.medium, f"M{i}")
    db = SessionLocal(); N.enqueue_deliveries(db); N.process_deliveries(db); db.close()
    res = []
    def go():
        d = SessionLocal()
        try: res.append(N.send_due_digests(d, datetime(2030, 1, 1, 12)))
        finally: d.close()
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(res) == 1 and len(_Recv.got) == 1


def test_turning_digest_off_releases_held_alerts(client, tenant_a, http):
    tok = owner(client, tenant_a)
    cid = add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s", min_severity="informational", digest_mode="daily", digest_hour=23).json()["id"]
    a = make_alert(tenant_a, AlertSeverity.medium, "Held one")
    dispatch(); assert status_of(a)[0] == "held" and not _Recv.got
    client.patch(f"/api/notifications/channels/{cid}?tenant_id={tenant_a}", json={"digest_mode": "off"}, headers=bearer(tok))
    st = dispatch(); assert st["released"] == 1 and st["sent"] == 1 and status_of(a)[0] == "sent"


# ---------- filters ----------
def test_kind_and_entity_filters(client, tenant_a, http):
    tok = owner(client, tenant_a)
    add_channel(client, tok, tenant_a, channel_type="slack", target=http + "/s", filter_kinds=["trend"], filter_entities=["sale"])
    db = SessionLocal()
    def mk(kind, metric):
        a = Alert(tenant_id=tenant_a, severity=AlertSeverity.critical, title=f"{kind}-{metric}", what_happened="x", kind=kind,
                  evidence={"metric": metric} if metric else {})
        db.add(a); db.commit(); return a.id
    ok1, wrong_kind, wrong_entity, unknown_entity = mk("trend", "sale.amount"), mk("deviation", "sale.amount"), mk("trend", "expense.amount"), mk("trend", None)
    db.close()
    st = dispatch(); assert st["sent"] == 2 and st["skipped"] == 2
    assert status_of(ok1)[0] == "sent" and status_of(unknown_entity)[0] == "sent"       # undeterminable entity is never hidden
    assert status_of(wrong_kind)[0] == "skipped" and status_of(wrong_entity)[0] == "skipped"


# ---------- forgot / reset ----------
def _text(m):
    import email
    return email.message_from_bytes(m.content).get_payload(decode=True).decode()     # undo quoted-printable


def _mail_tokens(smtp):
    out = []
    for m in smtp.messages:
        found = re.search(r"token=([\w-]+)", _text(m))
        if found: out.append((m.rcpt_tos[0], found.group(1)))
    return out


def test_forgot_password_end_to_end(client, tenant_a, smtp):
    email, pw, tok = register(client, tenant_a, "owner"); smtp.messages.clear()
    r1 = client.post("/api/auth/forgot-password", json={"email": email})
    r2 = client.post("/api/auth/forgot-password", json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com"})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()                     # no enumeration
    mails = _mail_tokens(smtp); assert len(mails) == 1 and mails[0][0] == email
    token = mails[0][1]
    db = SessionLocal(); row = db.query(PasswordResetToken).filter_by(purpose="reset").order_by(PasswordResetToken.created_at.desc()).first()
    assert row.token_hash != token and len(row.token_hash) == 64; db.close()                      # only a hash is stored
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "short"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "x" * 80}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "brand-new-pass-9"}).status_code == 200   # typo did not burn it
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "another-pass-10"}).status_code == 400    # single use
    assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "brand-new-pass-9"}).status_code == 200
    assert client.get("/api/tenants/me", headers=bearer(tok)).status_code == 401                  # old session revoked


def test_reset_token_expiry_and_supersession(client, tenant_a, smtp):
    email, _, _ = register(client, tenant_a, "owner"); smtp.messages.clear()
    client.post("/api/auth/forgot-password", json={"email": email}); first = _mail_tokens(smtp)[-1][1]
    client.post("/api/auth/forgot-password", json={"email": email}); second = _mail_tokens(smtp)[-1][1]
    assert first != second
    assert client.post("/api/auth/reset-password", json={"token": first, "new_password": "valid-pass-123"}).status_code == 400   # voided by newer
    db = SessionLocal(); db.query(PasswordResetToken).update({"expires_at": datetime.utcnow() - timedelta(seconds=1)}); db.commit(); db.close()
    assert client.post("/api/auth/reset-password", json={"token": second, "new_password": "valid-pass-123"}).status_code == 400   # expired
    assert client.post("/api/auth/reset-password", json={"token": "garbage", "new_password": "valid-pass-123"}).status_code == 400


def test_reset_token_cannot_be_spent_twice_concurrently(client, tenant_a, smtp):
    email, _, _ = register(client, tenant_a, "owner"); smtp.messages.clear()
    client.post("/api/auth/forgot-password", json={"email": email}); token = _mail_tokens(smtp)[-1][1]
    from app.core import recovery
    res = []
    def go():
        d = SessionLocal()
        try: res.append(recovery.consume_token(d, token))
        finally: d.close()
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in res if r) == 1


def test_forgot_password_is_throttled_per_address(client, tenant_a, smtp):
    email, _, _ = register(client, tenant_a, "owner"); smtp.messages.clear()
    from app.core import rate_limit as rl
    for _ in range(6):
        rl.auth_limiter._state.clear()
        assert client.post("/api/auth/forgot-password", json={"email": email}).status_code == 200
    assert len(_mail_tokens(smtp)) == 3                                                          # 3/hour per address


def test_forgot_password_without_smtp_still_answers_the_same(client, tenant_a, monkeypatch):
    email, _, _ = register(client, tenant_a, "owner"); monkeypatch.delenv("SMTP_HOST")
    r = client.post("/api/auth/forgot-password", json={"email": email}); assert r.status_code == 200 and r.json()["ok"]


# ---------- invitations ----------
def test_invitation_flow(client, tenant_a, smtp):
    _, _, o = register(client, tenant_a, "owner"); _, _, s = register(client, tenant_a, "staff"); smtp.messages.clear()
    new = f"{uuid.uuid4().hex[:8]}@example.com"
    body = {"tenant_id": tenant_a, "email": new, "role": "staff"}
    assert client.post("/api/users/invite", json=body, headers=bearer(s)).status_code == 403
    r = client.post("/api/users/invite", json=body, headers=bearer(o)); assert r.status_code == 200, r.text
    assert "temporary_password" not in r.json() and r.json()["must_change_password"] is True
    assert client.post("/api/users/invite", json=body, headers=bearer(o)).status_code == 409          # already exists
    (rcpt, token), = _mail_tokens(smtp); assert rcpt == new
    assert "accept-invite" in _text(smtp.messages[0])
    assert client.post("/api/auth/login", json={"email": new, "password": "anything-1234"}).status_code == 401   # cannot sign in yet
    assert client.post("/api/auth/reset-password", json={"token": token, "new_password": "my-first-pass-1"}).status_code == 200
    login = client.post("/api/auth/login", json={"email": new, "password": "my-first-pass-1"}); assert login.status_code == 200
    assert login.json()["must_change_password"] is False
    assert client.post(f"/api/users/{r.json()['id']}/resend-invite?tenant_id={tenant_a}", headers=bearer(o)).status_code == 400   # accepted already


def test_resend_invite_voids_previous_link(client, tenant_a, smtp):
    _, _, o = register(client, tenant_a, "owner"); smtp.messages.clear()
    new = f"{uuid.uuid4().hex[:8]}@example.com"
    uid = client.post("/api/users/invite", json={"tenant_id": tenant_a, "email": new}, headers=bearer(o)).json()["id"]
    first = _mail_tokens(smtp)[-1][1]
    assert client.post(f"/api/users/{uid}/resend-invite?tenant_id={tenant_a}", headers=bearer(o)).status_code == 200
    second = _mail_tokens(smtp)[-1][1]; assert first != second
    assert client.post("/api/auth/reset-password", json={"token": first, "new_password": "valid-pass-123"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": second, "new_password": "valid-pass-123"}).status_code == 200


def test_invite_requires_mail_and_respects_tenant_boundaries(client, tenant_a, tenant_b, monkeypatch, smtp):
    _, _, oa = register(client, tenant_a, "owner"); _, _, ob = register(client, tenant_b, "owner")
    assert client.post("/api/users/invite", json={"tenant_id": tenant_a, "email": "x@example.com"}, headers=bearer(ob)).status_code == 403
    uid = client.post("/api/users/invite", json={"tenant_id": tenant_a, "email": f"{uuid.uuid4().hex[:6]}@example.com"}, headers=bearer(oa)).json()["id"]
    assert client.post(f"/api/users/{uid}/resend-invite?tenant_id={tenant_b}", headers=bearer(ob)).status_code == 404
    monkeypatch.delenv("SMTP_HOST")
    assert client.post("/api/users/invite", json={"tenant_id": tenant_a, "email": "y@example.com"}, headers=bearer(oa)).status_code == 409


def test_deleting_user_removes_their_tokens(client, tenant_a, smtp):
    _, _, o = register(client, tenant_a, "owner")
    uid = client.post("/api/users/invite", json={"tenant_id": tenant_a, "email": f"{uuid.uuid4().hex[:6]}@example.com"}, headers=bearer(o)).json()["id"]
    assert client.delete(f"/api/users/{uid}?tenant_id={tenant_a}", headers=bearer(o)).status_code == 200
    db = SessionLocal(); assert db.query(PasswordResetToken).filter_by(user_id=uid).count() == 0; db.close()
