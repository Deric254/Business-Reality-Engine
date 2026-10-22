"""
Question router (Section 45): maps a plain-language question onto the analytics the system already has.

Deterministic first: keyword rules choose an intent, SQL/forecasting code computes every number,
and the answer is template-worded. No LLM is needed to answer, and none ever computes a figure
(Section 46). A question the rules cannot place gets an honest "I can't answer that yet" plus what
can be asked, never a guess. The profit intent keeps its optional LLM phrasing (business_questions).
"""
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import coverage as coverage_mod
from app.core.alert_engine import ACTIVE
from app.core.detection import _by_dimension, _sum_metric, dimension_movers
from app.core.forecasting import forecast_days, forecast_next_period
from app.models.alerts import Alert
from app.models.core import BusinessEvent, ResolvedEntity

_UNITS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}
SUGGESTIONS = [
    "Why did profit fall?", "How are sales this month?", "What are my expenses this week?",
    "Which branch is doing worst?", "Which day of the week is my best?", "Who are my top customers?", "What are my best selling products?", "What will sales be next week?",
    "What needs my attention?", "Is my data up to date?",
]


def parse_period(question: str, default_days: int = 30) -> tuple[int, str]:
    """'last 14 days', 'this week', 'last month', 'today', 'yesterday', 'past 2 weeks' -> (days, label)."""
    q = question.lower()
    m = re.search(r"(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month|quarter|year)s?", q)
    if m:
        days = min(365, int(m.group(1)) * _UNITS[m.group(2)])
        return max(1, days), f"the last {m.group(1)} {m.group(2)}{'s' if m.group(1) != '1' else ''}"
    if re.search(r"\b(today|yesterday)\b", q):
        return 1, "the last day"
    for unit in ("week", "month", "quarter", "year"):
        if re.search(rf"\b(this|last|past|previous|per|each|a)\s+{unit}\b|\b{unit}ly\b", q):
            return _UNITS[unit], f"the last {unit}"
    return default_days, f"the last {default_days} days"


_RULES: list[tuple[str, re.Pattern]] = [
    ("forecast", re.compile(r"\b(will|forecast|predict|projection|expect|next (week|month|period)|going to be)\b")),
    ("top_customers", re.compile(r"\b(top|best|biggest|largest|main|key)\b.*\bcustomers?\b|\bcustomers?\b.*\b(top|best|biggest)\b|\bwho (buys|are my)\b.*\bcustomers?\b")),
    ("top_products", re.compile(r"\b(top|best|biggest|selling|popular)\b.*\b(products?|items?)\b|\b(products?|items?)\b.*\b(top|best|selling)\b|\bbest sellers?\b")),
    ("top_suppliers", re.compile(r"\b(top|biggest|largest|main)\b.*\bsuppliers?\b|\bsuppliers?\b.*\b(most|top|biggest)\b|\bwho do i (pay|spend)\b")),
    ("weekdays", re.compile(r"\b(day of (the )?week|weekdays?|busiest day|slowest day|quietest day|best day|worst day|which days?)\b")),
    ("branches", re.compile(r"\b(branch(es)?|locations?|shops?|outlets?|stores?)\b")),
    ("profit", re.compile(r"\b(profit|margin|earnings|net income|bottom line)\b")),
    ("sales", re.compile(r"\b(sales?|revenue|income|turnover|selling|sold|takings)\b")),
    ("expenses", re.compile(r"\b(expenses?|costs?|spend(ing)?|spent|outgoings?)\b")),
    ("attention", re.compile(r"\b(attention|alerts?|problems?|issues?|wrong|worr(y|ied)|urgent|risks?|trouble|should i (do|look))\b")),
    ("data_health", re.compile(r"\b(data|coverage|connected|connectors?|up to date|stale|sync(ed)?|missing)\b")),
]


def classify(question: str) -> Optional[str]:
    q = question.lower()
    for intent, pat in _RULES:
        if pat.search(q):
            return intent
    return None


