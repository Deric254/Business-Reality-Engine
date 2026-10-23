"""Ask anything about sales, expenses and profit: the AI only PLANS the question; validated SQL answers it."""
import datetime as dt
import json

import pytest

from app.core import business_questions as bq
from app.core.query_plan import QueryPlan, resolve_period
from tests.test_block_c import make_owner, mk

CFG = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total", "location_col": "Branch", "product_id_col": "Product"}
NOW = dt.datetime.utcnow()


class Planner:
    """Stands in for the AI: returns the text it is given and remembers what it was asked."""
    def __init__(self, reply): self.reply, self.calls = reply, []
    def generate_json(self, system_prompt, user_content, max_tokens=400):
        self.calls.append((system_prompt, user_content))
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def _load(client, t, h, rows, cfg=CFG, name="s"):
    cid = mk(client, t, h, name=name, connector_type="file", config=cfg).json()["id"]
    csv = "Date,Total,Branch,Product\n" + "".join(f"{NOW - dt.timedelta(days=d):%Y-%m-%d} 12:00,{a},{b},{p}\n" for d, a, b, p in rows)
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", csv.encode(), "text/csv")}, headers=h).status_code == 200


@pytest.fixture()
def shop(client):
    """This week: Meru 1500, Maua 500. The week before: Meru 1500, Maua 1500. Products: Sugar and Oil at both."""
    t, h = make_owner(client, "qp")
    _load(client, t, h, [(2, 1000, "Meru", "Oil"), (3, 500, "Meru", "Sugar"), (2, 300, "Maua", "Sugar"), (4, 200, "Maua", "Oil"),
                         (9, 1000, "Meru", "Oil"), (10, 500, "Meru", "Sugar"), (9, 1000, "Maua", "Sugar"), (11, 500, "Maua", "Oil")])
    return t, h


def _ask(client, t, h, q, monkeypatch, reply):
    p = Planner(reply)
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: p)
    return client.post("/api/questions/ask", json={"tenant_id": t, "question": q}, headers=h).json(), p


def test_grouping_and_comparison_are_computed_by_sql_not_by_the_ai(client, shop, monkeypatch):
    t, h = shop
    out, p = _ask(client, t, h, "Sales by branch this week against last week", monkeypatch,
                  {"measure": "revenue", "group_by": "branch", "period": "last_7_days", "compare": "previous_period"})
    assert out["answered"] and out["intent"] == "analysis" and out["classified_by"] == "llm_plan" and out["answer_source"] == "deterministic"
    rows = {r["label"]: r for r in out["evidence"]["rows"]}
    assert (rows["Meru"]["value"], rows["Maua"]["value"]) == (1500.0, 500.0)
    assert (rows["Meru"]["change_pct"], rows["Maua"]["change_pct"]) == (0.0, -66.7)
    assert out["evidence"]["total"] == 2000.0 and out["evidence"]["previous_total"] == 3000.0
    assert "Sales by branch, the last 7 days" in out["answer"] and "Maua KES 500 (-67%)" in out["answer"] and "Total KES 2,000 (-33% vs the previous period, KES 3,000)" in out["answer"]
    assert "2000" not in p.calls[0][1] and "1500" not in p.calls[0][1]                             # the AI never saw a number


def test_a_branch_named_in_the_question_filters_and_the_ai_may_misspell_its_case(client, shop, monkeypatch):
    t, h = shop
    out, p = _ask(client, t, h, "Best products at Maua this week", monkeypatch,
                  {"measure": "revenue", "group_by": "product", "period": "last_7_days", "branch": "maua", "order": "desc", "limit": 3})
    assert [(r["label"], r["value"]) for r in out["evidence"]["rows"]] == [("Sugar", 300.0), ("Oil", 200.0)]
    assert "at Maua" in out["answer"] and p.calls and "Maua" in p.calls[0][1]                       # branch names are offered to the AI


def test_an_unknown_branch_is_said_not_guessed(client, shop, monkeypatch):
    t, h = shop
    out, _ = _ask(client, t, h, "Sales at Mombasa by product", monkeypatch, {"measure": "revenue", "group_by": "product", "branch": "Mombasa"})
    assert out["answered"] and "couldn't find a branch called 'Mombasa'" in out["answer"] and "Meru" in out["answer"]


