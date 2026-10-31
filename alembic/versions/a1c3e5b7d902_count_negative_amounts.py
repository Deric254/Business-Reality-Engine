"""count negative sale/expense amounts: records that were left out only for being negative are validated again

Revision ID: a1c3e5b7d902
Revises: f2c4e6a8b013
Create Date: 2026-10-31 18:00:00.000000

A negative amount (a refund, a credit, a correction) is real data and is now counted. Records stored earlier with that
as their only validation error are put back into the totals; a record with another error as well keeps that error and
stays out. Nothing is deleted or edited besides the validation fields.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c3e5b7d902'
down_revision = 'f2c4e6a8b013'
branch_labels = None
depends_on = None

_MSG = "negative amount on sale/expense — check source mapping"


def upgrade():
    op.get_bind().execute(sa.text("""
        UPDATE business_events SET
            validation_status = CASE WHEN (validation_errors::jsonb - CAST(:msg AS text)) = '[]'::jsonb THEN 'validated' ELSE 'failed' END,
            validation_errors = CASE WHEN (validation_errors::jsonb - CAST(:msg AS text)) = '[]'::jsonb THEN NULL
                                     ELSE (validation_errors::jsonb - CAST(:msg AS text))::json END
        WHERE validation_status = 'failed' AND validation_errors::jsonb @> jsonb_build_array(CAST(:msg AS text))
    """), {"msg": _MSG})


def downgrade():
    pass        # which records were once left out for this reason is not recorded, and counting them is the intended behaviour
