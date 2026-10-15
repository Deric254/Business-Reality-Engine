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
"Profit fell 13%. Main contributors: Revenue down 6%, Supplier costs up 11%.
Largest contributor: supplier cost increase."

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
            "name": "Revenue", "delta": round(revenue_delta, 2),
            "delta_pct": round((revenue_delta / revenue_previous) * 100, 1) if revenue_previous else None,
            "profit_impact": round(revenue_delta, 2),  # revenue up -> profit up, same sign
        })
    if expense_previous or expense_current:
        contributors.append({
            "name": "Expenses", "delta": round(expense_delta, 2),
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
    """No LLM configured — still a complete, correct answer, just template-worded."""
    profit = evidence["profit"]
    parts = [f"Profit is {profit['current']:.2f}"]
    if profit["change_pct"] is not None:
        direction = "up" if profit["change"] >= 0 else "down"
        parts[0] += f", {direction} {abs(profit['change_pct']):.1f}% vs. the prior period"
    parts[0] += "."
    if evidence["contributors"]:
        contrib_strs = [
            f"{c['name']} {'up' if c['delta'] >= 0 else 'down'} "
            f"{abs(c['delta_pct']):.1f}%" if c["delta_pct"] is not None else f"{c['name']} changed"
            for c in evidence["contributors"]
        ]
        parts.append("Contributors: " + ", ".join(contrib_strs) + ".")
        parts.append(f"Largest contributor: {evidence['largest_contributor']}.")
    return " ".join(parts)


def answer_question(db: Session, tenant_id: str, question: str, period_days: int = 30) -> dict:
    """Section 45: route the question to the analytics that can answer it. Profit questions keep the
    optional LLM phrasing; everything else is deterministic. Unplaceable questions are said so."""
    from app.core.question_router import route, SUGGESTIONS
    from app.core.advisory import advisory_text, language_name
    from app.core.question_router import classify, classify_with_llm
    from app.models.core import Tenant
    tenant = db.get(Tenant, tenant_id)
    lang = (tenant.language if tenant and tenant.language else "en")
    routed = route(db, tenant_id, question, period_days)
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
                "answer": "I can't answer that yet. I can answer questions about profit, sales, expenses, top customers, "
                          "products or suppliers, next-period forecasts, what needs attention, and data freshness.",
                "suggestions": SUGGESTIONS, "advisory": advisory_text(lang)}
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
