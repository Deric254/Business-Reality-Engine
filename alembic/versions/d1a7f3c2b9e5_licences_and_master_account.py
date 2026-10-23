"""licences, payments ledger, master account flag

Existing businesses are given an active, free licence so that switching licence enforcement on never locks anyone out.

Revision ID: d1a7f3c2b9e5
Revises: c4d8a9e1f376
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d1a7f3c2b9e5"
down_revision = "c4d8a9e1f376"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_master", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_table(
        "licences",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("key_hint", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("interval_months", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_reason", sa.String(), nullable=True),
        sa.Column("paid_through", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_licences_key_hash", "licences", ["key_hash"], unique=True)
    op.create_index("ix_licences_tenant", "licences", ["tenant_id"], unique=True)
    op.create_index("ix_licences_status", "licences", ["status"])
    op.create_table(
        "licence_payments",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("licence_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("licences.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
    )
    op.create_index("ix_licence_payments_licence", "licence_payments", ["licence_id"])
    op.create_index("ix_licence_payments_reference", "licence_payments", ["reference"], unique=True, postgresql_where=sa.text("reference IS NOT NULL"))
    # Every business that exists today keeps working: an active, free ("existing account") licence each.
    op.execute("""
        INSERT INTO licences (id, key_hash, key_hint, label, status, price_cents, currency, interval_months, created_at, redeemed_at, tenant_id)
        SELECT gen_random_uuid()::text::uuid, md5(t.id::text || 'existing') || md5('existing' || t.id::text), 'existing', 'Existing account',
               'active', 0, 'USD', 1, now(), now(), t.id FROM tenants t
    """)


def downgrade():
    op.drop_table("licence_payments")
    op.drop_table("licences")
    op.drop_column("users", "is_master")
