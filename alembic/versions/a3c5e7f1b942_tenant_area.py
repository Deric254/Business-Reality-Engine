"""tenant area: where the business trades, used to ground web searches

Revision ID: a3c5e7f1b942
Revises: d1a7f3c2b9e5
Create Date: 2026-10-25 06:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a3c5e7f1b942'
down_revision = 'd1a7f3c2b9e5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('area', sa.String(), nullable=True))


def downgrade():
    op.drop_column('tenants', 'area')
