"""user security questions

Revision ID: c3a7d51e9f20
Revises: b4df3b1116b2
Create Date: 2026-10-15 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3a7d51e9f20'
down_revision = 'b4df3b1116b2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('security_questions', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('users', 'security_questions')