def _money(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def _change(cur: float, prev: float) -> tuple[Optional[float], str]:
    if not prev:
        return None, "with no earlier period to compare against"
    pct = (cur - prev) / abs(prev) * 100
    return round(pct, 1), f"{'up' if pct >= 0 else 'down'} {abs(pct):.1f}% from {_money(prev)} in the period before"


def _movers(db, tenant_id, entity, days, now, rising):
    """The branches and products behind a change in the total, as a sentence plus the structured detail."""
    cur, prev = (now - timedelta(days=days), now), (now - timedelta(days=days * 2), now - timedelta(days=days))
    found = {label: m for column, label in (("location", "branch"), ("product_id", "category" if entity == "expense" else "product"))
             if (m := dimension_movers(db, tenant_id, entity, column, cur, prev, rising))}
    text = "".join(f" {label.capitalize()} {m['value']} {'up' if rising else 'down'} {abs(m['change_pct'])}% ({_money(m['before'])} to {_money(m['after'])})."
                   for label, ms in found.items() for m in ms)
    return (" Where it moved:" + text if text else ""), found


def _metric_answer(db, tenant_id, entity, noun, days, label, now):
    cur = _sum_metric(db, tenant_id, entity, "amount", now - timedelta(days=days), now)
    prev = _sum_metric(db, tenant_id, entity, "amount", now - timedelta(days=days * 2), now - timedelta(days=days))
    if not cur and not prev:
        return {"answer": f"There is no {noun} data in {label} or the period before it.", "evidence": {"current": 0, "previous": 0}}
    pct, phrase = _change(cur, prev)
    where, found = _movers(db, tenant_id, entity, days, now, rising=cur > prev) if cur != prev else ("", {})
    return {"answer": f"{noun.capitalize()} over {label} were {_money(cur)}, {phrase}.{where}",
            "evidence": {"period_days": days, "current": cur, "previous": prev, "change_pct": pct, "movers": found}}


def _weekdays(db, tenant_id, q, days, label, now):
    from app.core import query_plan                      # imported here: query_plan itself builds on this module
    measure = "expenses" if re.search(r"\b(expenses?|costs?|spend(ing)?|spent)\b", q) else "revenue"
    out = query_plan.run_last_days(db, tenant_id, measure, "weekday", days, now)
    return {"answer": out["answer"], "evidence": out["evidence"]}


def _branches(db, tenant_id, q, days, label, now):
    cur = _by_dimension(db, tenant_id, "sale", "location", now - timedelta(days=days), now)
    prev = _by_dimension(db, tenant_id, "sale", "location", now - timedelta(days=days * 2), now - timedelta(days=days))
    if len(cur) < 2:
        seen = f"only one branch ({next(iter(cur))})" if cur else "no branch"
        return {"answer": f"Your sales records name {seen} in {label}, so there is nothing to compare. "
                          "Include a branch or location column when you connect a source.", "evidence": {"branches": []}}
    rows = sorted(({"name": n, "amount": a, "previous": prev.get(n, 0.0),
                    "change_pct": round((a - prev[n]) / prev[n] * 100, 1) if prev.get(n) else None} for n, a in cur.items()),
                  key=lambda r: -r["amount"])
    show = lambda r: _money(r["amount"]) + (f", {r['change_pct']:+.0f}% compared with the {days} days before" if r["change_pct"] is not None else "")
    answer = f"Over {label}, {rows[0]['name']} sold the most ({show(rows[0])}) and {rows[-1]['name']} the least ({show(rows[-1])})."
    falling = min((r for r in rows if r["change_pct"] is not None), key=lambda r: r["change_pct"], default=None)
    if falling and falling["change_pct"] <= -10:
        answer += f" Biggest fall: {falling['name']}, down {abs(falling['change_pct']):.0f}% from {_money(falling['previous'])} to {_money(falling['amount'])}."
    return {"answer": answer, "evidence": {"period_days": days, "branches": rows}}


def _ranked(db, tenant_id, entity, fk_col, days, now, limit=5):
    start = now - timedelta(days=days)
    rows = (
        db.query(ResolvedEntity.canonical_name, func.sum(BusinessEvent.amount), func.count(BusinessEvent.id))
        .join(ResolvedEntity, ResolvedEntity.id == fk_col)
        .filter(BusinessEvent.tenant_id == tenant_id, ResolvedEntity.tenant_id == tenant_id,
                BusinessEvent.entity == entity, BusinessEvent.timestamp >= start, BusinessEvent.timestamp < now,
                BusinessEvent.validation_status == "validated", BusinessEvent.amount.isnot(None))
        .group_by(ResolvedEntity.id, ResolvedEntity.canonical_name)
        .order_by(func.sum(BusinessEvent.amount).desc()).limit(limit).all()
    )
    total = _sum_metric(db, tenant_id, entity, "amount", start, now)
    return [{"name": n, "amount": float(a), "events": int(c), "share_pct": round(float(a) / total * 100, 1) if total else None}
            for n, a, c in rows], total


def _ranking_answer(kind, entity, fk, verb):
    def handler(db, tenant_id, q, days, label, now):
        items, total = _ranked(db, tenant_id, entity, getattr(BusinessEvent, fk), days, now)
        if not items:
            return {"answer": f"No {kind} with recorded {verb} in {label}. Records need to be linked to a named {kind[:-1]} first.",
                    "evidence": {"items": [], "total": total}}
        lines = ", ".join(f"{i['name']} ({_money(i['amount'])}" + (f", {i['share_pct']}%" if i['share_pct'] is not None else "") + ")" for i in items)
        return {"answer": f"Top {kind} by {verb} over {label}: {lines}.", "evidence": {"period_days": days, "items": items, "total": total}}
    return handler


def _forecast(db, tenant_id, q, days, label, now):
    entity, noun = ("expense", "expenses") if re.search(r"\b(expenses?|costs?|spend)", q) else ("sale", "sales")
    period = 30 if re.search(r"month", q) else 7
    f = forecast_days(db, tenant_id, entity, period, now)
    if f:
        trend = "flat" if abs(f.trend_pct) < 2 else f"{'up' if f.trend_pct > 0 else 'down'} {abs(f.trend_pct):.0f}%"
        return {"answer": (f"If your weekly pattern holds, {noun} over the next {period} days will be about {_money(f.total)} "
                           f"(likely between {_money(f.low)} and {_money(f.high)}). Strongest day: {f.busiest}; quietest: {f.quietest}. "
                           f"Based on the last {f.weeks_of_history} weeks, with the underlying trend {trend} over that time. "
                           "Holidays and paydays are not modelled, so treat this as a guide, not a promise."),
                "evidence": {"period_days": period, "method": "weekly pattern + trend", "weeks_of_history": f.weeks_of_history,
                             "forecast_total": f.total, "low": f.low, "high": f.high, "trend_pct": f.trend_pct,
                             "days": [{"date": d.day.isoformat(), "weekday": d.weekday, "expected": d.expected, "low": d.low, "high": d.high} for d in f.days]}}
    t = forecast_next_period(db, tenant_id, entity, "amount", period, 6, now)
    vals = t.historical_values
    if sum(1 for v in vals if v > 0) < 3:
        return {"answer": f"There is not enough {noun} history yet (needs at least 3 of the last 6 {period}-day periods) to project the next one.",
                "evidence": {"values": vals}}
    unit = "month" if period == 30 else "week"
    return {"answer": (f"If the recent trend continues, {noun} next {unit} will be about {_money(t.forecast_next)} "
                       f"(trend {t.direction}; last {unit} was {_money(vals[-1])}). This is a straight-line projection of the last 6 {unit}s, not a promise."),
            "evidence": {"period_days": period, "values": vals, "forecast_next": t.forecast_next, "slope": t.slope, "direction": t.direction}}


def _attention(db, tenant_id, q, days, label, now):
    alerts = (db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE))
              .order_by(Alert.priority_rank.desc().nullslast(), Alert.created_at.desc()).limit(5).all())
    if not alerts:
        return {"answer": "Nothing needs attention right now: there are no open alerts.", "evidence": {"alerts": []}}
    total = db.query(func.count(Alert.id)).filter(Alert.tenant_id == tenant_id, Alert.state.in_(ACTIVE)).scalar()
    items = [{"id": a.id, "severity": a.severity.value, "title": a.title, "escalated": bool((a.evidence or {}).get("escalated_at"))} for a in alerts]
    lines = "; ".join(f"{i['title']} ({i['severity']}{', not yet reviewed' if i['escalated'] else ''})" for i in items)
    return {"answer": f"{total} open alert{'s' if total != 1 else ''}. Most important first: {lines}.", "evidence": {"open": total, "alerts": items}}


