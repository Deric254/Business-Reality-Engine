"""events: source_hash (fingerprint of the record as its source gave it)

Revision ID: b9f2d6e8c471
Revises: a8e1c5d70b24
Create Date: 2026-10-19 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b9f2d6e8c471'
down_revision = 'a8e1c5d70b24'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('business_events', sa.Column('source_hash', sa.String(length=40), nullable=True))


def downgrade():
    op.drop_column('business_events', 'source_hash')
