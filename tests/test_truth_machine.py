"""The dashboard must agree with the sheet, to the shilling, for every period, on every screen.

Ground truth is the user's own TEST_DATA.xlsx (1,042 sales, 539 expenses, 1 Jan - 23 Oct 2026, Africa/Nairobi). The
expected figures below were summed straight from the sheet with pandas, independently of the application code."""
import datetime as dt
import io
import os

import pandas as pd
import pytest

from app.api.business import build_trend
from app.core import periods
from app.core.business_health import health_summary
from app.core.business_questions import compute_profit_breakdown
from app.core.db import SessionLocal
from tests.test_block_c import make_owner, mk

XLSX = os.path.join(os.path.dirname(__file__), "data", "TEST_DATA.xlsx")
NOW = dt.datetime(2026, 10, 23, 14, 20)            # 17:20 in Nairobi on 23 Oct: the day the screenshots were taken


@pytest.fixture(scope="module")
def sheet():
    return pd.read_excel(XLSX, "Sales"), pd.read_excel(XLSX, "Expenses")


@pytest.fixture()
def business(client):
    t, h = make_owner(client, "truth")
    assert client.patch("/api/tenants/me", json={"timezone": "Africa/Nairobi", "base_currency": "KES"}, headers=h).status_code == 200
    for name, sheet_name, ent, amt in (("Sales", "Sales", "sale", "Amount"), ("Expenses", "Expenses", "expense", "Expense Amount")):
        cfg = {"entity": ent, "event_type": f"{ent}.created", "timestamp_col": "Date", "amount_col": amt, "location_col": "Branch",
               "entity_id_col": "Receipt" if ent == "sale" else "Reference"}
        cid = mk(client, t, h, name=name, connector_type="file", config=cfg).json()["id"]
        buf = io.BytesIO(); pd.read_excel(XLSX, sheet_name).to_csv(buf, index=False)
        r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": (f"{name}.csv", buf.getvalue(), "text/csv")}, headers=h)
        assert r.status_code == 200, r.text
    return t


def truth(sheet, first, last):
    s, e = sheet
    a = s[(s.Date >= first) & (s.Date <= last)].Amount.sum()
    b = e[(e.Date >= first) & (e.Date <= last)]["Expense Amount"].sum()
    return float(a), float(b)


@pytest.mark.parametrize("days", [7, 30, 90, 180])
def test_every_period_matches_the_sheet_and_the_previous_period_too(business, sheet, days):
    db = SessionLocal()
    try:
        k = compute_profit_breakdown(db, business, days, now=NOW)
        first, last = pd.Timestamp("2026-10-23") - pd.Timedelta(days=days - 1), pd.Timestamp("2026-10-23")
        sales, spend = truth(sheet, first, last)
        psales, pspend = truth(sheet, first - pd.Timedelta(days=days), first - pd.Timedelta(days=1))
        assert (k["revenue"]["current"], k["expenses"]["current"]) == (sales, spend)
        assert (k["revenue"]["previous"], k["expenses"]["previous"]) == (psales, pspend)
        assert k["profit"]["current"] == sales - spend
        h = health_summary(db, business, days, NOW)["financials"]          # the health strip says the same thing
        assert (h["sales"], h["expenses"], h["profit"]) == (sales, spend, sales - spend)
    finally:
        db.close()


def test_the_figures_in_the_screenshots(business):
    db = SessionLocal()
    try:
        got = {d: compute_profit_breakdown(db, business, d, now=NOW) for d in (7, 30, 90, 180)}
        assert [got[d]["revenue"]["current"] for d in got] == [520950, 2014050, 5828130, 11557380]
        assert [got[d]["expenses"]["current"] for d in got] == [186588, 1002830, 3481006, 6608757]
        assert [got[d]["profit"]["current"] for d in got] == [334362, 1011220, 2347124, 4948623]
    finally:
        db.close()


def test_all_time_is_the_whole_sheet_with_nothing_to_compare(business, sheet):
    db = SessionLocal()
    try:
        k = compute_profit_breakdown(db, business, 0, now=NOW)
        assert (k["revenue"]["current"], k["expenses"]["current"]) == (float(sheet[0].Amount.sum()), float(sheet[1]["Expense Amount"].sum()))
        assert k["revenue"]["previous"] == 0 and k["profit"]["change_pct"] is None
        assert k["period"] == {"days": 296, "all_time": True, "first_day": "2026-01-01", "last_day": "2026-10-23", "timezone": "Africa/Nairobi"}
        t = build_trend(db, business, 0, NOW)
        assert t["previous"] == [] and len(t["current"]) == 296
        assert round(sum(d["sales"] for d in t["current"]), 2) == k["revenue"]["current"]
    finally:
        db.close()


