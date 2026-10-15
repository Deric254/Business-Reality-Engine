import hashlib, hmac, json, threading, time, uuid, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from aiosmtpd.controller import Controller

from app.core import notifications as N
from app.core.db import SessionLocal
from app.models.alerts import Alert, AlertSeverity
from app.models.config import NotificationChannel, NotificationDelivery
from tests.conftest import ADMIN, bearer, register


# ---------- real receivers ----------
class _Smtp:
    def __init__(self): self.messages = []
    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope); return "250 OK"


@pytest.fixture(scope="module")
def smtp():
    import os, socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    h = _Smtp(); c = Controller(h, hostname="127.0.0.1", port=port); c.start()
    os.environ.update(SMTP_HOST="127.0.0.1", SMTP_PORT=str(port),
                      SMTP_STARTTLS="false", SMTP_FROM="alerts@test.local", APP_BASE_URL="https://app.test")
    yield h
    c.stop()


class _Hook(BaseHTTPRequestHandler):
    received, status = [], 200
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Hook.received.append((dict(self.headers), body))
        self.send_response(_Hook.status); self.end_headers()
    def log_message(self, *a): pass


@pytest.fixture(scope="module")
def hook():
    srv = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import os
    os.environ["ALLOW_PRIVATE_WEBHOOK_TARGETS"] = "true"
    yield f"http://127.0.0.1:{srv.server_port}/hook"
    srv.shutdown()
    os.environ["ALLOW_PRIVATE_WEBHOOK_TARGETS"] = "false"


@pytest.fixture(autouse=True)
def _reset_hook():
    _Hook.received.clear(); _Hook.status = 200
    # dispatch_cycle works across ALL tenants, so leftovers from earlier runs (in this shared test
    # database) would be processed and counted too; start each test with an empty outbox.
    db = SessionLocal()
    db.query(NotificationDelivery).delete(); db.query(NotificationChannel).delete(); db.commit(); db.close()


def make_alert(tenant_id, severity=AlertSeverity.critical, title="Sales dropped"):
    db = SessionLocal()
    a = Alert(tenant_id=tenant_id, severity=severity, title=title, what_happened="Sale amount is 40% below baseline.\nsecond line",
              evidence={}, recommended_action="Check the till.")
    db.add(a); db.commit(); aid = a.id; db.close()
    return aid


def dispatch():
    db = SessionLocal()
    try: return N.dispatch_cycle(db)
    finally: db.close()


def add_channel(client, tok, tenant, **kw):
    body = {"tenant_id": tenant, "channel_type": "email", "target": "boss@example.com", "min_severity": "high", **kw}
    r = client.post("/api/notifications/channels", json=body, headers=bearer(tok)); return r


# ---------- onboarding ----------
def test_admin_creates_tenant_with_owner_and_defaults(client):
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/system/tenants", json={"name": "Duka Ltd", "owner_email": email, "owner_password": "owner-pass-1"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    tok = client.post("/api/auth/login", json={"email": email, "password": "owner-pass-1"}).json()
    m = client.get(f"/api/monitoring/metrics?tenant_id={tid}", headers=bearer(tok)).json()
    assert {(x["entity"], x["field"]) for x in m} == {("sale", "amount"), ("expense", "amount")}
    me = client.get("/api/tenants/me", headers=bearer(tok)).json()
    assert me["timezone"] == "Africa/Nairobi" and me["base_currency"] == "KES"


def test_tenant_creation_guards(client):
    assert client.post("/api/system/tenants", json={"name": "x"}).status_code in (401, 503)
    assert client.post("/api/system/tenants", json={"name": "x", "timezone": "Mars/Base"}, headers=ADMIN).status_code == 400
    assert client.post("/api/system/tenants", json={"name": "x", "owner_email": "a@example.com"}, headers=ADMIN).status_code == 400
    assert client.post("/api/system/tenants", json={"name": "x", "base_currency": "kes"}, headers=ADMIN).status_code == 422


