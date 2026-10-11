"""add per-role resolved entity FKs on business_events

Revision ID: 314b901ffccb
Revises: d6998e1727f4
Create Date: 2026-09-12 20:24:53.145004

"""
from alembic import op
import sqlalchemy as sa


revision = '314b901ffccb'
down_revision = 'd6998e1727f4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('business_events', sa.Column('resolved_customer_id', sa.UUID(as_uuid=False), nullable=True))
    op.add_column('business_events', sa.Column('resolved_product_id', sa.UUID(as_uuid=False), nullable=True))
    op.add_column('business_events', sa.Column('resolved_employee_id', sa.UUID(as_uuid=False), nullable=True))
    op.add_column('business_events', sa.Column('resolved_supplier_id', sa.UUID(as_uuid=False), nullable=True))
    op.drop_constraint('business_events_resolved_entity_id_fkey', 'business_events', type_='foreignkey')
    op.create_foreign_key('business_events_resolved_employee_id_fkey', 'business_events', 'resolved_entities', ['resolved_employee_id'], ['id'])
    op.create_foreign_key('business_events_resolved_product_id_fkey', 'business_events', 'resolved_entities', ['resolved_product_id'], ['id'])
    op.create_foreign_key('business_events_resolved_customer_id_fkey', 'business_events', 'resolved_entities', ['resolved_customer_id'], ['id'])
    op.create_foreign_key('business_events_resolved_supplier_id_fkey', 'business_events', 'resolved_entities', ['resolved_supplier_id'], ['id'])
    op.drop_column('business_events', 'resolved_entity_id')


def downgrade():
    op.add_column('business_events', sa.Column('resolved_entity_id', sa.UUID(), autoincrement=False, nullable=True))
    op.drop_constraint('business_events_resolved_employee_id_fkey', 'business_events', type_='foreignkey')
    op.drop_constraint('business_events_resolved_product_id_fkey', 'business_events', type_='foreignkey')
    op.drop_constraint('business_events_resolved_customer_id_fkey', 'business_events', type_='foreignkey')
    op.drop_constraint('business_events_resolved_supplier_id_fkey', 'business_events', type_='foreignkey')
    op.create_foreign_key('business_events_resolved_entity_id_fkey', 'business_events', 'resolved_entities', ['resolved_entity_id'], ['id'])
    op.drop_column('business_events', 'resolved_supplier_id')
    op.drop_column('business_events', 'resolved_employee_id')
    op.drop_column('business_events', 'resolved_product_id')
    op.drop_column('business_events', 'resolved_customer_id')
