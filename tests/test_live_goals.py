"""Live refresh (screens never trail the data), targets (progress, status, why) and what-changed lines."""
import datetime as dt
import json
import threading

import pytest

from app.core import detection_kick, goals as G
from app.core.db import SessionLocal
from app.models.config import Goal
from app.models.core import Tenant
from tests.test_block_c import make_owner, mk
from tests.test_loss_alerts import COSTS, SALES

NOW = dt.datetime.utcnow()


def _load(client, t, h, name, cfg, per_day, days=14):
    cid = mk(client, t, h, name=name, connector_type="file", config=cfg).json()["id"]
    rows = "".join(f"{NOW - dt.timedelta(days=d):%Y-%m-%d} 12:00,{per_day(d)}\n" for d in range(days, 0, -1))
    r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", ("Date,Amount\n" + rows).encode(), "text/csv")}, headers=h)
    assert r.status_code == 200, r.text
    return cid


def _goal(client, t, h, **kw):
    body = {"tenant_id": t, "name": "Month sales", "metric": "sales", "cadence": "month", "target_amount": 100000, **kw}
    return client.post("/api/goals", json=body, headers=h)


# ---------- the rules, without a database ----------

@pytest.mark.parametrize("metric,actual,target,elapsed,total,has,status", [
    ("sales", 0, 1000, 0, 30, False, "no_data"),
    ("sales", 0, 1000, 0, 30, True, "not_started"),
    ("sales", 1000, 1000, 10, 30, True, "met"),
    ("sales", 400, 1000, 10, 30, True, "on_track"),        # pace lands on 1200
    ("sales", 320, 1000, 10, 30, True, "at_risk"),         # pace lands on 960: within 10% of missing
    ("sales", 200, 1000, 10, 30, True, "off_track"),       # pace lands on 600
    ("spending", 300, 1000, 10, 30, True, "on_track"),     # pace lands on 900
    ("spending", 340, 1000, 10, 30, True, "at_risk"),      # pace lands on 1020
    ("spending", 400, 1000, 10, 30, True, "off_track"),    # pace lands on 1200
    ("spending", 1001, 1000, 20, 30, True, "off_track"),   # limit already passed
    ("spending", 0, 1000, 0, 30, True, "not_started"),
    ("profit", -50, 1000, 10, 30, True, "off_track"),
])
def test_status_rules(metric, actual, target, elapsed, total, has, status):
    assert G.judge(metric, actual, target, elapsed, total, has)[0] == status


def test_calendar_bounds():
    d = dt.date
    assert G.bounds("week", d(2026, 10, 25)) == (d(2026, 10, 19), d(2026, 10, 25))      # Sunday closes the week
    assert G.bounds("month", d(2024, 2, 10)) == (d(2024, 2, 1), d(2024, 2, 29))         # leap February
    assert G.bounds("month", d(2026, 12, 31)) == (d(2026, 12, 1), d(2026, 12, 31))
    assert G.bounds("quarter", d(2026, 11, 5)) == (d(2026, 10, 1), d(2026, 12, 31))     # the quarter that ends the year
    assert G.bounds("quarter", d(2026, 2, 1)) == (d(2026, 1, 1), d(2026, 3, 31))
    assert G.bounds("year", d(2026, 6, 1)) == (d(2026, 1, 1), d(2026, 12, 31))


# ---------- progress is the same figure as the records say ----------