@pytest.mark.parametrize("reply", [{"answerable": False}, "I think sales fell.", RuntimeError("down"), {"measure": "revenu"},
                                   {"measure": "profit", "product": "Oil"}, {"measure": "revenue", "start": "2026-01-01"},
                                   {"measure": "revenue", "period": "last_week", "start": "2026-01-01", "end": "2026-01-02"}])
def test_an_unusable_plan_falls_back_to_the_old_behaviour(client, shop, monkeypatch, reply):
    t, h = shop
    out, _ = _ask(client, t, h, "tell me about the weather", monkeypatch, reply)
    assert out["answered"] is False and out["suggestions"]


def test_when_the_plan_fails_a_matching_fixed_intent_still_answers(client, shop, monkeypatch):
    t, h = shop
    out, p = _ask(client, t, h, "which branch is best, per week", monkeypatch, "not a plan")
    assert p.calls and out["answered"] and out["intent"] == "branches" and out["classified_by"] == "rules"


def test_unknown_fields_in_the_plan_are_ignored_never_executed(client, shop, monkeypatch):
    t, h = shop
    out, _ = _ask(client, t, h, "total sales per branch", monkeypatch,
                  {"measure": "revenue", "group_by": "branch", "period": "last_7_days", "sql": "DROP TABLE business_events", "branch": None})
    assert out["answered"] and out["evidence"]["total"] == 2000.0


def test_a_plain_question_still_needs_no_ai_call(client, shop, monkeypatch):
    t, h = shop
    out, p = _ask(client, t, h, "How are sales this week?", monkeypatch, RuntimeError("must not be called"))
    assert out["answered"] and out["intent"] == "sales" and p.calls == []


def test_the_weekday_question_needs_no_ai_and_uses_the_business_clock(client):
    t, h = make_owner(client, "qp2")
    assert client.patch("/api/tenants/me", json={"timezone": "Africa/Nairobi"}, headers=h).status_code == 200
    sunday = NOW.date() - dt.timedelta(days=(NOW.weekday() + 1) % 7 or 7)             # the latest Sunday before today
    cid = mk(client, t, h, name="s", connector_type="file", config=CFG).json()["id"]
    csv = f"Date,Total,Branch,Product\n{sunday:%Y-%m-%d} 22:30:00+00:00,500,Meru,Oil\n{sunday:%Y-%m-%d} 10:00,100,Meru,Oil\n"       # 22:30 UTC is 01:30 Monday in Nairobi; a time with no zone is Nairobi's own clock
    client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", csv.encode(), "text/csv")}, headers=h)
    out = client.post("/api/questions/ask", json={"tenant_id": t, "question": "Which day of the week is my best?", "period_days": 30}, headers=h).json()
    rows = {r["label"]: r["value"] for r in out["evidence"]["rows"]}
    assert out["intent"] == "weekdays" and rows == {"Sunday": 100.0, "Monday": 500.0}
    assert "Strongest day: Monday; weakest: Sunday" in out["answer"]


def test_periods_start_at_the_business_local_midnight(db_session=None):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Africa/Nairobi")                                                   # UTC+3
    start, end, label = resolve_period(QueryPlan(measure="revenue", period="today"), dt.datetime(2026, 10, 17, 22, 0), tz)       # 01:00 on the 18th in Nairobi
    assert (start, end, label) == (dt.datetime(2026, 10, 17, 21, 0), dt.datetime(2026, 10, 18, 21, 0), "today")
    start, end, _ = resolve_period(QueryPlan(measure="revenue", period="last_month"), dt.datetime(2026, 10, 17, 9, 0), tz)
    assert (start, end) == (dt.datetime(2026, 8, 31, 21, 0), dt.datetime(2026, 9, 30, 21, 0))
    start, end, _ = resolve_period(QueryPlan(measure="revenue", start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31)), dt.datetime(2026, 10, 17), tz)
    assert (start, end) == (dt.datetime(2026, 2, 28, 21, 0), dt.datetime(2026, 3, 31, 21, 0))


def test_plans_are_checked_before_they_are_trusted():
    for bad in ({"measure": "revenue", "start": "2026-05-01", "end": "2026-04-01"}, {"measure": "revenue", "start": "2000-01-01", "end": "2026-01-01"},
                {"measure": "profit", "group_by": "product"}, {"measure": "revenue", "limit": 500}, {"measure": "drop"}):
        with pytest.raises(ValueError):
            QueryPlan(**bad)
    assert QueryPlan(measure="revenue", group_by="day", compare="previous_period").compare == "none"
    assert QueryPlan(measure="revenue").period == "last_30_days"