@pytest.mark.parametrize("days", [7, 30, 90, 180])
def test_the_chart_is_on_the_businesss_days_and_adds_up_to_the_totals(business, sheet, days):
    db = SessionLocal()
    try:
        t = build_trend(db, business, days, NOW)
        k = compute_profit_breakdown(db, business, days, now=NOW)
        assert t["current"][-1]["date"] == "2026-10-23" and len(t["current"]) == len(t["previous"]) == days
        assert sum(d["sales"] for d in t["current"]) == k["revenue"]["current"]
        assert sum(d["expenses"] for d in t["current"]) == k["expenses"]["current"]
        assert sum(d["sales"] for d in t["previous"]) == k["revenue"]["previous"]
        by_day = {d["date"]: d for d in t["current"]}
        s = sheet[0]
        for day in ("2026-10-22", "2026-10-23"):            # the day the old chart shifted by one
            want = float(s[s.Date == day].Amount.sum())
            if day in by_day:
                assert by_day[day]["sales"] == want
        assert by_day["2026-10-23"]["sales"] == 138300 and by_day["2026-10-23"]["expenses"] == 886
    finally:
        db.close()


def test_a_profit_alert_that_is_no_longer_true_is_closed(client, business):
    """The 'Spending exceeds sales by 214,267' alert stayed on screen after the week recovered."""
    from app.core.detection import _find_open_alert
    from app.core.detectors import _upsert
    from app.core.detectors_extra import detect_profit_risk
    from app.models.alerts import AlertSeverity
    db = SessionLocal()
    try:
        key = "risk:profit:7d"
        _upsert(db, business, key, NOW, {"metric": "profit.net", "projection": True},
                dict(kind="risk", severity=AlertSeverity.high, title="Spending exceeds sales", what_happened="old", confidence=0.5))
        assert _find_open_alert(db, business, key) is not None
        now = dt.datetime(2026, 10, 22, 21, 0)              # start of 23 Oct in Nairobi: 16-22 Oct are the complete days
        assert detect_profit_risk(db, business, now=now) is None
        assert _find_open_alert(db, business, key) is None
    finally:
        db.close()


# ---- every way of getting the same sheet in must give the same numbers ----
from tests.test_mirror_sync import SHEET  # noqa: E402,F401
from tests.test_url_connector import server  # noqa: E402,F401


def _totals(client, t, h, days):
    k = client.get(f"/api/questions/kpis?tenant_id={t}&period_days={days}", headers=h).json()
    return k["revenue"]["current"], k["expenses"]["current"], k["profit"]["current"]


def test_sheet_link_and_json_api_agree_with_the_file_upload(client, server, business, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "true")
    csvs = {n: pd.read_excel(XLSX, n).to_csv(index=False) for n in ("Sales", "Expenses")}
    # (1) a sheet link, the way a Google Sheet is connected
    t1, h1 = make_owner(client, "viasheet")
    client.patch("/api/tenants/me", json={"timezone": "Africa/Nairobi", "base_currency": "KES"}, headers=h1)
    for n, ent, amt, idc in (("Sales", "sale", "Amount", "Receipt"), ("Expenses", "expense", "Expense Amount", "Reference")):
        SHEET["csv"] = csvs[n]
        cfg = {"url": server + "/sheet.csv", "auth_type": "none", "entity": ent, "event_type": f"{ent}.created", "timestamp_col": "Date",
               "amount_col": amt, "location_col": "Branch", "entity_id_col": idc}
        cid = mk(client, t1, h1, name=n, connector_type="url", config=cfg).json()["id"]
        assert client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t1}", headers=h1).status_code == 200
    # (2) the JSON events API, the way a till or a system pushes records
    t2, h2 = make_owner(client, "viaapi")
    client.patch("/api/tenants/me", json={"timezone": "Africa/Nairobi", "base_currency": "KES"}, headers=h2)
    for n, ent, amt, idc in (("Sales", "sale", "Amount", "Receipt"), ("Expenses", "expense", "Expense Amount", "Reference")):
        cid = mk(client, t2, h2, name=n, connector_type="webhook", config={}).json()["id"]
        df = pd.read_excel(XLSX, n)
        body = [{"tenant_id": t2, "connector_id": cid, "source": n, "entity": ent, "entity_id": r[idc], "event_type": f"{ent}.created",
                 "timestamp": r["Date"].strftime("%Y-%m-%dT%H:%M:%S"), "amount": float(r[amt]), "currency": "KES", "location": r["Branch"]}
                for _, r in df.iterrows()]
        for i in range(0, len(body), 500):
            assert client.post("/api/events/ingest/batch", json=body[i:i + 500], headers=h2).status_code == 200
    # the same figures from all three, for every period, all time included
    for days in (7, 30, 90, 180, 0):
        a, b = _totals(client, t1, h1, days), _totals(client, t2, h2, days)
        assert a == b, (days, a, b)
    assert _totals(client, t1, h1, 0) == (18420870.0, 10243090.0, 8177780.0)
