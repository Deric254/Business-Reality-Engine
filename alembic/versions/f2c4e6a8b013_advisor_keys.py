"""advisor keys: the keys a CRM was paired with, kept only as hashes

Revision ID: f2c4e6a8b013
Revises: e7a9c2d4f618
Create Date: 2026-10-31 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'f2c4e6a8b013'
down_revision = 'e7a9c2d4f618'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'advisor_keys',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_advisor_keys_hash', 'advisor_keys', ['key_hash'], unique=True)


def downgrade():
    op.drop_index('ix_advisor_keys_hash', table_name='advisor_keys')
    op.drop_table('advisor_keys')
