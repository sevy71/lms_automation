#!/usr/bin/env python3
"""
Run LMS Flask app with background scheduler
This starts both the web server and the automation scheduler
"""

import os
import sys
import logging
from lms_automation.app import app
from lms_automation.scheduler import scheduler
from lms_automation.extensions import db
from sqlalchemy import text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def ensure_season_columns():
    """Ensure season tracking columns exist in rounds table.

    This is a safety net in case Alembic migrations don't run properly.
    """
    with app.app_context():
        try:
            # Check if columns exist by trying to query them
            db.session.execute(text("SELECT season_id, api_season_year FROM rounds LIMIT 1"))
            logger.info("Season tracking columns already exist")
        except Exception as e:
            logger.warning(f"Season columns missing, adding them: {e}")
            try:
                # Add columns if they don't exist (PostgreSQL syntax)
                db.session.execute(text(
                    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS season_id VARCHAR(10)"
                ))
                db.session.execute(text(
                    "ALTER TABLE rounds ADD COLUMN IF NOT EXISTS api_season_year INTEGER"
                ))
                db.session.commit()
                logger.info("Season tracking columns added successfully")
            except Exception as add_err:
                logger.error(f"Failed to add season columns: {add_err}")
                db.session.rollback()


def main():
    """Main entry point for running app with scheduler"""
    try:
        # Ensure database schema is up to date
        ensure_season_columns()

        # Initialize scheduler with Flask app
        scheduler.init_app(app)

        # Start scheduler inside Flask app context
        logger.info("Starting background scheduler...")
        with app.app_context():
            scheduler.start()
        logger.info("Scheduler started successfully")
        # Run Flask app
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"Starting Flask app on port {port}...")

        # Always start the Flask server so pick links are reachable in production.
        app.run(host='0.0.0.0', port=port, debug=False)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.stop()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error running application: {e}")
        scheduler.stop()
        sys.exit(1)

if __name__ == '__main__':
    main()
