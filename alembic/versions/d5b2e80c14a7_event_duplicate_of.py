"""events: duplicate_of (same record counted from another source)

Revision ID: d5b2e80c14a7
Revises: c3a7d51e9f20
Create Date: 2026-10-16 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd5b2e80c14a7'
down_revision = 'c3a7d51e9f20'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('business_events', sa.Column('duplicate_of', postgresql.UUID(as_uuid=False), nullable=True))
    op.create_foreign_key('fk_events_duplicate_of', 'business_events', 'business_events', ['duplicate_of'], ['id'], ondelete='SET NULL')
    op.create_index('ix_events_tenant_entity_id', 'business_events', ['tenant_id', 'entity_id'], postgresql_where=sa.text('entity_id IS NOT NULL'))
    op.create_index('ix_events_duplicate_of', 'business_events', ['duplicate_of'], postgresql_where=sa.text('duplicate_of IS NOT NULL'))


def downgrade():
    op.drop_index('ix_events_duplicate_of', table_name='business_events')
