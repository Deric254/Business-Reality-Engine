"""
Alert Engine (Sections 21-26) and Business Memory (Sections 36-41).
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Numeric, Text, ForeignKey, JSON, Enum as SAEnum, Index, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import enum

from app.models.core import Base, gen_uuid


class AlertSeverity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    informational = "informational"
    opportunity = "opportunity"


class AlertState(str, enum.Enum):
    detected = "detected"
    investigating = "investigating"
    acknowledged = "acknowledged"
    action_planned = "action_planned"
    action_taken = "action_taken"
    monitoring = "monitoring"
    resolved = "resolved"
    dismissed = "dismissed"
    false_positive = "false_positive"


class Alert(Base):
    """Section 22: full alert structure."""
    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)

    severity = Column(SAEnum(AlertSeverity), nullable=False)
    state = Column(SAEnum(AlertState), nullable=False, default=AlertState.detected)
    title = Column(String, nullable=False)

    what_happened = Column(Text, nullable=False)
    magnitude = Column(Numeric(10, 4), nullable=True)     # e.g. -0.154 for -15.4%
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    where = Column(JSON, nullable=True)                    # affected locations/products/etc
    who_what_affected = Column(JSON, nullable=True)

    evidence = Column(JSON, nullable=False, default=dict)  # structured evidence, Section 29 shape
    related_variables = Column(JSON, nullable=True)
    possible_explanations = Column(JSON, nullable=True)    # Section 27 driver/evidence pairs

    confidence = Column(Numeric(3, 2), nullable=True)      # 0-1
    data_coverage_pct = Column(Numeric(5, 2), nullable=True)
    recommended_action = Column(Text, nullable=True)
    priority_rank = Column(Numeric(5, 2), nullable=True)   # for sorting within severity

    correlated_alert_ids = Column(JSON, nullable=True)     # Section 26

    # Section 28-30: AI reasoning layer output. Kept separate from the
    # deterministic `what_happened`/`possible_explanations` above — those
    # are always computed from evidence and never touched by the LLM call;
    # this is the natural-language narrative layered on top.
    ai_summary = Column(Text, nullable=True)
    ai_recommended_action = Column(Text, nullable=True)
    ai_evidence_hash = Column(String, nullable=True)  # cache key — skip re-calling LLM if evidence unchanged
    ai_generated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Dedup / cooldown lookups by evidence['dedup_key'] must not scan every alert of a tenant.
        Index("ix_alerts_dedup", "tenant_id", text("(evidence->>'dedup_key')")),
        Index("ix_alerts_tenant_state", "tenant_id", "state"),
    )


class Case(Base):
    """
    Business Memory (Sections 36, 41).
    Structured record: problem -> evidence -> recommendation -> decision -> outcome.
    """
    __tablename__ = "cases"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    alert_id = Column(UUID(as_uuid=False), ForeignKey("alerts.id"), nullable=True)

    problem = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    context = Column(JSON, nullable=True)

    recommendation = Column(Text, nullable=True)
    decision = Column(String, nullable=True)   # e.g. "doing_this" | "already_handled" | "not_doing_this" — Section 37
    intervention = Column(Text, nullable=True)

    expected_outcome = Column(Text, nullable=True)
    measurement_period_days = Column(Numeric(6, 2), nullable=True)
    actual_outcome = Column(Text, nullable=True)
    attribution_confidence = Column(SAEnum(
        "high", "medium", "low", "uncertain", name="attribution_confidence_enum"
    ), nullable=True)

    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    outcome_measured_at = Column(DateTime, nullable=True)
