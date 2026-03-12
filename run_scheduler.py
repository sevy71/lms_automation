#!/usr/bin/env python3
"""
Run the background scheduler as a standalone process.

This process handles all automation (fixture polling, pick tokens, reminders,
eliminations, etc.) without serving any web traffic.

Process separation guarantees:
  - Scheduler restarts never interrupt web requests.
  - Web dynos can scale freely without spawning extra scheduler instances.
  - Only ONE scheduler instance is active by design: scale this process to 1.

The scheduler still needs the Flask app for database access (app_context),
but it never starts the HTTP server.
"""

import os
import sys
import time
import logging
from lms_automation.app import app
from lms_automation.scheduler import scheduler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    try:
        scheduler.init_app(app)

        logger.info("Starting background scheduler (standalone process)...")
        with app.app_context():
            scheduler.start()
        logger.info("Scheduler running. Web server is a separate process.")

        # APScheduler runs jobs in background threads; keep the main thread alive.
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Scheduler shutting down...")
        scheduler.stop()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Scheduler process error: {e}")
        try:
            scheduler.stop()
        except Exception:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
