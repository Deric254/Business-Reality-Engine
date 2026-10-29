"""The check tools in scripts/ must keep matching the product they check."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db import engine
from tests.test_block_c import make_owner, mk

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_payment_simulator_builds_what_the_mpesa_source_accepts_and_counts_a_repeat_once(client):
    sim = load("mpesa_simulate")
    t, h = make_owner(client, "sim")
    with engine.begin() as c:
        c.execute(text("update tenants set timezone = 'Africa/Nairobi' where id = :t"), {"t": t})
    c = mk(client, t, h, connector_type="webhook", name="Paybill", config={"preset": "mpesa_c2b"}).json()
    from app.connectors.mpesa import sample_c2b_payload
    payload = sample_c2b_payload(150, "SIMTEST01")
    for _ in range(2):
        r = client.post(c["webhook_url"], json=payload)
        assert r.status_code == 200 and r.json()["ResultCode"] == 0
    with engine.connect() as conn:
        rows = conn.execute(text("select amount, timestamp from business_events where connector_id = :c"), {"c": c["id"]}).all()
    assert len(rows) == 1 and float(rows[0].amount) == 150.0
    assert abs((dt.datetime.utcnow() - rows[0].timestamp).total_seconds()) < 60          # East Africa Time was read as East Africa Time


def test_the_ai_checker_reports_success_non_json_and_failure_without_leaking_the_key(monkeypatch, capsys):
    check = load("check_ai")

    class Stub:
        def __init__(self, reply):
            self.reply = reply

        def name(self):
            return "stub"

        def generate_json(self, *_a, **_k):
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    def run(reply):
        monkeypatch.setattr(check, "build_provider", lambda *a, **k: Stub(reply))
        monkeypatch.setattr(sys, "argv", ["check_ai", "--kind", "anthropic", "--key", "sk-secret-123"])
        code = check.main()
        return code, capsys.readouterr().out

    code, out = run('{"intent": "top_products"}')
    assert code == 0 and "OK" in out
    code, out = run("Sure! Here is your plan")
    assert code == 1 and "not JSON" in out
    code, out = run(RuntimeError("401 bad key sk-secret-123"))
    assert code == 1 and "sk-secret-123" not in out and "FAILED" in out
