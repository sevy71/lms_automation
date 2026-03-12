"""Add Organiser model: organisers table + organiser_id FK on players/rounds

Revision ID: add_organiser_model_001
Revises: add_telegram_id_001
Create Date: 2026-03-12 00:00:00.000000

Migration behaviour
-------------------
1. Creates the new `organisers` table.
2. Inserts the single bootstrap row: slug='default', name='Default Organiser'.
3. Adds nullable `organiser_id` FK column to `players` and `rounds`.
4. Backfills every existing player and round to the default organiser.
5. Makes `organiser_id` NOT NULL on both tables (safe after backfill).
6. Adds FK constraints (PostgreSQL) and indexes.

Rollback (downgrade) removes the columns and drops the table.
No existing columns are dropped or renamed — fully backward-compatible.

Phase 2 TODO
------------
- Add organiser_id to `picks` and `fixtures` tables.
- Add per-organiser admin accounts (currently single shared password).
- Enforce organiser_id NOT NULL at application creation paths.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision = 'add_organiser_model_001'
down_revision = 'add_telegram_id_001'
branch_labels = None
depends_on = None

_DEFAULT_SLUG = 'default'
_DEFAULT_NAME = 'Default Organiser'


def upgrade():
    conn = op.get_bind()
    dialect = conn.dialect.name  # 'postgresql' or 'sqlite'

    # ------------------------------------------------------------------
    # 1. Create organisers table
    # ------------------------------------------------------------------
    op.create_table(
        'organisers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_organisers_slug'),
    )
    op.create_index('ix_organisers_slug', 'organisers', ['slug'], unique=True)

    # ------------------------------------------------------------------
    # 2. Insert the default organiser
    # ------------------------------------------------------------------
    conn.execute(
        text(
            "INSERT INTO organisers (name, slug, status, created_at) "
            "VALUES (:name, :slug, 'active', CURRENT_TIMESTAMP)"
        ).bindparams(name=_DEFAULT_NAME, slug=_DEFAULT_SLUG)
    )

    # Retrieve the generated id (always 1 in practice, but don't hard-code)
    result = conn.execute(
        text("SELECT id FROM organisers WHERE slug = :slug").bindparams(
            slug=_DEFAULT_SLUG
        )
    )
    default_org_id = result.scalar()

    # ------------------------------------------------------------------
    # 3. Add organiser_id columns — nullable first so backfill can run
    # ------------------------------------------------------------------
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('organiser_id', sa.Integer(), nullable=True)
        )

    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('organiser_id', sa.Integer(), nullable=True)
        )

    # ------------------------------------------------------------------
    # 4. Backfill all existing rows → default organiser
    # ------------------------------------------------------------------
    conn.execute(
        text(
            "UPDATE players SET organiser_id = :oid WHERE organiser_id IS NULL"
        ).bindparams(oid=default_org_id)
    )
    conn.execute(
        text(
            "UPDATE rounds SET organiser_id = :oid WHERE organiser_id IS NULL"
        ).bindparams(oid=default_org_id)
    )

    # ------------------------------------------------------------------
    # 5. Enforce NOT NULL (safe after backfill)
    # ------------------------------------------------------------------
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.alter_column('organiser_id', nullable=False)

    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.alter_column('organiser_id', nullable=False)

    # ------------------------------------------------------------------
    # 6. Add FK constraints (PostgreSQL only — SQLite ignores them anyway)
    #    and indexes for query performance.
    # ------------------------------------------------------------------
    if dialect != 'sqlite':
        op.create_foreign_key(
            'fk_players_organiser_id',
            'players', 'organisers',
            ['organiser_id'], ['id'],
        )
        op.create_foreign_key(
            'fk_rounds_organiser_id',
            'rounds', 'organisers',
            ['organiser_id'], ['id'],
        )

    op.create_index('ix_players_organiser_id', 'players', ['organiser_id'])
    op.create_index('ix_rounds_organiser_id', 'rounds', ['organiser_id'])


def downgrade():
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Drop FK constraints (PostgreSQL only)
    if dialect != 'sqlite':
        op.drop_constraint('fk_rounds_organiser_id', 'rounds',
                           type_='foreignkey')
        op.drop_constraint('fk_players_organiser_id', 'players',
                           type_='foreignkey')

    op.drop_index('ix_rounds_organiser_id', table_name='rounds')
    op.drop_index('ix_players_organiser_id', table_name='players')

    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.drop_column('organiser_id')

    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.drop_column('organiser_id')

    op.drop_index('ix_organisers_slug', table_name='organisers')
    op.drop_table('organisers')
