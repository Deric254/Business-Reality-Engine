"""Licences: the keys that let a business onto the platform, and the payments recorded against them."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.models.core import Base, gen_uuid


class Licence(Base):
    """One key = one business, once. Only a hash of the key is stored; the key itself is shown to the issuer a single time.
    status: unused (issued, not yet activated) | active | revoked."""
    __tablename__ = "licences"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    key_hash = Column(String(64), nullable=False)
    key_hint = Column(String, nullable=False)                    # first and last characters only, to recognise a key in a list
    label = Column(String, nullable=True)                        # who it was issued to, in the issuer's words
    status = Column(String, nullable=False, default="unused")
    price_cents = Column(Integer, nullable=False, default=100)   # what this licence is charged per interval
    currency = Column(String, nullable=False, default="USD")
    interval_months = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    redeemed_at = Column(DateTime, nullable=True)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    paid_through = Column(DateTime, nullable=True)               # the end of the last period a payment was recorded for

    __table_args__ = (Index("ix_licences_key_hash", "key_hash", unique=True),
                      Index("ix_licences_tenant", "tenant_id", unique=True),
                      Index("ix_licences_status", "status"))


class LicencePayment(Base):
    """The ledger: every amount received for a licence. Revenue figures are sums of these rows, nothing estimated."""
    __tablename__ = "licence_payments"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    licence_id = Column(UUID(as_uuid=False), ForeignKey("licences.id", ondelete="CASCADE"), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="USD")
    paid_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    reference = Column(String, nullable=True)                    # a payment provider's transaction id, when there is one
    method = Column(String, nullable=False, default="manual")

    __table_args__ = (Index("ix_licence_payments_licence", "licence_id"),
                      Index("ix_licence_payments_reference", "reference", unique=True, postgresql_where=reference.isnot(None)))
