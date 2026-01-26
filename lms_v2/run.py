#!/usr/bin/env python3
"""
LMS V2 - Main Entry Point
Runs Flask app with background scheduler
"""
import os
import sys
import logging

from lms_v2.app import app
from lms_v2.scheduler import scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Start the LMS application with scheduler."""
    try:
        # Initialize scheduler
        scheduler.init_app(app)

        # Start scheduler
        logger.info("Starting background scheduler...")
        with app.app_context():
            scheduler.start()
        logger.info("Scheduler started")

        # Run Flask
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"Starting LMS on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.stop()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        scheduler.stop()
        sys.exit(1)


if __name__ == '__main__':
    main()
