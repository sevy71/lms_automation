"""Add telegram_id to Player model

Revision ID: add_telegram_id_001
Revises: c16973330891
Create Date: 2025-01-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_telegram_id_001'
down_revision = 'c16973330891'
branch_labels = None
depends_on = None


def upgrade():
    # Add telegram_id column to players table
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_id', sa.String(length=50), nullable=True))


def downgrade():
    # Remove telegram_id column from players table
    with op.batch_alter_table('players', schema=None) as batch_op:
        batch_op.drop_column('telegram_id')