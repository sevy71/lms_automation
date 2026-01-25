#!/usr/bin/env python3
"""
Run LMS Flask app with background scheduler
This starts both the web server and the automation scheduler

Resilience features:
- App starts even if Postgres is temporarily unavailable
- Scheduler jobs handle transient DB errors gracefully
- Connection pool automatically recovers from stale connections
"""

import os
import sys
import logging
from lms_automation.app import app
from lms_automation.scheduler import scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for running app with scheduler.

    The app is designed to survive transient Postgres unavailability:
    1. App startup uses retry logic (in app.py via wait_for_db)
    2. Connection pool uses pool_pre_ping to detect stale connections
    3. Scheduler jobs wrap DB operations in app_context
    4. App continues running even if initial DB check fails
    """
    try:
        # Initialize scheduler with Flask app
        scheduler.init_app(app)

        # Start scheduler inside Flask app context
        # The scheduler uses the same db session/engine as the Flask app
        logger.info("Starting background scheduler...")
        with app.app_context():
            scheduler.start()
        logger.info("Scheduler started successfully")

        # Run Flask app
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"Starting Flask app on port {port}...")

        # Always start the Flask server so pick links are reachable in production.
        # Even if DB is temporarily unavailable, the server should start.
        # pool_pre_ping will handle reconnection when DB comes back.
        app.run(host='0.0.0.0', port=port, debug=False)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.stop()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error running application: {e}")
        # Don't immediately exit on startup errors - let Railway restart handle it
        # But do stop the scheduler cleanly
        try:
            scheduler.stop()
        except Exception:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
