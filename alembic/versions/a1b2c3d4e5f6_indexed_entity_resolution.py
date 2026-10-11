"""indexed entity resolution: denormalized alias columns, pg_trgm, unique alias identity

Revision ID: a1b2c3d4e5f6
Revises: 7f403e0225e7
"""
import re
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '7f403e0225e7'
branch_labels = None
depends_on = None


def _normalize(value):
    # Must stay identical to app.core.entity_resolution._normalize.
    return re.sub(r"[^\w\s]", "", value or "").strip().lower()


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column('entity_aliases', sa.Column('tenant_id', sa.UUID(as_uuid=False), nullable=True))
    op.add_column('entity_aliases', sa.Column('entity_type', sa.String(), nullable=True))
    op.add_column('entity_aliases', sa.Column('normalized_value', sa.String(), nullable=True))
    op.add_column('entity_aliases', sa.Column('last_token', sa.String(), nullable=True))
    op.execute("""
        UPDATE entity_aliases a SET tenant_id = e.tenant_id, entity_type = e.entity_type
        FROM resolved_entities e WHERE e.id = a.entity_id
    """)
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, raw_value FROM entity_aliases")).fetchall()
    for i in range(0, len(rows), 1000):
        conn.execute(
            sa.text("UPDATE entity_aliases SET normalized_value = :n, last_token = :l WHERE id = :id"),
            [{"id": r[0], "n": _normalize(r[1]), "l": (_normalize(r[1]).split() or [""])[-1]} for r in rows[i:i + 1000]],
        )
    op.alter_column('entity_aliases', 'tenant_id', nullable=False)
    op.alter_column('entity_aliases', 'entity_type', nullable=False)
    op.alter_column('entity_aliases', 'normalized_value', nullable=False)
    op.alter_column('entity_aliases', 'last_token', nullable=False)
    # Fails loudly if pre-existing data already contains duplicate aliases
    # (possible only from historical races) — dedupe those by hand first.
    op.create_index('ux_entity_aliases_identity', 'entity_aliases', ['tenant_id', 'entity_type', 'source', 'raw_value'], unique=True)
    op.create_index('ix_entity_aliases_norm', 'entity_aliases', ['tenant_id', 'entity_type', 'normalized_value'])
    op.create_index('ix_entity_aliases_last_token', 'entity_aliases', ['tenant_id', 'entity_type', 'last_token'], postgresql_ops={'last_token': 'text_pattern_ops'})
    op.create_index('ix_entity_aliases_entity', 'entity_aliases', ['entity_id'])
    op.create_index('ix_entity_aliases_trgm_knn', 'entity_aliases', ['tenant_id', 'entity_type', 'normalized_value'], postgresql_using='gist', postgresql_ops={'normalized_value': 'gist_trgm_ops'})


def downgrade():
    for ix in ('ix_entity_aliases_trgm_knn', 'ix_entity_aliases_entity', 'ix_entity_aliases_last_token',
               'ix_entity_aliases_norm', 'ux_entity_aliases_identity'):
        op.drop_index(ix, table_name='entity_aliases')
    for col in ('last_token', 'normalized_value', 'entity_type', 'tenant_id'):
        op.drop_column('entity_aliases', col)
