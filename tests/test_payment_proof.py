"""The payment-proof email: same rules as the owner's other sites. No real mail is sent; the mailer is replaced."""
import base64

import pytest

from app.api import payment_proof as P

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
GOOD = {"item": "Business Reality Engine subscription (monthly)", "amount": 45, "currency": "usd", "qty": 1, "name": "Amina", "contact": "amina@example.com",
        "txn": "5TY05013RG002845M", "extraLabel": "Reference", "extra": "LIC-BRE-x-M", "website": "", "file": {"type": "image/png", "data": PNG}}


@pytest.fixture()
def sent(monkeypatch):
    out = []
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("PROOF_TO", raising=False)
    monkeypatch.setattr(P.mailer, "send", lambda u, p, m: out.append((u, p, m)))
    P._send_log.clear()
    yield out
    P._send_log.clear()


def test_sends_the_screenshot_as_an_attachment(client, sent):
    r = client.post("/api/payment-proof", json=GOOD)
    assert r.status_code == 200 and r.json() == {"ok": True}
    user, password, msg = sent[0]
    assert user == "dericmarangu@gmail.com" and password == "abcdefghijklmnop"          # spaces in the app password are ignored
    assert msg["To"] == "dericmarangu@gmail.com" and msg["Reply-To"] == "amina@example.com"
    assert msg["Subject"] == "PayPal proof [BRE subscriber]: Business Reality Engine subscription (monthly) - 45 USD - Amina"
    body = msg.get_body().get_content()
    assert "Paid from: BRE subscriber" in body and "PayPal transaction ID: 5TY05013RG002845M" in body and "Reference: LIC-BRE-x-M" in body
    att = list(msg.iter_attachments())[0]
    assert att.get_filename() == "payment-proof.png" and att.get_content_type() == "image/png"


def test_not_configured_without_the_password(client, monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert client.post("/api/payment-proof", json=GOOD).json() == {"error": "not_configured"}


def test_works_for_a_view_only_business_with_no_sign_in(client, sent):
    assert client.post("/api/payment-proof", json=GOOD).status_code == 200           # public on purpose


@pytest.mark.parametrize("change,code,status", [
    ({"name": ""}, "missing_fields", 400), ({"amount": 0}, "bad_amount", 400), ({"amount": "x"}, "bad_amount", 400), ({"qty": 101}, "bad_amount", 400),
    ({"file": {"type": "text/html", "data": PNG}}, "bad_file", 400),
    ({"file": {"type": "image/png", "data": base64.b64encode(b"not a png at all").decode()}}, "bad_file", 400),
    ({"file": None}, "bad_file", 400),
    ({"file": {"type": "image/png", "data": base64.b64encode(b"\x89PNG" + b"0" * (3 * 1024 * 1024)).decode()}}, "file_too_large", 413),
])
def test_refuses_bad_input(client, sent, change, code, status):
    r = client.post("/api/payment-proof", json={**GOOD, **change})
    assert (r.status_code, r.json()) == (status, {"error": code}) and not sent


def test_bot_field_pretends_success_and_sends_nothing(client, sent):
    assert client.post("/api/payment-proof", json={**GOOD, "website": "http://spam"}).json() == {"ok": True}
    assert not sent


def test_other_origin_and_oversize_body_and_bad_json(client, sent):
    assert client.post("/api/payment-proof", json=GOOD, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/payment-proof", content=b"x" * (4 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/payment-proof", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 400
    assert not sent


def test_five_sends_per_ten_minutes(client, sent):
    codes = [client.post("/api/payment-proof", json=GOOD).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_send_failure_is_a_502_and_headers_are_safe(client, sent, monkeypatch):
    def boom(*a):
        raise OSError("smtp down")
    monkeypatch.setattr(P.mailer, "send", boom)
    r = client.post("/api/payment-proof", json=GOOD)
    assert r.status_code == 502 and r.json() == {"error": "send_failed"}
    assert r.headers["cache-control"] == "private, no-store, max-age=0"


def test_only_post(client):
    assert client.get("/api/payment-proof").status_code == 405
