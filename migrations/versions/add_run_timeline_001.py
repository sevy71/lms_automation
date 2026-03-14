"""Add run timeline tables (automation_runs, run_decisions, run_actions, run_checkpoints, run_audit_events)

Revision ID: add_run_timeline_001
Revises: add_admin_users_001
Create Date: 2026-03-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_run_timeline_001'
down_revision = 'add_admin_users_001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'automation_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=36), nullable=False),
        sa.Column('organiser_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('trigger_type', sa.String(length=20), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('total_actions', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('affected_players', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('affected_rounds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('affected_reminders', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_info', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['organiser_id'], ['organisers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id'),
    )
    with op.batch_alter_table('automation_runs', schema=None) as batch_op:
        batch_op.create_index('ix_automation_runs_run_id', ['run_id'], unique=True)
        batch_op.create_index('ix_automation_runs_organiser_id', ['organiser_id'])
        batch_op.create_index('ix_automation_runs_status', ['status'])

    op.create_table(
        'run_decisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id_fk', sa.Integer(), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('rule_matched', sa.String(length=200), nullable=True),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('confidence', sa.String(length=20), nullable=True),
        sa.Column('intended_action', sa.String(length=200), nullable=True),
        sa.Column('expected_impact', sa.Text(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id_fk'], ['automation_runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('run_decisions', schema=None) as batch_op:
        batch_op.create_index('ix_run_decisions_run_id_fk', ['run_id_fk'])

    op.create_table(
        'run_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id_fk', sa.Integer(), nullable=False),
        sa.Column('decision_id', sa.Integer(), nullable=True),
        sa.Column('action_type', sa.String(length=100), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('actual_impact', sa.Text(), nullable=True),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column('reversible', sa.Boolean(), nullable=False),
        sa.Column('reversal_metadata', sa.Text(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id_fk'], ['automation_runs.id']),
        sa.ForeignKeyConstraint(['decision_id'], ['run_decisions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('run_actions', schema=None) as batch_op:
        batch_op.create_index('ix_run_actions_run_id_fk', ['run_id_fk'])
        batch_op.create_index('ix_run_actions_decision_id', ['decision_id'])

    op.create_table(
        'run_checkpoints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('checkpoint_id', sa.String(length=36), nullable=False),
        sa.Column('run_id_fk', sa.Integer(), nullable=True),
        sa.Column('organiser_id', sa.Integer(), nullable=True),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('snapshot', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['run_id_fk'], ['automation_runs.id']),
        sa.ForeignKeyConstraint(['organiser_id'], ['organisers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('checkpoint_id'),
    )
    with op.batch_alter_table('run_checkpoints', schema=None) as batch_op:
        batch_op.create_index('ix_run_checkpoints_checkpoint_id', ['checkpoint_id'], unique=True)
        batch_op.create_index('ix_run_checkpoints_run_id_fk', ['run_id_fk'])
        batch_op.create_index('ix_run_checkpoints_organiser_id', ['organiser_id'])

    op.create_table(
        'run_audit_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=36), nullable=True),
        sa.Column('organiser_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['organiser_id'], ['organisers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('run_audit_events', schema=None) as batch_op:
        batch_op.create_index('ix_run_audit_events_run_id', ['run_id'])
        batch_op.create_index('ix_run_audit_events_organiser_id', ['organiser_id'])


def downgrade():
    op.drop_table('run_audit_events')
    op.drop_table('run_checkpoints')
    op.drop_table('run_actions')
    op.drop_table('run_decisions')
    op.drop_table('automation_runs')
