# lms_automation/init_db.py
from app import app, db
from sqlalchemy import text

with app.app_context():
    db.create_all()

    # Ensure small runtime migration: add `unreachable` column to `player` if missing.
    try:
        # Use PRAGMA to inspect existing columns in sqlite
        with db.engine.connect() as conn:
            res = conn.execute(text("PRAGMA table_info('player')")).all()
            cols = [r[1] for r in res]
            if 'unreachable' not in cols:
                app.logger.info('Adding missing player.unreachable column to database')
                conn.execute(text("ALTER TABLE player ADD COLUMN unreachable BOOLEAN DEFAULT 0"))
                conn.commit()
    except Exception as e:
        app.logger.exception('Failed to ensure DB schema: %s', e)

    # Ensure small runtime migration: add `round_number` column to `fixture` if missing.
    try:
        # Use PRAGMA to inspect existing columns in sqlite
        with db.engine.connect() as conn:
            res = conn.execute(text("PRAGMA table_info('fixture')")).all()
            cols = [r[1] for r in res]
            if 'round_number' not in cols:
                app.logger.info('Adding missing fixture.round_number column to database')
                conn.execute(text("ALTER TABLE fixture ADD COLUMN round_number INTEGER"))
                conn.commit()
    except Exception as e:
        app.logger.exception('Failed to ensure DB schema: %s', e)
