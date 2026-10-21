"""connectors: last_sync_error (why the last sync failed)

Revision ID: c4d8a9e1f376
Revises: b9f2d6e8c471
Create Date: 2026-10-20 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d8a9e1f376'
down_revision = 'b9f2d6e8c471'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('connectors', sa.Column('last_sync_error', sa.String(), nullable=True))


def downgrade():
    op.drop_column('connectors', 'last_sync_error')
