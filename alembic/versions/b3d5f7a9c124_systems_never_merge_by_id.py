"""connected systems count every record: rows once merged because another system used the same id are counted again

Revision ID: b3d5f7a9c124
Revises: a1c3e5b7d902
Create Date: 2026-11-04 08:00:00.000000

A connected system (smePro, Imara, Pharmacy ERP) numbers its own rows, so "sale 7" in one is a different sale from "sale 7"
in another. Rows of a system that were set aside as a duplicate of such a lookalike are counted again, and the open screens
of the affected businesses are told to reload. Nothing is deleted.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3d5f7a9c124'
down_revision = 'a1c3e5b7d902'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    tenants = [r[0] for r in conn.execute(sa.text("""
        UPDATE business_events SET validation_status = 'validated', duplicate_of = NULL
        WHERE validation_status = 'duplicate' AND connector_id IN (SELECT id FROM connectors WHERE connector_type = 'system')
        RETURNING tenant_id"""))]
    if tenants:
        conn.execute(sa.text("UPDATE tenants SET data_version = data_version + 1 WHERE id = ANY(:ids)"), {"ids": sorted(set(tenants))})


def downgrade():
    pass        # which rows were once set aside is not recorded, and counting them is the intended behaviour