def test_tenant_settings_owner_only(client, tenant_a):
    _, _, owner = register(client, tenant_a, "owner"); _, _, staff = register(client, tenant_a, "staff")
    assert client.patch("/api/tenants/me", json={"timezone": "Europe/London"}, headers=bearer(staff)).status_code == 403
    assert client.patch("/api/tenants/me", json={"timezone": "Europe/London", "base_currency": "GBP"}, headers=bearer(owner)).json()["timezone"] == "Europe/London"
    assert client.patch("/api/tenants/me", json={"timezone": "Nope/Nope"}, headers=bearer(owner)).status_code == 400
    assert client.patch("/api/tenants/me", json={"name": "  "}, headers=bearer(owner)).status_code == 400


def test_monitored_metrics_crud_and_scheduler_uses_them(client, tenant_a, tenant_b):
    from app.core.scheduler import _metrics_for_tenant, _DEFAULT_METRICS
    _, _, owner = register(client, tenant_a, "owner"); _, _, staff = register(client, tenant_a, "staff")
    db = SessionLocal()
    assert _metrics_for_tenant(db, tenant_a) == _DEFAULT_METRICS            # no rows -> defaults
    body = {"tenant_id": tenant_a, "entity": "sale", "field": "quantity", "period_days": 14, "threshold_pct": 25}
    assert client.post("/api/monitoring/metrics", json=body, headers=bearer(staff)).status_code == 403
    r = client.post("/api/monitoring/metrics", json=body, headers=bearer(owner)); assert r.status_code == 200
    mid = r.json()["id"]
    assert client.post("/api/monitoring/metrics", json=body, headers=bearer(owner)).status_code == 409   # duplicate
    for bad in ({"field": "id"}, {"entity": "Sale; DROP"}, {"period_days": 0}, {"threshold_pct": 0}):
        assert client.post("/api/monitoring/metrics", json={**body, "period_days": 3, **bad}, headers=bearer(owner)).status_code == 422
    assert _metrics_for_tenant(db, tenant_a) == [{"entity": "sale", "field": "quantity", "period_days": 14, "baseline_periods": 4, "deterioration_threshold": 0.25,
                                                     "direction": "drop", "absolute_threshold": None}]
    assert client.patch(f"/api/monitoring/metrics/{mid}?tenant_id={tenant_a}", json={"enabled": False}, headers=bearer(owner)).status_code == 200
    assert _metrics_for_tenant(db, tenant_a) == []                          # all disabled -> watches nothing
    _, _, other = register(client, tenant_b, "owner")                       # cross-tenant
    assert client.patch(f"/api/monitoring/metrics/{mid}?tenant_id={tenant_b}", json={"enabled": True}, headers=bearer(other)).status_code == 404
    assert client.get(f"/api/monitoring/metrics?tenant_id={tenant_a}", headers=bearer(other)).status_code == 403
    assert client.delete(f"/api/monitoring/metrics/{mid}?tenant_id={tenant_a}", headers=bearer(owner)).status_code == 200
    db.close()


def test_detection_rejects_non_numeric_field(client, tenant_a):
    _, _, owner = register(client, tenant_a, "owner")
    r = client.post("/api/detection/run/historical-deviation", json={"tenant_id": tenant_a, "field": "metadata_json"}, headers=bearer(owner))
    assert r.status_code == 422


