"""agent_status: what each local-system agent last reported

Revision ID: c4d8f2a6b731
Revises: b7e2c4a9d153
Create Date: 2026-10-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'c4d8f2a6b731'
down_revision = 'b7e2c4a9d153'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_status',
        sa.Column('connector_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('notice', sa.Text(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('problem_since', sa.DateTime(), nullable=True),
        sa.Column('status', sa.JSON(), nullable=False),
        sa.Column('last_pass', sa.JSON(), nullable=False),
        sa.Column('agent', sa.JSON(), nullable=False),
        sa.Column('verify', sa.JSON(), nullable=False),
        sa.Column('check_requested_at', sa.DateTime(), nullable=True),
        sa.Column('check_done_at', sa.DateTime(), nullable=True),
        sa.Column('commands', sa.JSON(), nullable=False),
        sa.Column('confirm_until', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('connector_id'),
    )
    op.create_index('ix_agent_status_tenant', 'agent_status', ['tenant_id'])


def downgrade():
    op.drop_index('ix_agent_status_tenant', table_name='agent_status')
    op.drop_table('agent_status')
