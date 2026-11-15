"""the agent can describe an unknown database: what it found and what the owner chose is kept per system connector

Revision ID: c4e6a8b0d235
Revises: b3d5f7a9c124
Create Date: 2026-11-15 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4e6a8b0d235'
down_revision = 'b3d5f7a9c124'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_status", sa.Column("discovery", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("agent_status", "discovery")
