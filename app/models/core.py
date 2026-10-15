"""
DericBI Reality Engine — Core schema
Section 10: Universal Data Contract
Section 13: Entity Resolution
Section 15: Data Coverage Map
Section 48: Multi-tenant isolation (tenant_id on every row)
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Numeric, ForeignKey, Boolean,
    Index, JSON, Enum as SAEnum, BigInteger, Integer
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import MetaData, text
import enum

# Explicit naming convention — without this, Postgres/Alembic auto-generates
# constraint names that autogenerate can't reliably reference later (we hit
# exactly this problem in migration 314b901ffccb). Fixing it here so every
# migration from now on gets a predictable, named constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)
Base = declarative_base(metadata=metadata)


def gen_uuid():
    return str(uuid.uuid4())


class Tenant(Base):
    """One row per business using the engine. Everything else is scoped to this."""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    timezone = Column(String, nullable=False, default="UTC")  # Section 57
    base_currency = Column(String, nullable=False, default="KES")  # Section 57
    country = Column(String, nullable=True)                         # ISO 3166 alpha-2; drives the holiday calendar (Section 42)
    language = Column(String, nullable=False, default="en", server_default="en")  # Section 57: UI and narrative language
    created_at = Column(DateTime, default=datetime.utcnow)


class Connector(Base):
    """A configured data source for a tenant. Section 7-8."""
    __tablename__ = "connectors"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)               # e.g. "Main POS"
    connector_type = Column(String, nullable=False)      # api | webhook | database | file | sftp | email | local_agent
    config = Column(JSON, nullable=False, default=dict)  # connection details (never secrets in plaintext — see core/secrets.py later)
    expected_frequency = Column(String, nullable=True)   # realtime | near_realtime | periodic | slow — Section 17
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String, nullable=True)     # success | failed | partial
    sync_interval_minutes = Column(Integer, nullable=True)   # Section 17: pull-type connectors sync on this schedule (NULL = manual only)
    last_attempt_at = Column(DateTime, nullable=True)         # last scheduled/manual sync attempt, successful or not
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_connectors_tenant", "tenant_id"),)


class BusinessEvent(Base):
    """
    The Universal Data Contract (Section 10).
    Every connector normalizes its source data into rows of this shape.
    This is the single internal language the rest of the engine reasons over.
    """
    __tablename__ = "business_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    connector_id = Column(UUID(as_uuid=False), ForeignKey("connectors.id"), nullable=False)

    source = Column(String, nullable=False)          # e.g. "POS", "Accounting"
    entity = Column(String, nullable=False)           # e.g. "sale", "expense", "customer"
    entity_id = Column(String, nullable=True)         # source-native id, e.g. "SALE-92882"

    event_type = Column(String, nullable=False)       # e.g. "sale.created", "expense.recorded"
    timestamp = Column(DateTime, nullable=False)       # when it happened in the business, not when ingested

    amount = Column(Numeric(18, 2), nullable=True)
    quantity = Column(Numeric(18, 4), nullable=True)
    currency = Column(String, nullable=True)

    location = Column(String, nullable=True)

    # Raw source-native identifiers, as sent by the connector.
    customer_id = Column(String, nullable=True)
    product_id = Column(String, nullable=True)
    employee_id = Column(String, nullable=True)
    supplier_id = Column(String, nullable=True)

    # Resolved canonical identity for each role (Section 13). One event can
    # reference a customer AND a product AND a supplier simultaneously, so
    # this needs one FK per role — a single resolved_entity_id column can't
    # represent that. Populated by the ingestion pipeline, not by connectors.
    resolved_customer_id = Column(UUID(as_uuid=False), ForeignKey("resolved_entities.id"), nullable=True)
    resolved_product_id = Column(UUID(as_uuid=False), ForeignKey("resolved_entities.id"), nullable=True)
    resolved_employee_id = Column(UUID(as_uuid=False), ForeignKey("resolved_entities.id"), nullable=True)
    resolved_supplier_id = Column(UUID(as_uuid=False), ForeignKey("resolved_entities.id"), nullable=True)

    metadata_json = Column(JSON, nullable=False, default=dict)

    # Section 11: provenance / ingestion tracking
    imported_at = Column(DateTime, default=datetime.utcnow)
    validation_status = Column(String, default="pending")  # pending | validated | failed
    validation_errors = Column(JSON, nullable=True)

    __table_args__ = (
        # FK-referencing columns need indexes: deleting/merging a resolved entity
        # otherwise seq-scans the whole events table to check references
        # (found under load: a 500k-row table made each DELETE take >1 minute).
        Index("ix_events_resolved_customer", "resolved_customer_id", postgresql_where=text("resolved_customer_id IS NOT NULL")),
        Index("ix_events_resolved_product", "resolved_product_id", postgresql_where=text("resolved_product_id IS NOT NULL")),
        Index("ix_events_resolved_employee", "resolved_employee_id", postgresql_where=text("resolved_employee_id IS NOT NULL")),
        Index("ix_events_resolved_supplier", "resolved_supplier_id", postgresql_where=text("resolved_supplier_id IS NOT NULL")),
        Index("ix_events_tenant_ts", "tenant_id", "timestamp"),
        Index("ix_events_tenant_entity", "tenant_id", "entity"),
        Index("ix_events_source_entity_id", "source", "entity_id"),
        # Idempotent ingestion (Section 11): the same source record, re-sent
        # by the same connector, must never create a second row. Partial
        # (WHERE entity_id IS NOT NULL) because a source system with no
        # stable per-record ID has nothing to de-duplicate on — that's a
        # source-data limitation, not something this constraint can fix.
        Index(
            "ux_events_connector_entity_entity_id",
            "connector_id", "entity", "entity_id",
            unique=True,
            postgresql_where=entity_id.isnot(None),
        ),
    )


class ResolvedEntity(Base):
    """
    Section 13: Entity Resolution.
    Canonical identity that multiple source-system records collapse into
    (e.g. "John Mwangi" / "J. Mwangi" / "JM001" -> one customer).
    """
    __tablename__ = "resolved_entities"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    entity_type = Column(String, nullable=False)   # customer | product | supplier | employee | branch | asset
    canonical_name = Column(String, nullable=False)
    attributes = Column(JSON, nullable=False, default=dict)
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)  # 0-1, Section 13
    created_at = Column(DateTime, default=datetime.utcnow)

    aliases = relationship("EntityAlias", back_populates="entity")

    __table_args__ = (Index("ix_resolved_entities_tenant_type", "tenant_id", "entity_type"),)


class EntityAlias(Base):
    """Each raw source-string mapped to a ResolvedEntity, with per-alias confidence.

    tenant_id / entity_type are denormalized from the parent entity, and
    normalized_value / last_token are precomputed, so entity resolution is an
    INDEXED lookup (exact normalized match, surname match, pg_trgm similarity)
    instead of loading every entity in the tenant into Python."""
    __tablename__ = "entity_aliases"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    entity_id = Column(UUID(as_uuid=False), ForeignKey("resolved_entities.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=False), nullable=False)
    entity_type = Column(String, nullable=False)
    source = Column(String, nullable=False)
    raw_value = Column(String, nullable=False)
    normalized_value = Column(String, nullable=False)
    last_token = Column(String, nullable=False, default="")
    confidence = Column(Numeric(3, 2), nullable=False, default=1.0)

    entity = relationship("ResolvedEntity", back_populates="aliases")

    __table_args__ = (
        Index("ix_entity_aliases_raw", "source", "raw_value"),
        # One alias per (tenant, type, source, raw value): makes concurrent
        # creation of the same new entity impossible instead of silently
        # producing duplicates.
        Index("ux_entity_aliases_identity", "tenant_id", "entity_type", "source", "raw_value", unique=True),
        Index("ix_entity_aliases_norm", "tenant_id", "entity_type", "normalized_value"),
        Index("ix_entity_aliases_last_token", "tenant_id", "entity_type", "last_token", postgresql_ops={"last_token": "text_pattern_ops"}),
        Index("ix_entity_aliases_entity", "entity_id"),
        # Tenant-scoped nearest-neighbour trigram search (KNN) — bounded work per lookup.
        Index("ix_entity_aliases_trgm_knn", "tenant_id", "entity_type", "normalized_value", postgresql_using="gist", postgresql_ops={"normalized_value": "gist_trgm_ops"}),
    )


class DataCoverageStatus(str, enum.Enum):
    live = "live"
    recent = "recent"       # within expected freshness window
    stale = "stale"         # overdue but was connected
    not_connected = "not_connected"


class DataCoverage(Base):
    """
    Section 15: Data Coverage Map.
    One row per (tenant, business area) — computed/refreshed by a background job,
    not stored per-event. Powers the coverage widget and confidence downgrades.
    """
    __tablename__ = "data_coverage"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    area = Column(String, nullable=False)  # e.g. "sales", "inventory", "payroll"
    status = Column(SAEnum(DataCoverageStatus), nullable=False)
    last_data_at = Column(DateTime, nullable=True)
    checked_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_coverage_tenant_area", "tenant_id", "area", unique=True),)


class User(Base):
    """
    Section 48 per-tenant user auth. One user belongs to one tenant for
    now — multi-tenant membership (a consultant working across several
    businesses) isn't modeled yet; that's a real limitation, not an
    oversight, and would need a join table rather than this FK when it's
    needed.

    role: "owner" | "staff". Section 48's own example — "different users
    at the same business seeing different data (e.g. payroll vs general
    staff)" — is realized narrowly here: owner-only for creating
    connectors (where credentials are entered), any authenticated user
    of the tenant for everything else this auth layer currently guards.
    That's the honest current scope, not a claim of full RBAC coverage
    across the app — see SECURITY.md.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    email = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="staff")  # "owner" | "staff"
    created_at = Column(DateTime, default=datetime.utcnow)
    # Every token issued at or before this instant (epoch ms) is invalid: sign-out-everywhere / password change.
    tokens_valid_after_ms = Column(BigInteger, nullable=False, default=0, server_default="0")
    # True while the password is one an owner generated: the account can do nothing but change it.
    must_change_password = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    __table_args__ = (Index("ix_users_email", "email", unique=True),)


