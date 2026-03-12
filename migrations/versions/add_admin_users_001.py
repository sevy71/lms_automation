"""Add admin_users table for per-organiser admin accounts (Phase 2c)

Revision ID: add_admin_users_001
Revises: add_organiser_model_001
Create Date: 2026-03-12 00:00:00.000000

Migration behaviour
-------------------
1. Creates the new ``admin_users`` table.
2. Bootstraps a super-admin account linked to the 'default' organiser.
   - Username: value of BOOTSTRAP_ADMIN_USERNAME env var (default: 'admin')
   - Password: value of ADMIN_PASSWORD env var, hashed via Werkzeug.
     If ADMIN_PASSWORD is not set or is the insecure default 'admin123', a
     WARNING is printed and 'admin123' is used as a temporary password.
     Change it immediately via the admin interface.

Rollback drops the admin_users table.
No existing columns are dropped or renamed — fully backward-compatible.
"""
import os
import warnings

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.sql import text


# revision identifiers
revision = 'add_admin_users_001'
down_revision = 'add_organiser_model_001'
branch_labels = None
depends_on = None


def _hash_password(password: str) -> str:
    """Hash password using Werkzeug (same library Flask uses)."""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


def upgrade():
    conn = op.get_bind()
    dialect = conn.dialect.name  # 'postgresql' or 'sqlite'
    inspector = inspect(conn)

    # ------------------------------------------------------------------
    # 1. Create admin_users table (idempotent)
    # ------------------------------------------------------------------
    table_exists = inspector.has_table('admin_users')

    if not table_exists:
        op.create_table(
            'admin_users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(length=100), nullable=False),
            sa.Column('password_hash', sa.String(length=255), nullable=False),
            sa.Column('organiser_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=20), nullable=False,
                      server_default='organiser_admin'),
            sa.Column('is_active', sa.Boolean(), nullable=False,
                      server_default=sa.text('1') if dialect == 'sqlite' else sa.text('true')),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('username', name='uq_admin_users_username'),
        )

        op.create_index('ix_admin_users_username', 'admin_users', ['username'], unique=True)
        op.create_index('ix_admin_users_organiser_id', 'admin_users', ['organiser_id'])

        # Add FK constraint (PostgreSQL only — SQLite silently ignores FKs anyway)
        if dialect != 'sqlite':
            op.create_foreign_key(
                'fk_admin_users_organiser_id',
                'admin_users', 'organisers',
                ['organiser_id'], ['id'],
            )
    else:
        print("[MIGRATION] admin_users already exists — skipping table creation.")

    # ------------------------------------------------------------------
    # 2. Bootstrap super-admin account
    # ------------------------------------------------------------------
    # Resolve default organiser id
    result = conn.execute(
        text("SELECT id FROM organisers WHERE slug = 'default' LIMIT 1")
    )
    row = result.fetchone()
    if row is None:
        warnings.warn(
            "admin_users migration: 'default' organiser not found — "
            "super-admin bootstrap skipped. Run add_organiser_model_001 first.",
            RuntimeWarning,
        )
        return

    default_org_id = row[0]

    # Resolve credentials from env
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    admin_username = os.environ.get('BOOTSTRAP_ADMIN_USERNAME', 'admin')

    if not admin_password or admin_password == 'admin123':
        admin_password = 'admin123'
        print(
            "\n[MIGRATION WARNING] ADMIN_PASSWORD not set or is insecure default.\n"
            "  Super-admin bootstrapped with username='%s' password='admin123'.\n"
            "  CHANGE THIS PASSWORD IMMEDIATELY via the admin interface.\n" % admin_username
        )
    else:
        print(
            "\n[MIGRATION] Bootstrapping super-admin username='%s' from ADMIN_PASSWORD.\n" % admin_username
        )

    hashed = _hash_password(admin_password)

    # Check if an admin user already exists (idempotent re-run)
    existing = conn.execute(
        text("SELECT id FROM admin_users WHERE username = :uname").bindparams(uname=admin_username)
    ).fetchone()

    if existing is None:
        conn.execute(
            text(
                "INSERT INTO admin_users "
                "(username, password_hash, organiser_id, role, is_active, created_at) "
                "VALUES (:uname, :phash, :oid, 'super_admin', :active, CURRENT_TIMESTAMP)"
            ).bindparams(
                uname=admin_username,
                phash=hashed,
                oid=default_org_id,
                active=True,
            )
        )
        print("[MIGRATION] Super-admin '%s' created successfully.\n" % admin_username)
    else:
        print("[MIGRATION] Super-admin '%s' already exists — skipping insert.\n" % admin_username)


def downgrade():
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect != 'sqlite':
        op.drop_constraint('fk_admin_users_organiser_id', 'admin_users',
                           type_='foreignkey')

    op.drop_index('ix_admin_users_organiser_id', table_name='admin_users')
    op.drop_index('ix_admin_users_username', table_name='admin_users')
    op.drop_table('admin_users')
