"""
Business Questions Interface (Section 45).

Section 46's principle applied directly: the LLM does not compute
anything here. All numbers come from SQL aggregation (compute_profit_breakdown).
The LLM's only job is turning already-correct numbers into the kind of
answer Section 45 shows: "Why did profit fall?" -> contributors ranked,
largest one named, evidence attached.

If no LLM provider is configured, this still returns a complete, useful
answer — a deterministic template, not an error. A business question
should never fail just because reasoning-layer config is missing; only
the AI Reasoning Layer alerts (Section 28-30) are severity-gated/optional
in that sense. A direct question deserves a direct answer either way.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.orm import Session

from app.core.detection import _sum_metric
from app.reasoning.llm_providers import get_provider

logger = logging.getLogger("business_reality_engine")

SYSTEM_PROMPT = """You answer business questions using ONLY the structured evidence given to
you — a profit/revenue/expense breakdown that has already been computed correctly
by deterministic code. Do not recompute or second-guess the numbers.

Write a short, direct answer (2-4 sentences) in the style of:
"Profit fell 13%. Main causes: sales down 6%, supplier costs up 11%.
Biggest cause: the supplier cost increase."

Use ONLY the numbers provided. Never invent figures. Output plain text, no markdown."""


def compute_profit_breakdown(
    db: Session, tenant_id: str, period_days: int = 30, now: Optional[datetime] = None
) -> dict:
    """
    Section 45's example (revenue/supplier costs/labour costs contributing
    to a profit change) generalized: current vs. previous period on
    revenue-like ('sale') and cost-like ('expense') entities, with
    contributors ranked by absolute dollar impact on profit.
    """
    now = now or datetime.utcnow()
    current_start = now - timedelta(days=period_days)
    previous_start = now - timedelta(days=period_days * 2)
    previous_end = current_start

    revenue_current = _sum_metric(db, tenant_id, "sale", "amount", current_start, now)
    revenue_previous = _sum_metric(db, tenant_id, "sale", "amount", previous_start, previous_end)
    expense_current = _sum_metric(db, tenant_id, "expense", "amount", current_start, now)
    expense_previous = _sum_metric(db, tenant_id, "expense", "amount", previous_start, previous_end)

    profit_current = revenue_current - expense_current
    profit_previous = revenue_previous - expense_previous
    profit_change = profit_current - profit_previous
    profit_change_pct = (profit_change / abs(profit_previous)) if profit_previous else None

    revenue_delta = revenue_current - revenue_previous
    expense_delta = expense_current - expense_previous  # a rise in expenses HURTS profit

    contributors = []
    if revenue_previous or revenue_current:
        contributors.append({
            "name": "Sales", "delta": round(revenue_delta, 2),
            "delta_pct": round((revenue_delta / revenue_previous) * 100, 1) if revenue_previous else None,
            "profit_impact": round(revenue_delta, 2),  # revenue up -> profit up, same sign
        })
    if expense_previous or expense_current:
        contributors.append({
            "name": "Spending", "delta": round(expense_delta, 2),
            "delta_pct": round((expense_delta / expense_previous) * 100, 1) if expense_previous else None,
            "profit_impact": round(-expense_delta, 2),  # expenses up -> profit DOWN, opposite sign
        })
    contributors.sort(key=lambda c: -abs(c["profit_impact"]))

    return {
        "period_days": period_days,
        "revenue": {"current": revenue_current, "previous": revenue_previous},
        "expenses": {"current": expense_current, "previous": expense_previous},
        "profit": {
            "current": round(profit_current, 2), "previous": round(profit_previous, 2),
            "change": round(profit_change, 2),
            "change_pct": round(profit_change_pct * 100, 1) if profit_change_pct is not None else None,
        },
        "contributors": contributors,
        "largest_contributor": contributors[0]["name"] if contributors else None,
    }


def _deterministic_fallback(evidence: dict) -> str:
    """No LLM configured: still a complete, correct answer, in plain words."""
    from app.core.question_router import _money
    profit, days = evidence["profit"], evidence["period_days"]
    text = f"Your profit (sales minus spending) over the last {days} days is {_money(profit['current'])}"
    if profit["change_pct"] is not None:
        text += f", {'up' if profit['change'] >= 0 else 'down'} {abs(profit['change_pct']):.0f}% compared with the {days} days before"
    text += "."
    moves = [f"{c['name'].lower()} {'up' if c['delta'] >= 0 else 'down'} {abs(c['delta_pct']):.0f}%"
             for c in evidence["contributors"] if c["delta_pct"] is not None]
    if moves:
        text += " Over the same time: " + " and ".join(moves) + "."
    if evidence["largest_contributor"]:
        text += f" The biggest effect on your profit came from {evidence['largest_contributor'].lower()}."
    return text


PLANNABLE = {"sales", "expenses", "branches", "weekdays", "top_customers", "top_products", "top_suppliers"}


def answer_question(db: Session, tenant_id: str, question: str, period_days: int = 30) -> dict:
    from app.core.detection import currency_of
    from app.core.question_router import currency_scope
    with currency_scope(currency_of(db, tenant_id)):
        return _answer_question(db, tenant_id, question, period_days)


def _answer_question(db: Session, tenant_id: str, question: str, period_days: int) -> dict:
    """Section 45: route the question to the analytics that can answer it. Profit questions keep the
    optional LLM phrasing; everything else is deterministic. Unplaceable questions are said so."""
    from app.core import query_plan
    from app.core.question_router import route, SUGGESTIONS
    from app.core.advisory import advisory_text, language_name
    from app.core.question_router import classify, classify_with_llm
    from app.models.core import Tenant
    tenant = db.get(Tenant, tenant_id)
    lang = (tenant.language if tenant and tenant.language else "en")
    routed = route(db, tenant_id, question, period_days)
    try:
        provider = get_provider(db, tenant_id)
    except Exception:
        provider = None
    if provider and (routed is None or (routed["intent"] in PLANNABLE and query_plan.needs_plan(db, tenant_id, question))):
        # With a model connected, a question the fixed intents cannot express (a filter, a grouping, a comparison) is
        # PLANNED by the model and then run as SQL; the model never sees or writes numbers.
        planned = query_plan.answer_with_plan(db, tenant_id, question, provider)
        if planned:
            return {"question": question, "answered": True, "intent": planned["intent"], "answer": planned["answer"],
                    "evidence": planned["evidence"], "answer_source": "deterministic", "classified_by": "llm_plan",
                    "advisory": advisory_text(lang)}
    if routed is None and classify(question) is None:
        # No keyword matched: let a configured model pick an intent (classification only; numbers stay in SQL).
        try:
            guessed = classify_with_llm(question, get_provider(db, tenant_id))
        except Exception:
            guessed = None
        if guessed:
            routed = route(db, tenant_id, question, period_days, intent=guessed)
            if routed:
                routed["classified_by"] = "llm"
    if routed is None:
        return {"question": question, "answered": False, "intent": None, "answer_source": "none", "evidence": {},
                "answer": "I can't answer that yet. Without the AI assistant I can answer questions about profit, sales, expenses, top customers, "
                          "products or suppliers, branches, the best weekday, next-period forecasts, what needs attention, and data freshness."
                          + ("" if provider else " The AI assistant is not connected: connect it in Settings (or set ANTHROPIC_API_KEY on the server) "
                             "and you can ask about your sales, expenses and profit in your own words."),
                "ai_connected": provider is not None, "suggestions": SUGGESTIONS, "advisory": advisory_text(lang)}
    if routed["intent"] != "profit":
        return {"question": question, "answered": True, "intent": routed["intent"], "answer": routed["answer"],
                "evidence": routed["evidence"], "answer_source": "deterministic",
                "classified_by": routed.get("classified_by", "rules"), "advisory": advisory_text(lang)}

    days = routed["period_days"]
    evidence = compute_profit_breakdown(db, tenant_id, period_days=days)
    try:
        provider = get_provider(db, tenant_id)
        answer_text = provider.generate_json(
            system_prompt=SYSTEM_PROMPT + (f"\nWrite the answer in {language_name(lang)}." if lang != "en" else ""),
            user_content=f"Question: {question}\n\nEvidence: {evidence}",
            max_tokens=200,
        )
        source = "llm"
    except Exception as e:
        # A business question always gets an answer: fall back to the deterministic template.
        logger.info(f"Business question answered via deterministic fallback (no/failed LLM provider): {e}")
        answer_text = _deterministic_fallback(evidence)
        source = "deterministic_fallback"

    return {"question": question, "answered": True, "intent": "profit", "answer": answer_text.strip(),
            "evidence": evidence, "answer_source": source,
            "classified_by": routed.get("classified_by", "rules"), "advisory": advisory_text(lang)}
