"""The Analytics page's numbers: every figure must equal what the records add up to, use the Overview's period rules,
and never leak another business's data."""
import datetime as dt

import pytest

from app.core.analytics import build_analytics, is_generic_buyer
from app.core.db import SessionLocal
from app.models.core import BusinessEvent, Connector
from tests.conftest import _make_tenant
from tests.test_block_c import make_owner

TODAY = dt.datetime.utcnow().replace(hour=9, minute=0, second=0, microsecond=0)


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


def _seed(db, t, spec):
    c = Connector(tenant_id=t, name="Books", connector_type="file", expected_frequency="periodic", is_active=True)
    db.add(c); db.commit()
    for days_ago, entity, amount, place, prod, cust in spec:
        db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity=entity, event_type=f"{entity}.x",
                             timestamp=TODAY - dt.timedelta(days=days_ago), amount=amount, quantity=1, location=place, product_id=prod,
                             customer_id=cust, validation_status="validated"))
    db.commit()


SPEC = [
    (2, "sale", 1000, "Meru", "Sugar", "Ann"), (3, "sale", 3000, "Meru", "Rice", "Ann"), (4, "sale", 2000, "Maua", "Sugar", "Walk-in Customer"),
    (5, "sale", 4000, "Maua", "Oil", "Bob"), (6, "expense", 1500, "Meru", "Rent", None), (7, "expense", 500, "Maua", "Fuel", None),
    (40, "sale", 5000, "Meru", "Sugar", "Ann"), (41, "expense", 2000, "Meru", "Rent", None),
]


def test_headline_branches_products_and_spending_add_up(db):
    t = _make_tenant("an1"); _seed(db, t, SPEC)
    a = build_analytics(db, t, 30)
    h = a["headline"]
    assert h["sales"]["current"] == 10_000 and h["spending"]["current"] == 2_000 and h["profit"]["current"] == 8_000
    assert h["margin_pct"]["current"] == 80.0 and h["transactions"]["current"] == 4 and h["customers"]["current"] == 3
    assert h["sales"]["previous"] == 5_000 and h["sales"]["change_pct"] == 100.0
    by = {b["name"]: b for b in a["branches"]}
    assert by["Meru"]["sales"] == 4000 and by["Meru"]["spending"] == 1500 and by["Meru"]["profit"] == 2500 and by["Maua"]["sales"] == 6000
    assert sum(b["share_pct"] for b in a["branches"]) == pytest.approx(100.0, abs=0.2)
    prods = {p["name"]: p for p in a["products"]["top"]}
    assert prods["Sugar"]["amount"] == 3000 and a["products"]["top"][-1]["cumulative_pct"] == 100.0
    assert {c["name"]: c["amount"] for c in a["spending"]["categories"]} == {"Rent": 1500, "Fuel": 500}
    assert a["spending"]["total"] == 2000


def test_customers_count_walk_ins_as_a_share_but_never_as_dependence(db):
    t = _make_tenant("an2"); _seed(db, t, SPEC)
    c = build_analytics(db, t, 30)["customers"]
    assert {x["name"]: x["share_pct"] for x in c["top"]} == {"Ann": 40.0, "Bob": 40.0, "Walk-in Customer": 20.0}      # Ann 1000+3000, Bob 4000
    assert [x["generic"] for x in c["top"] if x["name"] == "Walk-in Customer"] == [True]
    assert c["generic_share_pct"] == 20.0 and c["named_count"] == 2
    assert is_generic_buyer("Walk-in Customer") and not is_generic_buyer("Mama Njeri Wholesale")


def test_all_time_has_no_comparison_and_empty_business_is_all_zeros(db):
    t = _make_tenant("an3"); _seed(db, t, SPEC)
    allt = build_analytics(db, t, 0)["headline"]
    assert allt["sales"]["current"] == 15_000 and allt["sales"]["previous"] is None
    empty = build_analytics(db, _make_tenant("an4"), 30)
    assert empty["headline"]["sales"]["current"] == 0 and empty["branches"] == [] and empty["products"]["top"] == [] and empty["forecast"]["projection"] == []


def test_insights_are_built_from_the_figures(db):
    t = _make_tenant("an5"); _seed(db, t, SPEC)
    titles = " | ".join(i["title"] for i in build_analytics(db, t, 30)["insights"])
    assert "Maua earns the most" in titles or "earns the most" in titles


def test_failed_and_duplicate_records_never_count(db):
    t = _make_tenant("an6"); _seed(db, t, SPEC)
    c = db.query(Connector).filter(Connector.tenant_id == t).first()
    for status in ("failed", "duplicate", "matched"):
        db.add(BusinessEvent(tenant_id=t, connector_id=c.id, source="t", entity="sale", event_type="sale.x", timestamp=TODAY - dt.timedelta(days=1),
                             amount=999_999, validation_status=status))
    db.commit()
    assert build_analytics(db, t, 30)["headline"]["sales"]["current"] == 10_000


def test_forecast_needs_enough_weeks_and_admits_a_poor_fit(db):
    t = _make_tenant("an7")
    spec = [(7 * w + 3, "sale", 10_000 + (4_000 if w % 2 else -3_000) * (1 if w % 3 else -1), "Meru", "Sugar", "Ann") for w in range(10)]
    _seed(db, t, spec)
    f = build_analytics(db, t, 90)["forecast"]
    assert len(f["projection"]) == 4 and f["r_squared"] is not None and f["reliable"] is False and "band" in f["note"]
    assert all(p["low"] <= p["expected"] <= p["high"] for p in f["projection"])
    young = build_analytics(db, _seed_young(db), 90)["forecast"]
    assert young["projection"] == [] and "complete weeks" in young["note"]


def _seed_young(db):
    t = _make_tenant("an8"); _seed(db, t, [(2, "sale", 100, "Meru", "Sugar", "Ann"), (9, "sale", 100, "Meru", "Sugar", "Ann")])
    return t


def test_endpoint_needs_a_sign_in_and_is_tenant_scoped(client, db):
    t, h = make_owner(client, "anapi")
    assert client.get(f"/api/business/analytics?tenant_id={t}&days=30").status_code in (401, 403)
    ok = client.get(f"/api/business/analytics?tenant_id={t}&days=30", headers=h)
    assert ok.status_code == 200 and set(ok.json()) >= {"headline", "monthly", "branches", "products", "spending", "weekdays", "customers", "daily", "forecast", "insights"}
    other = _make_tenant("anapi-other")
    assert client.get(f"/api/business/analytics?tenant_id={other}&days=30", headers=h).status_code in (401, 403, 404)


def test_today_and_last_7_days_are_real_periods(db):
    t = _make_tenant("anatoday")
    _seed(db, t, [(0, "sale", 1000, "A", "Tea", "Ann"), (0, "expense", 400, "A", "Rent", None),
                  (3, "sale", 500, "A", "Tea", "Bob"), (10, "sale", 9000, "A", "Tea", "Cy")])
    one, week = build_analytics(db, t, 1), build_analytics(db, t, 7)
    assert one["period"]["days"] == 1 and one["period"]["first_day"] == one["period"]["last_day"]
    assert one["headline"]["sales"]["current"] == 1000 and one["headline"]["spending"]["current"] == 400
    assert one["insights"] == []                       # today is not over: no "down vs yesterday" verdicts
    assert week["headline"]["sales"]["current"] == 1500 and week["period"]["days"] == 7