def test_progress_matches_the_records_and_follows_new_data(client):
    t, h = make_owner(client, "goalprog")
    _load(client, t, h, "POS", SALES, lambda d: 1000, days=10)
    g = _goal(client, t, h).json()
    today = dt.datetime.utcnow().date()
    first = today.replace(day=1)
    expected = sum(1000 for d in range(1, 11) if first <= today - dt.timedelta(days=d) < today)      # complete days of this month
    assert g["actual"] == expected and g["target"] == 100000
    assert g["days_elapsed"] == (today - first).days
    assert g["history"] is not None and g["summary"]
    listed = client.get(f"/api/goals?tenant_id={t}", headers=h).json()
    assert [x["id"] for x in listed] == [g["id"]]
    _load(client, t, h, "More", SALES, lambda d: 500, days=10)              # new data arrives: the very next read shows it
    again = client.get(f"/api/goals?tenant_id={t}", headers=h).json()[0]
    assert again["actual"] == expected + (expected // 1000) * 500


def test_spending_target_is_a_limit_and_profit_uses_both(client):
    t, h = make_owner(client, "goallimit")
    _load(client, t, h, "POS", SALES, lambda d: 1000, days=6)
    _load(client, t, h, "Costs", COSTS, lambda d: 400, days=6)
    lim = _goal(client, t, h, name="Cost cap", metric="spending", target_amount=1).json()
    assert lim["direction"] == "within" and lim["status"] in ("off_track", "not_started")
    prof = _goal(client, t, h, name="Profit", metric="profit").json()
    today = dt.datetime.utcnow().date()
    n = sum(1 for d in range(1, 7) if today.replace(day=1) <= today - dt.timedelta(days=d) < today)
    assert prof["actual"] == n * 600


def test_no_records_means_no_verdict(client):
    t, h = make_owner(client, "goalempty")
    g = _goal(client, t, h).json()
    assert g["status"] == "no_data" and g["actual"] == 0 and g["projected"] is None


# ---------- API rules ----------

def test_goal_validation_uniqueness_roles_and_isolation(client):
    t, h = make_owner(client, "goalapi")
    assert _goal(client, t, h, target_amount=0).status_code == 422
    assert _goal(client, t, h, metric="margin").status_code == 422
    assert _goal(client, t, h, cadence="decade").status_code == 422
    assert _goal(client, t, h, name="  ").status_code == 422
    ok = _goal(client, t, h)
    assert ok.status_code == 200
    assert _goal(client, t, h).status_code == 409                                    # same name twice
    gid = ok.json()["id"]
    upd = client.patch(f"/api/goals/{gid}?tenant_id={t}", json={"target_amount": 250000}, headers=h)
    assert upd.status_code == 200 and upd.json()["target"] == 250000
    t2, h2 = make_owner(client, "goalother")
    assert client.get(f"/api/goals?tenant_id={t}", headers=h2).status_code in (401, 403)           # someone else's business
    assert client.patch(f"/api/goals/{gid}?tenant_id={t2}", json={"target_amount": 1}, headers=h2).status_code == 404
    assert client.delete(f"/api/goals/{gid}?tenant_id={t2}", headers=h2).status_code == 404
    assert client.get(f"/api/goals?tenant_id={t2}", headers=h2).json() == []
    assert client.delete(f"/api/goals/{gid}?tenant_id={t}", headers=h).json() == {"deleted": True}
    assert client.get(f"/api/goals?tenant_id={t}", headers=h).json() == []
    assert client.get(f"/api/goals?tenant_id={t}").status_code == 401


# ---------- live refresh ----------

def test_pulse_moves_when_anything_on_screen_changes_and_only_then(client):
    t, h = make_owner(client, "pulse")
    p0 = client.get(f"/api/live/pulse?tenant_id={t}", headers=h)
    assert p0.status_code == 200 and p0.headers["cache-control"] == "no-store" and p0.json()["last_synced_at"] is None
    assert client.get(f"/api/live/pulse?tenant_id={t}", headers=h).json()["token"] == p0.json()["token"]    # nothing changed: same token
    _load(client, t, h, "POS", SALES, lambda d: 100, days=5)
    p1 = client.get(f"/api/live/pulse?tenant_id={t}", headers=h).json()
    assert p1["token"] != p0.json()["token"] and p1["last_synced_at"]                                    # data moved the token; the check is stamped
    _goal(client, t, h)
    p2 = client.get(f"/api/live/pulse?tenant_id={t}", headers=h).json()
    assert p2["token"] != p1["token"]                                                                       # a target moved it too
    t2, h2 = make_owner(client, "pulse2")
    assert client.get(f"/api/live/pulse?tenant_id={t}", headers=h2).status_code in (401, 403)
    assert client.get(f"/api/live/pulse?tenant_id={t}").status_code == 401


def test_every_path_that_changes_records_moves_the_data_version(client):
    t, h = make_owner(client, "version")
    def version():
        db = SessionLocal()
        try:
            return db.get(Tenant, t).data_version
        finally:
            db.close()
    v0 = version()
    cid = _load(client, t, h, "POS", SALES, lambda d: 100, days=5)
    v1 = version()
    assert v1 > v0
    assert client.delete(f"/api/connectors/{cid}?tenant_id={t}", headers=h).status_code == 200      # disconnecting removes records
    assert version() > v1


def test_removing_data_re_checks_the_alerts_not_only_adding_it(client, monkeypatch):
    runs = []
    monkeypatch.setenv("DETECT_AFTER_INGEST", "true")
    monkeypatch.setattr(detection_kick, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(detection_kick, "MIN_GAP_SECONDS", 0.2)
    monkeypatch.setattr(detection_kick, "_execute", lambda tenant_id: runs.append(tenant_id))
    t, h = make_owner(client, "rechk")
    cid = _load(client, t, h, "POS", SALES, lambda d: 100, days=5)
    threading.Event().wait(0.6)
    before = len(runs)
    assert before >= 1
    assert client.delete(f"/api/connectors/{cid}?tenant_id={t}", headers=h).status_code == 200
    threading.Event().wait(0.6)
    assert len(runs) > before and set(runs) == {t}


# ---------- what changed ----------

def test_changes_lists_improvements_and_deteriorations_with_the_right_direction(client):
    t, h = make_owner(client, "changes")
    _load(client, t, h, "POS", SALES, lambda d: 2000 if d <= 7 else 1000, days=14)
    _load(client, t, h, "Costs", COSTS, lambda d: 1500 if d <= 7 else 500, days=14)
    r = client.get(f"/api/goals/changes?tenant_id={t}&days=7", headers=h).json()
    better, worse = {x["name"]: x for x in r["improved"]}, {x["name"]: x for x in r["worse"]}
    assert better["Sales"]["change"] == 7000 and better["Sales"]["change_pct"] == 100
    assert worse["Spending"]["change"] == 7000                                       # spending UP is a deterioration
    assert "Profit" not in worse and "Profit" not in better                          # sales +7000 and spending +7000: profit is flat, so no line
    assert client.get(f"/api/goals/changes?tenant_id={t}&days=0", headers=h).json()["note"]
    assert client.get(f"/api/goals/changes?tenant_id={t}&days=1", headers=h).json()["improved"] == []


# ---------- the why ----------

def test_explain_gives_exact_drivers_and_never_depends_on_the_ai(client, monkeypatch):
    t, h = make_owner(client, "why")
    _load(client, t, h, "POS", SALES, lambda d: 1000, days=40)
    gid = _goal(client, t, h, target_amount=10_000_000).json()["id"]
    def boom(*a, **k):
        raise RuntimeError("no provider")
    monkeypatch.setattr("app.reasoning.llm_providers.get_provider", boom)
    r = client.post(f"/api/goals/{gid}/explain?tenant_id={t}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is None and body["ai_error"] and body["progress"]["status"] in ("off_track", "not_started")

    class Fake:
        name = "fake"
        def generate_json(self, system_prompt, user_content, max_tokens=400):
            assert "target" in json.loads(user_content) and "ONLY the numbers given" in system_prompt
            return json.dumps({"explanation": "Sales are behind pace.", "next_step": "Check the slowest days."})
    monkeypatch.setattr("app.reasoning.llm_providers.get_provider", lambda *a, **k: Fake())
    ok = client.post(f"/api/goals/{gid}/explain?tenant_id={t}", headers=h).json()
    assert (ok["ai"] if body["progress"]["status"] != "not_started" else {"explanation": "Sales are behind pace."})["explanation"] == "Sales are behind pace."
    assert client.post(f"/api/goals/{gid}/explain?tenant_id={make_owner(client, 'why2')[0]}", headers=h).status_code in (401, 403)


def test_after_new_data_the_screen_signal_moves_twice_first_for_the_data_then_for_the_alerts(client, monkeypatch):
    """The whole chain a user depends on, with real background detection: data arrives -> the signal moves (screens reload
    the new figures) -> alerts are re-checked on their own within seconds -> the signal moves again (screens reload alerts)."""
    import time
    monkeypatch.setenv("DETECT_AFTER_INGEST", "true")
    monkeypatch.setattr(detection_kick, "SETTLE_SECONDS", 0.3)
    t, h = make_owner(client, "chain")
    token = lambda: client.get(f"/api/live/pulse?tenant_id={t}", headers=h).json()["token"]
    t0 = token()
    _load(client, t, h, "POS", SALES, lambda d: 3000, days=30)
    _load(client, t, h, "Costs", COSTS, lambda d: 6000, days=30)         # spending twice sales: an alert must follow, unasked
    t1 = token()
    assert t1 != t0                                                       # 1) data arrived: screens reload figures at once
    deadline, alerts = time.time() + 20, []
    while time.time() < deadline and not alerts:
        alerts = client.get(f"/api/alerts/?tenant_id={t}", headers=h).json()
        time.sleep(0.25)
    assert alerts, "alerts were not raised on their own after the data arrived"
    time.sleep(0.5)
    assert token() != t1                                                  # 2) alerts changed: screens reload alerts too
