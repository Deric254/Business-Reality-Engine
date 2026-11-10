"""Money at stake: only a real, measured gap ever becomes an amount, the total never counts predictions or risks,
and one business can never see another's figures."""
import datetime as dt
import math

from app.core.clock import utcnow
from app.core.db import SessionLocal
from app.core.value_at_stake import at_stake
from app.models.alerts import Alert, AlertSeverity, AlertState
from tests.test_block_c import make_owner


def test_each_kind_of_gap_becomes_the_right_amount():
    assert at_stake("deviation", {"metric": "sale.amount", "current": 600, "baseline": 1000, "direction": "drop"}) == 400
    assert at_stake("deviation", {"metric": "expense.amount", "current": 1500.5, "baseline": 1000, "direction": "rise"}) == 500.5
    assert at_stake("category", {"metric": "expense.amount", "current": 150000, "usual": 100000}) == 50000
    assert at_stake("branch", {"metric": "sale.amount", "current": 60000, "usual": 90000}) == 30000
    assert at_stake("customers", {"monthly_value": 12000.0}) == 12000


def test_good_news_counts_and_non_money_gaps_never_become_an_amount():
    assert at_stake("deviation", {"metric": "sale.amount", "current": 1200, "baseline": 1000, "direction": "drop"}) is None        # sales above usual is not a loss
    assert at_stake("category", {"metric": "expense.amount", "current": 80, "usual": 100}) is None                                   # spending below usual is not a cost
    assert at_stake("deviation", {"metric": "sale.quantity", "current": 1, "baseline": 100, "direction": "drop"}) is None            # a count is not money
    for kind in ("forecast", "trend", "risk", "opportunity", "concentration", "silence", "payment", "daily", "threshold", "margin", "unknown", None):
        assert at_stake(kind, {"metric": "sale.amount", "current": 1, "baseline": 1000, "usual": 1000, "monthly_value": 5000, "direction": "drop"}) is None, kind


def test_bad_evidence_is_never_an_error_or_an_amount():
    for bad in (None, {}, "x", [], {"metric": "sale.amount"}, {"metric": "sale.amount", "current": "a", "baseline": 1, "direction": "drop"},
                {"metric": "sale.amount", "current": math.nan, "baseline": 1000, "direction": "drop"}, {"metric": "sale.amount", "current": True, "baseline": 1000}):
        for kind in ("deviation", "category", "branch", "customers"):
            assert at_stake(kind, bad) is None
    assert at_stake("customers", {"monthly_value": math.inf}) is None and at_stake("customers", {"monthly_value": 0}) is None


def _alert(db, tid, kind, ev, state=AlertState.detected, **kw):
    a = Alert(tenant_id=tid, severity=AlertSeverity.medium, state=state, title=f"{kind} alert", what_happened="x", kind=kind, evidence=ev, **kw)
    db.add(a); db.commit()
    return a


def test_endpoint_totals_what_is_open_and_never_counts_closed_or_unmeasurable_alerts(client):
    t, h = make_owner(client, "valapi")
    db = SessionLocal()
    old = (utcnow() - dt.timedelta(days=3)).isoformat()
    _alert(db, t, "deviation", {"metric": "sale.amount", "current": 600, "baseline": 1000, "direction": "drop", "dedup_key": "d"})           # 400
    _alert(db, t, "category", {"metric": "expense.amount", "current": 150, "usual": 100, "dedup_key": "c"})                                  # 50
    _alert(db, t, "forecast", {"metric": "sale.amount", "current": 1, "baseline": 1000, "dedup_key": "f"})                                    # open, but not a measured gap
    _alert(db, t, "branch", {"metric": "sale.amount", "current": 10, "usual": 900, "dedup_key": "b"}, state=AlertState.dismissed)            # closed: not at stake
    _alert(db, t, "customers", {"monthly_value": 1000.0, "recovered_at": old, "dedup_key": "k"}, state=AlertState.resolved)                   # went back to normal on its own
    _alert(db, t, "customers", {"monthly_value": 7.0, "dedup_key": "m"}, state=AlertState.resolved)                                           # closed by a person: not "back to normal"
    r = client.get(f"/api/alerts/value?tenant_id={t}", headers=h)
    assert r.status_code == 200
    v = r.json()
    assert v["open_count"] == 3 and v["open_measured"] == 2 and v["open_at_stake"] == 450
    assert v["back_to_normal"] == 1 and v["days"] == 30
    assert v["raised"] == 6 and v["raised_at_stake"] == 400 + 50 + 10 * 0 + 900 - 10 + 1000 + 7                                              # everything raised this month, measured the same way
    one = client.get(f"/api/alerts/?tenant_id={t}&state=open", headers=h).json()
    assert sorted(x["at_stake"] for x in one if x["at_stake"] is not None) == [50, 400] and any(x["at_stake"] is None for x in one)


def test_a_quiet_business_gets_zeroes_not_an_error(client):
    t, h = make_owner(client, "valzero")
    v = client.get(f"/api/alerts/value?tenant_id={t}", headers=h)
    assert v.status_code == 200 and v.json() == {"days": 30, "open_count": 0, "open_measured": 0, "open_at_stake": 0, "raised": 0, "raised_at_stake": 0, "back_to_normal": 0, "currency": "KES"}


def test_one_business_never_sees_anothers_money(client):
    a, ha = make_owner(client, "valA")
    b, hb = make_owner(client, "valB")
    db = SessionLocal()
    _alert(db, b, "deviation", {"metric": "sale.amount", "current": 0, "baseline": 777777, "direction": "drop", "dedup_key": "secret"})
    assert client.get(f"/api/alerts/value?tenant_id={a}", headers=ha).json()["open_at_stake"] == 0                                           # A's own total holds none of B's money
    assert client.get(f"/api/alerts/value?tenant_id={b}", headers=ha).status_code == 403                                                     # A cannot ask for B's
    assert "777777" not in client.get(f"/api/alerts/?tenant_id={a}", headers=ha).text
    assert client.get(f"/api/alerts/value?tenant_id={b}").status_code in (401, 403)                                                          # nobody logged in gets nothing
    assert client.get(f"/api/alerts/value?tenant_id={b}", headers={"Authorization": "Bearer nope"}).status_code in (401, 403)
    assert client.get(f"/api/alerts/value?tenant_id={b}", headers=hb).json()["open_at_stake"] == 777777


def test_the_value_route_is_not_swallowed_by_the_alert_id_route(client):
    t, h = make_owner(client, "valroute")
    assert client.get(f"/api/alerts/value?tenant_id={t}", headers=h).status_code == 200
    import uuid
    assert client.get(f"/api/alerts/{uuid.uuid4()}?tenant_id={t}", headers=h).status_code == 404
