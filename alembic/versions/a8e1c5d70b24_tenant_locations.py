"""tenant_locations: one spelling per place, kept in a small table

Revision ID: a8e1c5d70b24
Revises: f7d4a2b9c613
Create Date: 2026-10-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a8e1c5d70b24'
down_revision = 'f7d4a2b9c613'
branch_labels = None
depends_on = None

_SQUASH = "regexp_replace(btrim(location), '\\s+', ' ', 'g')"


def upgrade():
    op.create_table(
        'tenant_locations',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('key', sa.String(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
    )
    # Existing businesses keep the spelling they use most for each place.
    op.execute(f"""
        INSERT INTO tenant_locations (tenant_id, key, name)
        SELECT tenant_id, lower(spelling), spelling FROM (
            SELECT tenant_id, spelling, row_number() OVER (PARTITION BY tenant_id, lower(spelling) ORDER BY n DESC, spelling) AS rn FROM (
                SELECT tenant_id, {_SQUASH} AS spelling, count(*) AS n FROM business_events
                WHERE location IS NOT NULL AND btrim(location) <> '' GROUP BY tenant_id, {_SQUASH}) s) ranked
        WHERE rn = 1""")


def downgrade():
    op.drop_table('tenant_locations')
