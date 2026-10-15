"""Per-tenant configuration (Section 50) and alert delivery (Section 49)."""
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Date, Numeric, Integer, Boolean, ForeignKey, Index, Text, UniqueConstraint, JSON,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.core import Base, gen_uuid


class MonitoredMetric(Base):
    """What the scheduler watches for a tenant. A tenant with NO rows gets the
    defaults (sale.amount, expense.amount); any row at all replaces them."""
    __tablename__ = "monitored_metrics"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    entity = Column(String, nullable=False)                       # sale | expense | ...
    field = Column(String, nullable=False)                        # amount | quantity
    period_days = Column(Integer, nullable=False, default=7)
    baseline_periods = Column(Integer, nullable=False, default=4)
    threshold_pct = Column(Numeric(5, 2), nullable=False, default=10)   # % move (in the bad direction) that raises an alert
    # Which way is BAD: "drop" for revenue-like metrics, "rise" for cost-like ones.
    # A move the other way past the threshold is reported as an opportunity.
    direction = Column(String, nullable=False, default="drop", server_default="drop")
    # Optional fixed line on the period total (drop: alert when BELOW it; rise: when ABOVE it).
    absolute_threshold = Column(Numeric(18, 2), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "entity", "field", "period_days", name="uq_monitored_metric"),
        Index("ix_monitored_metrics_tenant", "tenant_id"),
    )


class NotificationChannel(Base):
    """Where a tenant's alerts are delivered. email: target is an address.
    webhook: target is an https URL; deliveries are HMAC-signed with a secret
    that is generated server-side, shown once, and stored encrypted."""
    __tablename__ = "notification_channels"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    channel_type = Column(String, nullable=False)                 # email | webhook
    target = Column(String, nullable=False)
    label = Column(String, nullable=True)
    secret_encrypted = Column(Text, nullable=True)
    min_severity = Column(String, nullable=False, default="high")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Delivery preferences (all times are UTC "HH:MM"; a window may cross midnight).
    quiet_start = Column(String(5), nullable=True)
    quiet_end = Column(String(5), nullable=True)
    digest_mode = Column(String, nullable=False, default="off", server_default="off")   # off | daily
    digest_hour = Column(Integer, nullable=False, default=7, server_default="7")        # UTC hour 0-23
    last_digest_at = Column(DateTime, nullable=True)
    last_digest_attempt_at = Column(DateTime, nullable=True)
    filter_kinds = Column(JSON(none_as_null=True), nullable=True)      # list of detector kinds; null = all
    filter_entities = Column(JSON(none_as_null=True), nullable=True)   # list of entity ids; null = all

    __table_args__ = (Index("ix_notification_channels_tenant", "tenant_id"),)


class NotificationDelivery(Base):
    """Outbox: one row per (alert, channel). The unique pair makes enqueueing
    idempotent; status/attempts/next_attempt_at drive retries with backoff."""
    __tablename__ = "notification_deliveries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    alert_id = Column(UUID(as_uuid=False), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False, default="alert", server_default="alert")   # alert | escalation
    channel_id = Column(UUID(as_uuid=False), ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False, default="pending")    # pending | held (awaiting digest) | sent | dead
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("alert_id", "channel_id", "kind", name="uq_delivery_alert_channel_kind"),
        Index("ix_deliveries_due", "status", "next_attempt_at"),
        Index("ix_deliveries_tenant_created", "tenant_id", "created_at"),
    )


class FxRate(Base):
    """Section 57: units of base currency per 1 unit of `currency`, effective from `effective_date`.
    An event converts with the latest rate on or before its own date."""
    __tablename__ = "fx_rates"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    currency = Column(String(3), nullable=False)
    rate_to_base = Column(Numeric(18, 8), nullable=False)
    effective_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("tenant_id", "currency", "effective_date", name="uq_fx_rate_day"),)


class ExternalContext(Base):
    """Section 42: external facts (holidays, weather, promotions...) kept SEPARATE from business
    data. They explain or temper alerts; they are never mixed into business totals."""
    __tablename__ = "external_context"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    kind = Column(String, nullable=False)              # holiday | promotion | weather | event | other
    name = Column(String, nullable=False)
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    source = Column(String, nullable=False, default="manual")   # manual | calendar
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_external_context_range", "tenant_id", "starts_on", "ends_on"),
        UniqueConstraint("tenant_id", "kind", "name", "starts_on", name="uq_external_context_item"),
    )


class ModelChange(Base):
    """Section 53: a change in the shape of the business (new/closed branch, product line, replaced system).
    `version` increases per tenant; alerts and cases carry the version in force when they were raised.
    `rebaseline` makes period-over-period detectors wait until enough post-change history exists."""
    __tablename__ = "model_changes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    version = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)          # location_added | location_discontinued | product_added | product_discontinued | source_added | source_replaced | restructure
    subject = Column(String, nullable=False)       # what changed (name/id); with kind, makes auto-detection idempotent
    description = Column(Text, nullable=True)
    effective_at = Column(DateTime, nullable=False)
    rebaseline = Column(Boolean, nullable=False, default=False, server_default="false")
    source = Column(String, nullable=False, default="auto")     # auto | manual
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "subject", name="uq_model_change_subject"),
        UniqueConstraint("tenant_id", "version", name="uq_model_change_version"),
    )


class GroundTruthEvent(Base):
    """Section 55: something that really happened (an outage, a stock-out, a theft, a promotion that worked)
    that a good detector should have noticed. Used to measure recall/precision of backtests and live alerts."""
    __tablename__ = "ground_truth_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    label = Column(String, nullable=False)
    metric = Column(String, nullable=True)         # optional entity (e.g. "sale") the event should show up in
    starts_on = Column(Date, nullable=False)
    ends_on = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_ground_truth_range", "tenant_id", "starts_on"),)


class BacktestRun(Base):
    """Section 55: a stored replay result, so go-live validation can be reviewed later."""
    __tablename__ = "backtest_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    params = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_backtest_runs_tenant", "tenant_id", "created_at"),)


class AIProvider(Base):
    """An AI service a business has connected (key stored encrypted). Providers are tried in priority order;
    if one fails (rate limit, outage) the next is used. A tenant with none falls back to the server's env settings."""
    __tablename__ = "ai_providers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    label = Column(String, nullable=False)                        # "Gemini", "Groq", ...
    kind = Column(String, nullable=False)                         # anthropic | openai | openai_compatible
    base_url = Column(String, nullable=True)
    model = Column(String, nullable=False)
    api_key = Column(Text, nullable=False)                        # Fernet-encrypted
    priority = Column(Integer, nullable=False, default=100)       # lower = tried first
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
