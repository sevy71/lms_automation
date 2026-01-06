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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Main entry point for running app with scheduler"""
    try:
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
