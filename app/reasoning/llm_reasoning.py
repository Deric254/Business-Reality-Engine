"""
AI Reasoning Layer (Sections 28-30, 54).

Section 28's principle: raw data never goes straight to the LLM. By the
time evidence reaches here, it's already been through deterministic
processing — aggregation (detection.py), explanation decomposition
(explain_deviation). The LLM's job is narrower than "figure out what's
wrong": turn already-computed evidence into a natural-language narrative
and a recommendation. It does not get raw event rows, and it is
instructed not to invent numbers beyond what's given.

Provider-agnostic: this module calls app.reasoning.llm_providers.get_provider(),
which returns whichever backend LLM_PROVIDER selects (Anthropic, OpenAI, or
any OpenAI-compatible endpoint). Nothing here knows or cares which one.

Cost control (Section 54):
- Only called for medium+ severity — informational/opportunity alerts
  don't justify an LLM call.
- Cached via a hash of the evidence dict. If evidence hasn't changed
  since the last call, skip the API call entirely and return the cached
  narrative — this is what actually prevents re-spending every time
  detection re-runs on an unchanged alert.
"""
import hashlib
import json
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.models.alerts import Alert, AlertSeverity
from app.reasoning.llm_providers import get_provider
from app.core.clock import utcnow

logger = logging.getLogger("business_reality_engine")

MIN_SEVERITY_FOR_REASONING = {AlertSeverity.medium, AlertSeverity.high, AlertSeverity.critical}

SYSTEM_PROMPT = """You are the reasoning layer of a business intelligence system.
You receive structured evidence that has ALREADY been computed by deterministic
code — aggregations, percentage changes, and rule-based explanations. Your job:

1. Write a short, plain-language narrative (2-3 sentences) of what happened,
   suitable for a busy business manager.
2. Suggest ONE concrete, advisory recommended action (1-2 sentences).

STRICT RULES:
- Use ONLY the numbers given in the evidence. Never invent, estimate, or
  round to different figures than what's provided.
- Do not claim certainty about causation the evidence doesn't support —
  if evidence shows correlation, say "may be linked to", not "caused by".
- Do not make the decision for the business — recommend investigation or
  a next step, not an irreversible action.
- Output ONLY valid JSON: {"narrative": "...", "recommended_action": "..."}
  No markdown, no preamble."""


def _evidence_hash(evidence: dict, related_variables: Optional[dict], possible_explanations: Optional[list]) -> str:
    payload = json.dumps(
        {"evidence": evidence, "related_variables": related_variables, "possible_explanations": possible_explanations},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def reason_about_alert(db: Session, alert: Alert, force: bool = False) -> dict:
    """
    Generates (or returns cached) AI narrative for an alert.
    Returns {"skipped": bool, "reason": str, ...} or the narrative fields.
    """
    if alert.severity not in MIN_SEVERITY_FOR_REASONING:
        return {"skipped": True, "reason": f"severity '{alert.severity.value}' below reasoning threshold"}

    from app.core.advisory import language_name
    from app.models.core import Tenant
    tenant = db.get(Tenant, alert.tenant_id)
    lang = (tenant.language if tenant and tenant.language else "en")
    current_hash = _evidence_hash(alert.evidence, alert.related_variables, alert.possible_explanations) + f":{lang}"

    if not force and alert.ai_evidence_hash == current_hash and alert.ai_summary:
        return {
            "skipped": True, "reason": "evidence unchanged since last reasoning call — using cache",
            "narrative": alert.ai_summary, "recommended_action": alert.ai_recommended_action,
        }

    evidence_payload = {
        "title": alert.title,
        "what_happened": alert.what_happened,
        "evidence": alert.evidence,
        "related_variables": alert.related_variables,
        "possible_explanations": alert.possible_explanations,
        "confidence": float(alert.confidence) if alert.confidence is not None else None,
    }

    provider = get_provider(db, alert.tenant_id)  # raises RuntimeError with a clear message if misconfigured — caller maps to 502
    try:
        raw_text = provider.generate_json(
            system_prompt=SYSTEM_PROMPT + (f"\n- Write the narrative and recommended_action in {language_name(lang)}; keep the JSON keys in English." if lang != "en" else ""),
            user_content=json.dumps(evidence_payload, default=str),
            max_tokens=400,
        )
    except Exception as e:
        # Never leak SDK/network internals to the client (could include host
        # config, partial keys, etc. depending on the provider's error body).
        # Full detail goes to the log only — same principle as the DB error
        # handlers in app/main.py.
        logger.error(f"LLM provider '{provider.name}' call failed: {type(e).__name__}: {e}")
        raise RuntimeError(f"LLM provider '{provider.name}' call failed ({type(e).__name__})")

    try:
        parsed = json.loads(raw_text)
        narrative = parsed["narrative"]
        recommended_action = parsed["recommended_action"]
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(
            f"LLM provider '{provider.name}' did not return the expected JSON shape: {e}. Raw: {raw_text[:200]}"
        )

    alert.ai_summary = narrative
    alert.ai_recommended_action = recommended_action
    alert.ai_evidence_hash = current_hash
    alert.ai_generated_at = utcnow()
    db.commit()
    db.refresh(alert)

    return {"skipped": False, "narrative": narrative, "recommended_action": recommended_action, "provider": provider.name}