def _data_health(db, tenant_id, q, days, label, now):
    rows = coverage_mod.compute_and_upsert_coverage(db, tenant_id, now)
    if not rows:
        return {"answer": "No data sources are connected yet.", "evidence": {"areas": []}}
    items = [{"area": r.area, "status": getattr(r.status, "value", str(r.status)),
              "last_data_at": r.last_data_at.isoformat() if r.last_data_at else None} for r in rows]
    bad = [i for i in items if i["status"] not in ("live", "recent")]
    if not bad:
        return {"answer": f"All {len(items)} data area{'s are' if len(items) != 1 else ' is'} up to date.", "evidence": {"areas": items}}
    return {"answer": f"{len(bad)} of {len(items)} data areas are not up to date: "
                      + ", ".join(f"{i['area']} ({i['status'].replace('_', ' ')})" for i in bad)
                      + ". Your figures may be missing recent records until they send data again.",
            "evidence": {"areas": items}}


HANDLERS: dict[str, Callable] = {
    "sales": lambda db, t, q, d, l, n: _metric_answer(db, t, "sale", "sales", d, l, n),
    "expenses": lambda db, t, q, d, l, n: _metric_answer(db, t, "expense", "expenses", d, l, n),
    "top_customers": _ranking_answer("customers", "sale", "resolved_customer_id", "sales"),
    "top_products": _ranking_answer("products", "sale", "resolved_product_id", "sales"),
    "top_suppliers": _ranking_answer("suppliers", "expense", "resolved_supplier_id", "spend"),
    "weekdays": _weekdays, "branches": _branches, "forecast": _forecast, "attention": _attention, "data_health": _data_health,
}


