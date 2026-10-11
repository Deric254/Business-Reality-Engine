"""index business_events resolved_* FK columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

_COLS = [("customer", "resolved_customer_id"), ("product", "resolved_product_id"),
         ("employee", "resolved_employee_id"), ("supplier", "resolved_supplier_id")]


def upgrade():
    # NOTE: on a very large live table, build these CONCURRENTLY outside a transaction instead.
    for name, col in _COLS:
        op.create_index(f"ix_events_resolved_{name}", "business_events", [col],
                        postgresql_where=sa.text(f"{col} IS NOT NULL"))


def downgrade():
    for name, col in _COLS:
        op.drop_index(f"ix_events_resolved_{name}", table_name="business_events",
                      postgresql_where=sa.text(f"{col} IS NOT NULL"))
