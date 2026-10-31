"""advisor alert sharing: which CRM lead a licence came from, and each business's own opt-in switch

Revision ID: e7a9c2d4f618
Revises: c4d8f2a6b731
Create Date: 2026-10-31 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e7a9c2d4f618'
down_revision = 'c4d8f2a6b731'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('licences', sa.Column('source_ref', sa.String(length=64), nullable=True))
    op.add_column('tenants', sa.Column('share_alerts_with_advisor', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('tenants', 'share_alerts_with_advisor')
    op.drop_column('licences', 'source_ref')
