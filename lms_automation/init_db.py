# lms_automation/init_db.py
import sys
from sqlalchemy import text

try:
    from app import app, db
except ModuleNotFoundError:
    from lms_automation.app import app, db  # type: ignore

def ensure_schema():
    """Create tables and apply small runtime migrations that are safe for SQLite."""
    with app.app_context():
        # Create any missing tables defined in models
        db.create_all()

        # --- Small migration: add player.unreachable if missing ---
        try:
            with db.engine.connect() as conn:
                res = conn.execute(text("PRAGMA table_info('player')")).all()
                cols = [r[1] for r in res]
                if 'unreachable' not in cols:
                    app.logger.info('Adding missing player.unreachable column to database')
                    conn.execute(text("ALTER TABLE player ADD COLUMN unreachable BOOLEAN DEFAULT 0"))
                    conn.commit()
        except Exception as e:
            app.logger.exception('Failed to ensure player.unreachable: %s', e)

        # --- Small migration: add fixture.round_number if missing (optional convenience) ---
        try:
            with db.engine.connect() as conn:
                res = conn.execute(text("PRAGMA table_info('fixture')")).all()
                cols = [r[1] for r in res]
                if 'round_number' not in cols:
                    app.logger.info('Adding missing fixture.round_number column to database')
                    conn.execute(text("ALTER TABLE fixture ADD COLUMN round_number INTEGER"))
                    conn.commit()
        except Exception as e:
            app.logger.exception('Failed to ensure fixture.round_number: %s', e)

    print("DB initialized / migrated OK")


if __name__ == "__main__":
    ensure_schema()