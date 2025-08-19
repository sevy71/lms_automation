#!/usr/bin/env python3
"""
WhatsApp Background Service for LMS
Runs continuously in the background, automatically processing queued messages.
No manual intervention needed!
"""

import os
import time
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add lms_automation to path
sys.path.append(str(Path(__file__).parent / 'lms_automation'))

from dotenv import load_dotenv
load_dotenv()

# Configuration
SERVICE_NAME = "LMS WhatsApp Service"
CHECK_INTERVAL = 30  # Check for new messages every 30 seconds
MAX_RETRIES = 3
STARTUP_DELAY = 5  # Wait 5 seconds on startup

class WhatsAppService:
    def __init__(self):
        self.running = True
        self.sender = None
        self.stats = {'sent': 0, 'failed': 0, 'started': datetime.now()}
        
    def log(self, message):
        """Log with timestamp"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {SERVICE_NAME}: {message}")
        
    def setup_signal_handlers(self):
        """Handle graceful shutdown"""
        def signal_handler(signum, frame):
            self.log("🛑 Shutdown signal received")
            self.running = False
            if self.sender:
                self.sender.close()
            sys.exit(0)
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def initialize_sender(self):
        """Initialize WhatsApp sender (only when needed)"""
        if self.sender:
            return True
            
        try:
            from whatsapp_sender import WhatsAppSender
            chrome_dir = os.environ.get('CHROME_USER_DATA_DIR')
            
            self.log("🚀 Initializing WhatsApp sender...")
            self.sender = WhatsAppSender(user_data_dir=chrome_dir)
            self.log("✅ WhatsApp sender ready!")
            return True
            
        except Exception as e:
            self.log(f"❌ Failed to initialize sender: {e}")
            self.log("💡 Make sure Chrome is closed and try restarting the service")
            return False
    
    def get_pending_jobs(self):
        """Get jobs from Railway database"""
        import requests
        
        base_url = os.environ.get('BASE_URL')
        token = os.environ.get('WORKER_API_TOKEN')
        
        if not base_url or not token:
            self.log("❌ Missing BASE_URL or WORKER_API_TOKEN")
            return []
        
        try:
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(f"{base_url}/api/queue/next?limit=10", 
                                  headers=headers, timeout=15)
            
            if response.status_code == 200:
                jobs = response.json()
                if jobs:
                    self.log(f"📥 Found {len(jobs)} pending message(s)")
                return jobs
            else:
                self.log(f"❌ API error: {response.status_code}")
                return []
                
        except Exception as e:
            self.log(f"❌ Connection error: {e}")
            return []
    
    def mark_job_status(self, job_id, status, error=None):
        """Report job status back to Railway"""
        import requests
        
        base_url = os.environ.get('BASE_URL')
        token = os.environ.get('WORKER_API_TOKEN')
        
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"id": job_id, "status": status}
        if error:
            payload["error"] = str(error)
        
        try:
            response = requests.post(f"{base_url}/api/queue/mark", 
                                   headers=headers, json=payload, timeout=15)
            return response.status_code == 200
        except:
            return False
    
    def process_job(self, job):
        """Process a single WhatsApp job"""
        job_id = job['id']
        number = job['number']
        message = job['message']
        
        self.log(f"📱 Sending to {number[:10]}...")
        
        try:
            success, status = self.sender.send_message(number, message)
            
            if success:
                self.log(f"✅ Message sent successfully")
                self.mark_job_status(job_id, "sent")
                self.stats['sent'] += 1
                return True
            else:
                self.log(f"❌ Send failed: {status}")
                self.mark_job_status(job_id, "failed", error=status)
                self.stats['failed'] += 1
                return False
                
        except Exception as e:
            error_msg = str(e)
            self.log(f"❌ Exception during send: {error_msg}")
            self.mark_job_status(job_id, "failed", error=error_msg)
            self.stats['failed'] += 1
            return False
    
    def run(self):
        """Main service loop"""
        self.setup_signal_handlers()
        
        self.log("🎯 Starting WhatsApp background service...")
        self.log(f"📊 Will check for messages every {CHECK_INTERVAL} seconds")
        self.log(f"⏱️  Startup delay: {STARTUP_DELAY} seconds")
        
        # Initial delay
        time.sleep(STARTUP_DELAY)
        
        while self.running:
            try:
                # Check for pending jobs
                jobs = self.get_pending_jobs()
                
                if jobs:
                    # Initialize sender if we have jobs
                    if not self.initialize_sender():
                        self.log("⏳ Retrying in 60 seconds...")
                        time.sleep(60)
                        continue
                    
                    self.log(f"🔄 Processing {len(jobs)} message(s)...")
                    
                    for job in jobs:
                        if not self.running:
                            break
                            
                        self.process_job(job)
                        
                        # Delay between messages to avoid detection
                        if self.running:
                            delay = 20  # 20 seconds between messages
                            self.log(f"⏱️  Waiting {delay}s before next message...")
                            time.sleep(delay)
                    
                    uptime = datetime.now() - self.stats['started']
                    self.log(f"📊 Session stats: {self.stats['sent']} sent, {self.stats['failed']} failed, uptime: {uptime}")
                
                # Wait before checking again
                if self.running:
                    time.sleep(CHECK_INTERVAL)
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                self.log(f"❌ Service error: {e}")
                self.log("⏳ Retrying in 60 seconds...")
                time.sleep(60)
        
        self.log("🛑 Service stopped")
        if self.sender:
            self.sender.close()

if __name__ == "__main__":
    service = WhatsAppService()
    service.run()