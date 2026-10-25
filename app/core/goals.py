"""
Targets: is the business meeting what its owner set out to do, and if not, why.

A goal is a figure for the CURRENT calendar week, month, quarter or year on the business's own clock. Progress is
always computed on demand from validated records, so it follows every sync at once and can never disagree with the
Overview or Analytics. It uses COMPLETE days only (today is unfinished; see app/core/periods.py), exactly like alerts.

  reach  (sales, profit)   met when the total so far is at or above the target.
  within (spending)        off track the moment the total passes the target.

For the days still to come the pace so far is carried forward in a straight line:
  on_track   the pace lands on or past the target (reach) / at or under it (within)
  at_risk    the pace lands within 10% of missing it
  off_track  the pace lands further away than that, or a 'within' limit is already passed
Nothing is claimed before there is a complete day to judge by ("not_started"), or before any record exists ("no_data").
"""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core import analytics, periods
from app.core.detection import money_formatter
from app.models.config import Goal
from app.models.core import BusinessEvent as E

METRICS = {"sales": "Sales", "spending": "Spending", "profit": "Profit"}
CADENCES = ("week", "month", "quarter", "year")
AT_RISK_BAND = 0.10
MAX_GOALS = 30
HISTORY_PERIODS = 4


def direction(metric: str) -> str:
    return "within" if metric == "spending" else "reach"


def bounds(cadence: str, day: date) -> tuple[date, date]:
    """First and last day (inclusive) of the calendar week (Monday to Sunday), month, quarter or year holding `day`."""
    if cadence == "week":
        first = day - timedelta(days=day.weekday())
        return first, first + timedelta(days=6)
    if cadence == "month":
        first = day.replace(day=1)
        return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if cadence == "quarter":
        first = date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
        nxt = date(first.year + (first.month == 10), (first.month + 3 - 1) % 12 + 1, 1)
        return first, nxt - timedelta(days=1)
    if cadence == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise ValueError(f"unknown cadence {cadence}")


def _total(db: Session, tenant_id: str, metric: str, start: datetime, end: datetime) -> float:
    if end <= start:
        return 0.0
    sales = analytics._totals(db, tenant_id, "sale", start, end)["amount"] if metric in ("sales", "profit") else 0.0
    spend = analytics._totals(db, tenant_id, "expense", start, end)["amount"] if metric in ("spending", "profit") else 0.0
    return sales if metric == "sales" else spend if metric == "spending" else sales - spend


def _met(metric: str, actual: float, target: float) -> bool:
    return actual <= target if direction(metric) == "within" else actual >= target


def judge(metric: str, actual: float, target: float, elapsed: int, total_days: int, has_data: bool) -> tuple[str, Optional[float]]:
    """(status, projected total at the end of the period). Pure, so the rules can be tested without a database."""
    if not has_data:
        return "no_data", None
    reach = direction(metric) == "reach"
    if reach and actual >= target:
        return "met", actual
    if elapsed <= 0:
        return "not_started", None
    projected = actual / elapsed * total_days
    if not reach and actual > target:
        return "off_track", projected
    if reach:
        return ("on_track" if projected >= target else "at_risk" if projected >= target * (1 - AT_RISK_BAND) else "off_track"), projected
    return ("on_track" if projected <= target else "at_risk" if projected <= target * (1 + AT_RISK_BAND) else "off_track"), projected


