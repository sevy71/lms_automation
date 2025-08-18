
from lms_automation.app import app, db
from sqlalchemy import text
from lms_automation.models import WhatsAppSend

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        try:
            with db.engine.connect() as conn:
                res = conn.execute(text("PRAGMA table_info('player')")).all()
                cols = [r[1] for r in res]
                if 'unreachable' not in cols:
                    app.logger.info('Adding missing player.unreachable column to database')
                    conn.execute(text("ALTER TABLE player ADD COLUMN unreachable BOOLEAN DEFAULT 0"))
                    conn.commit()
                
                res = conn.execute(text("PRAGMA table_info('whatsapp_send')")).all()
                if not res:
                    app.logger.info('Creating missing whatsapp_send table')
                    WhatsAppSend.__table__.create(db.engine)
        except Exception as e:
            app.logger.exception('Failed to ensure DB schema: %s', e)