class RevokedToken(Base):
    """Used/revoked token ids (refresh tokens are single-use). Rows expire with the token."""
    __tablename__ = "revoked_tokens"

    jti = Column(String, primary_key=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (Index("ix_revoked_tokens_expires", "expires_at"), Index("ix_revoked_tokens_user", "user_id"))


class AuditLog(Base):
    """Append-only security/administration trail (Section 48)."""
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    ts = Column(DateTime, nullable=False, default=datetime.utcnow)
    tenant_id = Column(UUID(as_uuid=False), nullable=True)      # no FK: the trail outlives and survives anything
    actor_user_id = Column(UUID(as_uuid=False), nullable=True)
    actor_type = Column(String, nullable=False, default="user")  # user | admin | service | anonymous
    action = Column(String, nullable=False)
    target = Column(String, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    detail = Column(JSON, nullable=False, default=dict)
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    __table_args__ = (Index("ix_audit_log_tenant_ts", "tenant_id", "ts"), Index("ix_audit_log_action_ts", "action", "ts"))


class SchedulerRun(Base):
    """
    Section 51: system self-monitoring. One row per detection cycle —
    the engine watches the business; this is what watches the engine.
    Not per-tenant — one row summarizes the whole cycle, since that's
    the unit a person checking "is the scheduler actually running"
    cares about.
    """
    __tablename__ = "scheduler_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    tenants_checked = Column(String, nullable=True)
    checks_run = Column(String, nullable=True)
    errors_count = Column(String, nullable=True)
    status = Column(String, nullable=False, default="running")  # running | completed | failed
    error_detail = Column(JSON, nullable=True)  # list of {tenant_id, metric, error} — internal ops only, never user-facing

    __table_args__ = (Index("ix_scheduler_runs_started_at", "started_at"),)


class PasswordResetToken(Base):
    """Single-use, expiring token for forgot-password and email invitations. Only the SHA-256 of the
    token is stored, so a database read never yields a usable link."""
    __tablename__ = "password_reset_tokens"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)
    purpose = Column(String, nullable=False)          # reset | invite
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
