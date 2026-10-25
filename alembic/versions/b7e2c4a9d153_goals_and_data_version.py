"""goals (targets) and a per-business data version for live refresh

Revision ID: b7e2c4a9d153
Revises: a3c5e7f1b942
Create Date: 2026-10-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'b7e2c4a9d153'
down_revision = 'a3c5e7f1b942'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenants', sa.Column('data_version', sa.Integer(), server_default='0', nullable=False))
    op.create_table(
        'goals',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('metric', sa.String(), nullable=False),
        sa.Column('cadence', sa.String(), nullable=False),
        sa.Column('target_amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_goal_name'),
    )
    op.create_index('ix_goals_tenant', 'goals', ['tenant_id'])


def downgrade():
    op.drop_index('ix_goals_tenant', table_name='goals')
    op.drop_table('goals')
    op.drop_column('tenants', 'data_version')
