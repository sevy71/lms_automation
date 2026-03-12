#!/usr/bin/env python3
"""
Run the Flask web server without the background scheduler.

The scheduler runs as a separate process (see run_scheduler.py).
Keeping them separate means:
  - Web uptime is not tied to scheduler workload or restarts.
  - Multiple web dynos can scale freely without spawning extra schedulers.
  - Scheduler can be restarted without dropping any in-flight web requests.

Scale the 'worker' process to exactly 1 in production to ensure only
one scheduler instance is ever active.
"""

import os
import sys
import logging
from lms_automation.app import app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    try:
        port = int(os.environ.get('PORT', 5000))
        logger.info(f"Starting Flask web server on port {port} (scheduler runs separately)...")
        app.run(host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        logger.info("Web server shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Web server error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
