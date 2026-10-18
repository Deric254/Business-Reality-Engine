"""events: 'matched' status for records paired by a confirmed merge

Revision ID: f7d4a2b9c613
Revises: e6c3f91a2b58
Create Date: 2026-10-18 09:00:00.000000

"""
from alembic import op


revision = 'f7d4a2b9c613'
down_revision = 'e6c3f91a2b58'
branch_labels = None
depends_on = None


def upgrade():
    # The first version of the merge recorded its pairs as 'duplicate' with a marker in metadata_json.
    op.execute("UPDATE business_events SET validation_status = 'matched', metadata_json = (metadata_json::jsonb - 'merged_by')::json "
               "WHERE validation_status = 'duplicate' AND metadata_json::jsonb ->> 'merged_by' = 'rule'")


def downgrade():
    op.execute("UPDATE business_events SET validation_status = 'duplicate', metadata_json = (metadata_json::jsonb || '{\"merged_by\": \"rule\"}'::jsonb)::json "
               "WHERE validation_status = 'matched'")
