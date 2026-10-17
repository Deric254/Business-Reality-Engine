"""connectors: merge_into (counted once with another source)

Revision ID: e6c3f91a2b58
Revises: d5b2e80c14a7
Create Date: 2026-10-17 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e6c3f91a2b58'
down_revision = 'd5b2e80c14a7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('connectors', sa.Column('merge_into', postgresql.UUID(as_uuid=False), nullable=True))
    op.create_foreign_key('fk_connectors_merge_into', 'connectors', 'connectors', ['merge_into'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_connectors_merge_into', 'connectors', type_='foreignkey')
    op.drop_column('connectors', 'merge_into')