def progress(db: Session, goal: Goal, now: Optional[datetime] = None) -> dict:
    tid = goal.tenant_id
    tz = periods.tenant_zone(db, tid)
    today = periods.local_today(db, tid, now)
    first, last = bounds(goal.cadence, today)
    total_days = (last - first).days + 1
    elapsed = (today - first).days                                   # complete days so far
    target = float(goal.target_amount)
    start, end = periods.local_midnight_utc(first, tz), periods.local_midnight_utc(today, tz)
    actual = _total(db, tid, goal.metric, start, end)
    has_data = periods.first_record_day(db, tid, tz) is not None
    status, projected = judge(goal.metric, actual, target, elapsed, total_days, has_data)
    remaining = total_days - elapsed                                 # days still to come, today included
    fmt = money_formatter(db, tid)

    history = []
    cursor = first - timedelta(days=1)
    for _ in range(HISTORY_PERIODS):
        f, l = bounds(goal.cadence, cursor)
        if periods.first_record_day(db, tid, tz) and l < periods.first_record_day(db, tid, tz):
            break                                                    # before the books began: nothing to judge
        got = _total(db, tid, goal.metric, periods.local_midnight_utc(f, tz), periods.local_midnight_utc(l + timedelta(days=1), tz))
        history.append({"first_day": f.isoformat(), "last_day": l.isoformat(), "actual": round(got, 2), "met": _met(goal.metric, got, target)})
        cursor = f - timedelta(days=1)

    gap = None if not has_data else round(actual - target * elapsed / total_days, 2)       # against an even pace
    return {
        "id": goal.id, "name": goal.name, "metric": goal.metric, "metric_label": METRICS[goal.metric], "cadence": goal.cadence,
        "direction": direction(goal.metric), "target": round(target, 2), "actual": round(actual, 2),
        "pct_of_target": round(actual / target * 100, 1) if target else None,
        "status": status, "projected": None if projected is None else round(projected, 2),
        "first_day": first.isoformat(), "last_day": last.isoformat(), "days_total": total_days, "days_elapsed": elapsed,
        "days_remaining": remaining, "data_through": (today - timedelta(days=1)).isoformat() if elapsed > 0 else None,
        "pace_gap": gap,                                             # positive = ahead of an even pace (for 'within': above it, which is worse)
        "per_day_needed": round(max(0.0, target - actual) / remaining, 2) if remaining and has_data else None,
        "summary": _sentence(goal, status, actual, target, projected, remaining, fmt),
        "history": history,
    }


def _sentence(goal: Goal, status: str, actual: float, target: float, projected: Optional[float], remaining: int, fmt) -> str:
    name = METRICS[goal.metric].lower()
    if status == "no_data":
        return "No records yet, so there is nothing to measure against this target."
    if status == "not_started":
        return f"A new {goal.cadence} has just begun. Progress shows once a full day is recorded."
    if status == "met":
        return f"Target reached: {fmt(actual)} of {fmt(target)}. Everything from here is above it."
    if direction(goal.metric) == "within":
        if status == "off_track" and actual > target:
            return f"Over the limit by {fmt(actual - target)}: {fmt(actual)} spent against {fmt(target)} with {remaining} day(s) left."
        return (f"{fmt(actual)} spent so far; at this pace the {goal.cadence} ends near {fmt(projected)} against a limit of {fmt(target)}."
                if status != "on_track" else f"On track to stay within {fmt(target)}: {fmt(actual)} so far, heading for about {fmt(projected)}.")
    need = max(0.0, target - actual) / remaining if remaining else 0.0
    if status == "on_track":
        return f"On track: {fmt(actual)} of {fmt(target)} so far, heading for about {fmt(projected)}."
    return (f"{fmt(actual)} of {fmt(target)} so far; at this pace {name} end near {fmt(projected)}. "
            f"Reaching it needs about {fmt(need)} a day for the {remaining} day(s) left.")


def all_progress(db: Session, tenant_id: str, now: Optional[datetime] = None) -> list[dict]:
    rows = db.query(Goal).filter(Goal.tenant_id == tenant_id).order_by(Goal.created_at).all()
    return [progress(db, g, now) for g in rows]


# ---------------- why ----------------

def _driver_rows(db: Session, tenant_id: str, entity: str, column, cur: tuple, prev: tuple) -> list[dict]:
    now_, was = analytics._by(db, tenant_id, entity, column, *cur), analytics._by(db, tenant_id, entity, column, *prev)
    return [{"name": n, "now": round(now_.get(n, {}).get("amount", 0.0), 2), "before": round(was.get(n, {}).get("amount", 0.0), 2),
             "change": round(now_.get(n, {}).get("amount", 0.0) - was.get(n, {}).get("amount", 0.0), 2)} for n in set(now_) | set(was)]


