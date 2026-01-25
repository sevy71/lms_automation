"""Add season tracking fields to rounds

Revision ID: a1b2c3d4e5f6
Revises: c16973330891
Create Date: 2025-01-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'c16973330891'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('season_id', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('api_season_year', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.drop_column('api_season_year')
        batch_op.drop_column('season_id')