# ---------- channels ----------
def test_channel_auth_validation_and_secret_shown_once(client, tenant_a, tenant_b, hook):
    _, _, owner = register(client, tenant_a, "owner"); _, _, staff = register(client, tenant_a, "staff")
    assert client.post("/api/notifications/channels", json={"tenant_id": tenant_a, "channel_type": "email", "target": "a@example.com"}).status_code == 401
    assert add_channel(client, staff, tenant_a).status_code == 403
    assert add_channel(client, owner, tenant_a, target="not-an-email").status_code == 400
    w = add_channel(client, owner, tenant_a, channel_type="webhook", target=hook)
    assert w.status_code == 200 and len(w.json()["signing_secret"]) > 20
    listed = client.get(f"/api/notifications/channels?tenant_id={tenant_a}", headers=bearer(owner)).json()
    assert listed and all(c.get("signing_secret") is None for c in listed)
    cid = w.json()["id"]
    _, _, other = register(client, tenant_b, "owner")
    assert client.patch(f"/api/notifications/channels/{cid}?tenant_id={tenant_b}", json={"enabled": False}, headers=bearer(other)).status_code == 404
    assert client.delete(f"/api/notifications/channels/{cid}?tenant_id={tenant_a}", headers=bearer(staff)).status_code == 403


def test_ssrf_guard(client, tenant_a, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "false")
    _, _, owner = register(client, tenant_a, "owner")
    for url in ("http://example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/x",
                "https://169.254.169.254/latest/meta-data", "https://user:pw@example.com/x", "https://[::1]/x", "ftp://example.com/x"):
        r = add_channel(client, owner, tenant_a, channel_type="webhook", target=url)
        assert r.status_code == 400, url


# ---------- delivery ----------
def test_email_delivery_end_to_end_and_idempotent(client, tenant_a, smtp):
    _, _, owner = register(client, tenant_a, "owner")
    cid = add_channel(client, owner, tenant_a, min_severity="high").json()["id"]
    before = len(smtp.messages)
    aid = make_alert(tenant_a)
    s = dispatch()
    assert s["enqueued"] == 1 and s["sent"] == 1
    msg = smtp.messages[before]
    body = msg.content.decode()
    assert "Subject: [CRITICAL] Sales dropped" in body and f"https://app.test/alert/{aid}" in body and "Check the till." in body
    assert msg.rcpt_tos == ["boss@example.com"]
    dispatch(); dispatch()
    assert len(smtp.messages) == before + 1                                # never re-sent


def test_severity_floor_and_no_backfill_before_channel_existed(client, tenant_a, smtp):
    _, _, owner = register(client, tenant_a, "owner")
    old = make_alert(tenant_a, title="Older than the channel")
    time.sleep(0.05)
    add_channel(client, owner, tenant_a, min_severity="high")
    before = len(smtp.messages)
    make_alert(tenant_a, AlertSeverity.medium, "Too minor")
    make_alert(tenant_a, AlertSeverity.high, "High enough")
    dispatch()
    subjects = [m.content.decode() for m in smtp.messages[before:]]
    assert len(subjects) == 1 and "High enough" in subjects[0]


def test_header_injection_in_title_is_neutralised(client, tenant_a, smtp):
    _, _, owner = register(client, tenant_a, "owner")
    add_channel(client, owner, tenant_a, min_severity="critical")
    before = len(smtp.messages)
    make_alert(tenant_a, title="Hi\r\nBcc: evil@example.com")
    dispatch()
    m = smtp.messages[before]
    import email
    parsed = email.message_from_bytes(m.content)
    assert "evil@example.com" not in m.rcpt_tos
    assert parsed["Bcc"] is None                                  # no injected header
    assert parsed["Subject"].startswith("[CRITICAL] Hi Bcc:")     # newline collapsed into the subject text


