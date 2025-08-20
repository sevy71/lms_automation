#!/usr/bin/env python3
"""
Enhanced WhatsApp Worker with autonomous operation
Improvements:
- Better error handling and recovery
- Health monitoring and automatic restarts
- Graceful shutdown handling
- More robust session management
"""

import os
import sys
import time
import random
import signal
import logging
import requests
from dotenv import load_dotenv, find_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/whatsapp_worker.log')
    ]
)
logger = logging.getLogger(__name__)

# Add the script's directory to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Import enhanced WhatsApp sender
try:
    from whatsapp_sender_enhanced import WhatsAppSenderEnhanced
except ImportError:
    logger.error("Could not import enhanced sender, falling back to original")
    from whatsapp_sender import WhatsAppSender as WhatsAppSenderEnhanced

# Load environment variables
load_dotenv(find_dotenv())

class WhatsAppWorker:
    def __init__(self):
        self.running = True
        self.sender = None
        self.health_check_interval = 300  # 5 minutes
        self.last_health_check = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5
        
        # Configuration from environment
        self.base_url = os.environ.get("BASE_URL")
        self.worker_token = os.environ.get("WORKER_API_TOKEN")
        self.chrome_data_dir = os.environ.get("CHROME_USER_DATA_DIR")
        self.headless = os.environ.get("CHROME_HEADLESS", "true").lower() == "true"
        
        # Validate configuration
        if not all([self.base_url, self.worker_token, self.chrome_data_dir]):
            logger.error("Missing required environment variables")
            raise ValueError("BASE_URL, WORKER_API_TOKEN, and CHROME_USER_DATA_DIR are required")
        
        logger.info("=== WhatsApp Worker Configuration ===")
        logger.info(f"BASE_URL: {self.base_url}")
        logger.info(f"WORKER_TOKEN: {'*' * 10}...")
        logger.info(f"CHROME_DATA_DIR: {self.chrome_data_dir}")
        logger.info(f"HEADLESS: {self.headless}")
        logger.info("=====================================")
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def initialize_sender(self):
        """Initialize or reinitialize the WhatsApp sender"""
        if self.sender:
            try:
                self.sender.close()
            except Exception as e:
                logger.warning(f"Error closing previous sender: {e}")
        
        try:
            logger.info("🚀 Initializing WhatsApp sender...")
            self.sender = WhatsAppSenderEnhanced(
                user_data_dir=self.chrome_data_dir,
                headless=self.headless,
                max_retries=3
            )
            logger.info("✅ WhatsApp sender initialized successfully")
            self.consecutive_failures = 0
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize WhatsApp sender: {e}")
            self.consecutive_failures += 1
            return False

    def get_jobs(self, limit=10):
        """Get pending jobs from the API"""
        headers = {"Authorization": f"Bearer {self.worker_token}"}
        api_url = f"{self.base_url}/api/queue/next?limit={limit}"
        
        try:
            response = requests.get(api_url, headers=headers, timeout=15)
            response.raise_for_status()
            jobs = response.json()
            logger.info(f"📥 Retrieved {len(jobs)} pending job(s)")
            return jobs
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Connection error: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ API error: {e}")
            return None

    def mark_job_status(self, job_id, status, error=None):
        """Mark job status in the API"""
        headers = {"Authorization": f"Bearer {self.worker_token}"}
        api_url = f"{self.base_url}/api/queue/mark"
        
        payload = {"id": job_id, "status": status}
        if error:
            payload["error"] = str(error)
        
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
            logger.info(f"📤 Marked job {job_id} as {status}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to mark job {job_id}: {e}")
            return False

    def health_check(self):
        """Perform health check on the sender"""
        current_time = time.time()
        if current_time - self.last_health_check < self.health_check_interval:
            return True
        
        self.last_health_check = current_time
        
        if not self.sender:
            logger.warning("💊 No sender instance for health check")
            return False
        
        try:
            healthy = self.sender.check_session_health()
            if healthy:
                logger.info("💊 Health check passed")
                return True
            else:
                logger.warning("💊 Health check failed, attempting recovery...")
                if self.sender.recover_session():
                    logger.info("💊 Session recovery successful")
                    return True
                else:
                    logger.error("💊 Session recovery failed")
                    return False
                    
        except Exception as e:
            logger.error(f"💊 Health check error: {e}")
            return False

    def process_job(self, job):
        """Process a single job"""
        job_id = job.get("id")
        phone_number = job.get("number")
        message = job.get("message")
        
        logger.info(f"📤 Processing job {job_id}: {phone_number}")
        
        try:
            success, result = self.sender.send_message(phone_number, message)
            
            if success:
                logger.info(f"✅ Job {job_id} completed successfully")
                self.mark_job_status(job_id, "sent")
                self.consecutive_failures = 0
                return True
            else:
                logger.error(f"❌ Job {job_id} failed: {result}")
                self.mark_job_status(job_id, "failed", error=result)
                return False
                
        except Exception as e:
            logger.error(f"❌ Critical error processing job {job_id}: {e}")
            self.mark_job_status(job_id, "failed", error=str(e))
            self.consecutive_failures += 1
            return False

    def run(self):
        """Main worker loop"""
        logger.info("🚀 Starting enhanced WhatsApp worker...")
        
        # Initialize sender
        if not self.initialize_sender():
            logger.error("Failed to initialize sender, exiting")
            return
        
        while self.running:
            try:
                # Health check
                if not self.health_check():
                    logger.warning("Health check failed, reinitializing sender...")
                    if not self.initialize_sender():
                        logger.error("Failed to reinitialize sender")
                        time.sleep(60)
                        continue
                
                # Check for too many consecutive failures
                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.error(f"Too many consecutive failures ({self.consecutive_failures}), reinitializing...")
                    if not self.initialize_sender():
                        logger.error("Failed to reinitialize after failures")
                        time.sleep(300)  # Wait 5 minutes before trying again
                        continue
                
                # Get jobs
                jobs = self.get_jobs()
                
                if jobs is None:
                    logger.warning("API error, waiting 60 seconds...")
                    time.sleep(60)
                    continue
                
                if not jobs:
                    logger.info("No pending jobs, waiting 30 seconds...")
                    time.sleep(30)
                    continue
                
                # Process jobs
                logger.info(f"Processing {len(jobs)} job(s)...")
                for job in jobs:
                    if not self.running:
                        break
                    
                    self.process_job(job)
                    
                    # Random delay between messages (anti-detection)
                    if len(jobs) > 1:  # Only add delay if multiple jobs
                        delay = random.randint(5, 15)  # Reduced from 20-60 to 5-15 seconds
                        logger.info(f"💤 Anti-detection delay: {delay}s...")
                        time.sleep(delay)
                
                # Brief pause before next poll
                time.sleep(5)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(30)
        
        # Cleanup
        logger.info("🔒 Shutting down worker...")
        if self.sender:
            try:
                self.sender.close()
            except Exception as e:
                logger.error(f"Error closing sender: {e}")
        
        logger.info("✅ Worker shutdown complete")


def main():
    """Main entry point"""
    try:
        worker = WhatsAppWorker()
        worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.error(f"Worker failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()