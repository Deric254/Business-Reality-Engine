"""Payment proof: emails a customer's PayPal screenshot to the owner, through Gmail, as a real attachment.

Same behaviour as the owner's Tujengane `api/payment-proof.js`:
  POST /api/payment-proof   JSON body -> 200 {"ok": true}

Environment: GMAIL_APP_PASSWORD (required; a Google app password, spaces ignored), GMAIL_USER (optional, default
dericmarangu@gmail.com), PROOF_TO (optional, default the sender). Without the password the answer is 503 "not_configured".
Public on purpose (an expired business is view-only and must still be able to pay), so it is guarded by a hidden bot field,
a same-origin check, 5 sends per IP per 10 minutes, a body cap of 4 MB and a file cap of 3 MB checked by its first bytes.
"""
import base64
import json
import os
import re
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.rate_limit import _client_ip

router = APIRouter()

DEFAULT_USER = "dericmarangu@gmail.com"
SOURCE = "BRE subscriber"            # who is paying, so the owner can tell these proofs from the learners' and the website customers'
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_SENDS = 5
WINDOW_SECONDS = 10 * 60
MAX_TRACKED_IPS = 2000

_send_log: dict[str, tuple[int, float]] = {}      # ip -> (count, first seen)

FILE_TYPES = {
    "image/jpeg": ("jpg", lambda b: b[:2] == b"\xff\xd8"),
    "image/png": ("png", lambda b: b[:4] == b"\x89PNG"),
    "image/webp": ("webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    "application/pdf": ("pdf", lambda b: b[:4] == b"%PDF"),
}


class _Mailer:
    """One email through Gmail's SMTP server. An object so tests can replace it."""

    def send(self, user: str, password: str, message: EmailMessage) -> None:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)


mailer = _Mailer()


def _reply(status: int, payload: dict, headers: dict | None = None) -> JSONResponse:
    h = {"Cache-Control": "private, no-store, max-age=0", "X-Robots-Tag": "noindex, nofollow"}
    h.update(headers or {})
    return JSONResponse(payload, status_code=status, headers=h)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        return urlparse(origin).netloc == request.headers.get("host")
    except ValueError:
        return False


def _rate_limited(ip: str, now: float) -> bool:
    if len(_send_log) >= MAX_TRACKED_IPS:
        for key in [k for k, (_, first) in _send_log.items() if now - first > WINDOW_SECONDS]:
            del _send_log[key]
        if len(_send_log) >= MAX_TRACKED_IPS:
            _send_log.clear()
    entry = _send_log.get(ip)
    if not entry or now - entry[1] > WINDOW_SECONDS:
        _send_log[ip] = (1, now)
        return False
    _send_log[ip] = (entry[0] + 1, entry[1])
    return entry[0] + 1 > MAX_SENDS


def _line(value, limit: int) -> str:
    return re.sub(r"[\r\n\t]+", " ", "" if value is None else str(value)).strip()[:limit]


def validate(data) -> tuple[dict | None, str | None]:
    """(clean, None) or (None, error code)."""
    if not isinstance(data, dict):
        return None, "bad_request"
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        amount = float("nan")
    try:
        qty = int(float(data.get("qty") or 1))
    except (TypeError, ValueError):
        qty = 1
    clean = {"item": _line(data.get("item"), 120), "amount": amount, "currency": _line(data.get("currency"), 3).upper() or "USD",
             "qty": qty, "name": _line(data.get("name"), 100), "contact": _line(data.get("contact"), 120),
             "txn": _line(data.get("txn"), 60), "extraLabel": _line(data.get("extraLabel"), 40), "extra": _line(data.get("extra"), 80)}
    if not clean["item"] or not clean["name"] or not clean["contact"]:
        return None, "missing_fields"
    if not (0 < amount <= 100000) or not (1 <= qty <= 100):        # NaN fails the first comparison
        return None, "bad_amount"
    f = data.get("file")
    kind = FILE_TYPES.get(f.get("type")) if isinstance(f, dict) else None
    if not kind or not isinstance(f.get("data"), str):
        return None, "bad_file"
    try:
        raw = base64.b64decode(f["data"], validate=False)
    except ValueError:
        return None, "bad_file"
    if not raw or len(raw) > MAX_FILE_BYTES:
        return None, "file_too_large"
    if not kind[1](raw):
        return None, "bad_file"
    clean["file"] = {"type": f["type"], "ext": kind[0], "bytes": raw}
    return clean, None


def _money(c: dict) -> str:
    a = c["amount"]
    return f"{a:,.0f}" if a == int(a) else f"{a:,.2f}"


def email_text(c: dict) -> str:
    lines = [f"A {SOURCE} says they have paid by PayPal.", "", f"Paid from: {SOURCE} (Business Reality Engine)", f"Item: {c['item']}", f"Amount: {_money(c)} {c['currency']}", f"Name: {c['name']}",
             f"Phone or email: {c['contact']}", f"PayPal transaction ID: {c['txn'] or '(not typed, see screenshot)'}"]
    if c["extra"]:
        lines.append(f"{c['extraLabel'] or 'Extra detail'}: {c['extra']}")
    lines += ["", "The payment screenshot is attached. Check it against your PayPal activity before you reply."]
    return "\n".join(lines)


@router.post("")
async def payment_proof(request: Request):
    if not _same_origin(request):
        return _reply(403, {"error": "forbidden"})
    password = re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD", ""))
    user = (os.environ.get("GMAIL_USER") or DEFAULT_USER).strip()
    if not password:
        return _reply(503, {"error": "not_configured"})
    if _rate_limited(_client_ip(request), time.time()):
        return _reply(429, {"error": "too_many"}, {"Retry-After": str(WINDOW_SECONDS)})
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BODY_BYTES:
        return _reply(413, {"error": "file_too_large"})
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return _reply(413, {"error": "file_too_large"})
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        return _reply(400, {"error": "bad_request"})
    if isinstance(data, dict) and data.get("website"):
        return _reply(200, {"ok": True})                 # hidden field filled in: a bot, pretend success
    clean, error = validate(data)
    if error:
        return _reply(413 if error == "file_too_large" else 400, {"error": error})

    msg = EmailMessage()
    msg["From"] = f'"DericBI Payments" <{user}>'
    msg["To"] = (os.environ.get("PROOF_TO") or user).strip()
    times = f" x{clean['qty']}" if clean["qty"] > 1 else ""
    msg["Subject"] = _line(f"PayPal proof [{SOURCE}]: {clean['item']}{times} - {_money(clean)} {clean['currency']} - {clean['name']}", 250)
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean["contact"]):
        msg["Reply-To"] = clean["contact"]
    msg.set_content(email_text(clean))
    main, sub = clean["file"]["type"].split("/")
    msg.add_attachment(clean["file"]["bytes"], maintype=main, subtype=sub, filename=f"payment-proof.{clean['file']['ext']}")
    try:
        mailer.send(user, password, msg)
    except Exception:
        return _reply(502, {"error": "send_failed"})
    return _reply(200, {"ok": True})