def drivers(db: Session, goal: Goal, now: Optional[datetime] = None) -> dict:
    """What moved this period against the same stretch of the one before, by branch, product and spending category.
    Deterministic and exact; the AI only words it (see explain)."""
    tid = goal.tenant_id
    tz = periods.tenant_zone(db, tid)
    today = periods.local_today(db, tid, now)
    first, _ = bounds(goal.cadence, today)
    elapsed = (today - first).days
    if elapsed <= 0:
        return {"compared": None, "sections": []}
    prev_last = first - timedelta(days=1)
    prev_first, _ = bounds(goal.cadence, prev_last)
    prev_days = min(elapsed, (prev_last - prev_first).days + 1)          # the same number of days from the start of the previous period
    cur = (periods.local_midnight_utc(first, tz), periods.local_midnight_utc(today, tz))
    prev = (periods.local_midnight_utc(prev_first, tz), periods.local_midnight_utc(prev_first + timedelta(days=prev_days), tz))
    specs = []
    if goal.metric in ("sales", "profit"):
        specs += [("Branches", "sale", E.location, +1), ("Products", "sale", E.product_id, +1)]
    if goal.metric in ("spending", "profit"):
        specs += [("Spending categories", "expense", E.product_id, -1)]
    sections = []
    for title, entity, col, sign in specs:
        rows = _driver_rows(db, tid, entity, col, cur, prev)
        for r in rows:
            r["effect"] = round(r["change"] * sign, 2)               # positive = helped the target, negative = hurt it
        rows = [r for r in rows if r["change"] != 0]
        hurt = sorted([r for r in rows if r["effect"] < 0], key=lambda r: r["effect"])[:3]
        helped = sorted([r for r in rows if r["effect"] > 0], key=lambda r: -r["effect"])[:3]
        if hurt or helped:
            sections.append({"title": title, "hurt": hurt, "helped": helped})
    return {"compared": {"days": prev_days, "this": [first.isoformat(), (today - timedelta(days=1)).isoformat()],
                         "before": [prev_first.isoformat(), (prev_first + timedelta(days=prev_days - 1)).isoformat()]},
            "sections": sections}

EXPLAIN_PROMPT = """You explain to a business owner why they are ahead of, on, or behind a target they set.
You receive figures ALREADY computed by code: progress against the target, and what moved (by branch, product, spending
category) against the same stretch of the previous period.
STRICT RULES:
- Use ONLY the numbers given. Never invent, estimate or round to other figures.
- Say what the figures show; where they only suggest a cause, say "may be linked to". Never claim certainty about why.
- 2-4 plain sentences, then ONE concrete next step the owner can take. Do not decide for them.
- Output ONLY valid JSON: {"explanation": "...", "next_step": "..."}  No markdown, no preamble."""


def explain(db: Session, goal: Goal, now: Optional[datetime] = None) -> dict:
    """Deterministic drivers always; an AI wording of them when a provider is reachable (never required, never blocking)."""
    import json
    prog, drv = progress(db, goal, now), drivers(db, goal, now)
    out = {"progress": prog, "drivers": drv, "ai": None, "ai_error": None}
    if prog["status"] in ("no_data", "not_started"):
        return out
    from app.core.advisory import language_name
    from app.models.core import Tenant
    from app.reasoning.llm_providers import get_provider
    lang = (db.get(Tenant, goal.tenant_id).language or "en")
    try:
        text = get_provider(db, goal.tenant_id).generate_json(
            system_prompt=EXPLAIN_PROMPT + (f"\n- Write in {language_name(lang)}; keep the JSON keys in English." if lang != "en" else ""),
            user_content=json.dumps({"target": {k: prog[k] for k in ("name", "metric_label", "cadence", "direction", "target", "actual", "status", "projected", "days_elapsed", "days_remaining", "pace_gap")},
                                     "what_moved": drv}, default=str), max_tokens=500)
        parsed = json.loads(text)
        out["ai"] = {"explanation": str(parsed["explanation"]), "next_step": str(parsed["next_step"])}
    except Exception as e:                                           # no provider, a rate limit, malformed JSON: the figures above still stand
        import logging
        logging.getLogger("business_reality_engine").warning(f"Goal explanation by AI unavailable for tenant={goal.tenant_id}: {type(e).__name__}")
        out["ai_error"] = "The AI explanation is not available right now. The figures above are exact."
    return out
