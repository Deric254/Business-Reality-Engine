"""Per-tenant configuration (Section 50) and alert delivery (Section 49)."""
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Numeric, Integer, Boolean, ForeignKey, Index, Text, UniqueConstraint,
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
    status = Column(String, nullable=False, default="pending")    # pending | sent | dead
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