def test_webhook_signed_and_verifiable(client, tenant_a, hook):
    _, _, owner = register(client, tenant_a, "owner")
    ch = add_channel(client, owner, tenant_a, channel_type="webhook", target=hook).json()
    aid = make_alert(tenant_a)
    assert dispatch()["sent"] == 1
    headers, body = _Hook.received[0]
    ts = headers["X-BRE-Timestamp"]
    expected = "sha256=" + hmac.new(ch["signing_secret"].encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(headers["X-BRE-Signature"], expected)
    p = json.loads(body)
    assert p["event"] == "alert.created" and p["alert"]["id"] == aid and p["alert"]["severity"] == "critical"
    assert "evidence" not in p["alert"]                                     # internals are not exported


def test_failing_webhook_retries_with_backoff_then_goes_dead(client, tenant_a, hook):
    _, _, owner = register(client, tenant_a, "owner")
    add_channel(client, owner, tenant_a, channel_type="webhook", target=hook)
    _Hook.status = 500
    aid = make_alert(tenant_a)
    assert dispatch()["retry"] == 1
    db = SessionLocal()
    d = db.query(NotificationDelivery).filter_by(alert_id=aid).one()
    assert d.status == "pending" and d.attempts == 1 and "HTTP 500" in d.last_error
    assert d.next_attempt_at > dt.datetime.utcnow()
    assert dispatch()["retry"] == 0                                          # not due yet -> untouched
    for expected_attempts in (2, 3, 4, 5):
        d.next_attempt_at = dt.datetime.utcnow() - dt.timedelta(seconds=1); db.commit()
        dispatch(); db.refresh(d)
        assert d.attempts == expected_attempts
    assert d.status == "dead"
    _Hook.status = 200
    d.next_attempt_at = dt.datetime.utcnow() - dt.timedelta(seconds=1); db.commit()
    dispatch(); db.refresh(d)
    assert d.status == "dead"                                                # dead stays dead
    db.close()


def test_one_broken_channel_does_not_block_others(client, tenant_a, hook, smtp):
    _, _, owner = register(client, tenant_a, "owner")
    add_channel(client, owner, tenant_a, channel_type="webhook", target=hook)
    add_channel(client, owner, tenant_a, target="ok@example.com")
    _Hook.status = 500
    before = len(smtp.messages)
    make_alert(tenant_a)
    s = dispatch()
    assert s["sent"] == 1 and s["retry"] == 1 and len(smtp.messages) == before + 1


def test_concurrent_dispatchers_never_double_send(client, tenant_a, smtp):
    _, _, owner = register(client, tenant_a, "owner")
    add_channel(client, owner, tenant_a, min_severity="informational")
    before = len(smtp.messages)
    for i in range(12): make_alert(tenant_a, title=f"Alert {i}")
    ts = [threading.Thread(target=dispatch) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(smtp.messages) == before + 12


def test_disabled_channel_and_test_endpoint(client, tenant_a, smtp, hook):
    _, _, owner = register(client, tenant_a, "owner")
    cid = add_channel(client, owner, tenant_a).json()["id"]
    before = len(smtp.messages)
    r = client.post(f"/api/notifications/channels/{cid}/test?tenant_id={tenant_a}", headers=bearer(owner))
    assert r.json() == {"ok": True} and "Test alert" in smtp.messages[before].content.decode()
    client.patch(f"/api/notifications/channels/{cid}?tenant_id={tenant_a}", json={"enabled": False}, headers=bearer(owner))
    make_alert(tenant_a); dispatch()
    assert len(smtp.messages) == before + 1                                  # disabled -> nothing sent
    w = add_channel(client, owner, tenant_a, channel_type="webhook", target=hook).json()["id"]
    _Hook.status = 500
    assert client.post(f"/api/notifications/channels/{w}/test?tenant_id={tenant_a}", headers=bearer(owner)).json()["ok"] is False


def test_deliveries_log_is_tenant_scoped(client, tenant_a, tenant_b, smtp):
    _, _, a = register(client, tenant_a, "owner"); _, _, b = register(client, tenant_b, "owner")
    add_channel(client, a, tenant_a); make_alert(tenant_a); dispatch()
    assert len(client.get(f"/api/notifications/deliveries?tenant_id={tenant_a}", headers=bearer(a)).json()) == 1
    assert client.get(f"/api/notifications/deliveries?tenant_id={tenant_a}", headers=bearer(b)).status_code == 403
    assert client.get(f"/api/notifications/deliveries?tenant_id={tenant_b}", headers=bearer(b)).json() == []