INTENTS = [name for name, _ in _RULES]


def classify_with_llm(question: str, provider) -> Optional[str]:
    """Section 45 fallback when no keyword rule matches. The model only PICKS one of our fixed intents; it
    never produces numbers or answer text. Anything that is not exactly one allowed intent is ignored."""
    system = ("Classify a business question into exactly one intent from this list: " + ", ".join(INTENTS) +
              ". Reply with only the intent name, or the single word none if it fits none of them. "
              "Questions may be in English or Swahili. Do not answer the question.")
    try:
        raw = provider.generate_json(system_prompt=system, user_content=question[:500], max_tokens=10)
    except Exception:
        return None
    word = (raw or "").strip().strip(".\"'` ").lower()
    return word if word in INTENTS else None


def route(db: Session, tenant_id: str, question: str, default_days: int = 30, now: Optional[datetime] = None,
          intent: Optional[str] = None) -> Optional[dict]:
    """Returns {intent, answer, evidence, period_days} for every intent except profit (handled by the
    caller, which owns the optional LLM phrasing), or None when no rule matches."""
    now = now or datetime.utcnow()
    intent = intent or classify(question)
    if intent is None or (intent != "profit" and intent not in HANDLERS):
        return None
    days, label = parse_period(question, default_days)
    if intent == "profit":
        return {"intent": "profit", "period_days": days}
    out = HANDLERS[intent](db, tenant_id, question.lower(), days, label, now)
    return {"intent": intent, "period_days": days, **out}
