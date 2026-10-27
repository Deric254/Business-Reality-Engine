"""What the local agent last told the engine about one system connector (smePro, Imara, Pharmacy ERP)."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.models.core import Base


class AgentStatus(Base):
    __tablename__ = "agent_status"

    connector_id = Column(UUID(as_uuid=False), ForeignKey("connectors.id", ondelete="CASCADE"), primary_key=True)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    state = Column(String, nullable=False, default="waiting")      # what the agent last reported (connected, not_found, ...)
    detail = Column(Text, nullable=True)
    notice = Column(Text, nullable=True)                           # e.g. the system was updated since the connection was verified
    last_seen_at = Column(DateTime, nullable=True)                 # last heartbeat or sync from the agent
    last_synced_at = Column(DateTime, nullable=True)               # last pass whose totals matched
    problem_since = Column(DateTime, nullable=True)                # when the current non-connected state began
    status = Column(JSON, nullable=False, default=dict)            # where the database is, businesses found, size, version
    last_pass = Column(JSON, nullable=False, default=dict)
    agent = Column(JSON, nullable=False, default=dict)
    verify = Column(JSON, nullable=False, default=dict)            # the engine's own count/sum check against the agent's totals
    check_requested_at = Column(DateTime, nullable=True)
    check_done_at = Column(DateTime, nullable=True)
    commands = Column(JSON, nullable=False, default=dict)          # owner's choices: path, scope_id, paused, resync
    confirm_until = Column(DateTime, nullable=True)                # owner confirmed a large removal until then
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_agent_status_tenant", "tenant_id"),)
