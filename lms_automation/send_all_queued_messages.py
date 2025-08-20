
# lms_automation/send_all_queued_messages.py
import os
import sys
import time
import random
import requests
from dotenv import load_dotenv, find_dotenv

# Add the script's directory to the Python path to resolve local imports
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from whatsapp_sender import WhatsAppSender

# Load environment variables
load_dotenv(find_dotenv())

# --- Configuration ---
BASE_URL = os.environ.get("BASE_URL")
WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
CHROME_USER_DATA_DIR = os.environ.get("CHROME_USER_DATA_DIR")

def get_all_queued_jobs():
    """Fetches all pending messages from the server."""
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    # This new API endpoint will need to be created in app.py
    api_url = f"{BASE_URL}/api/queue/all_pending"
    
    print(f"Connecting to: {api_url}")
    
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        jobs = response.json()
        print(f"Found {len(jobs)} pending job(s)")
        return jobs
    except requests.exceptions.RequestException as e:
        print(f"API Error (get_all_queued_jobs): {e}")
        return None

def mark_job_status(job_id, status, error=None):
    """Reports the outcome of a job back to the server."""
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    api_url = f"{BASE_URL}/api/queue/mark"
    
    payload = {"id": job_id, "status": status}
    if error:
        payload["error"] = str(error)
    
    print(f"Reporting job {job_id} as {status}")
        
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"API Error (mark_job_status for job {job_id}): {e}")
        return False

def main():
    """
    Initializes the sender, fetches all queued jobs, sends them, and exits.
    """
    print("--- Starting Manual WhatsApp Sender ---")
    
    if not all([BASE_URL, WORKER_API_TOKEN]):
        print("FATAL: Missing one or more required environment variables.")
        exit(1)

    sender = None
    try:
        sender = WhatsAppSender(user_data_dir=None)
        print("--- WhatsApp Sender Initialized ---")
        time.sleep(10) # Give WhatsApp Web time to load
            
        jobs = get_all_queued_jobs()
    
        if jobs is None:
            print("Could not retrieve jobs. Exiting.")
            return

        if not jobs:
            print("No pending jobs found. Exiting.")
            return
                
        print(f"Found {len(jobs)} job(s). Processing...")
        for job in jobs:
            job_id = job.get("id")
            number = job.get("number")
            message = job.get("message")
            
            print(f"  - Sending message to {number} (Job ID: {job_id})")
            
            try:
                success, status_message = sender.send_message(number, message)
                
                if success:
                    print(f"    -> Success.")
                    mark_job_status(job_id, "sent")
                else:
                    print(f"    -> Failed: {status_message}")
                    mark_job_status(job_id, "failed", error=status_message)

                time.sleep(random.randint(5, 15))

            except Exception as e:
                print(f"    -> CRITICAL ERROR during send: {e}")
                mark_job_status(job_id, "failed", error=e)
    
    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        if sender:
            print("--- Closing WhatsApp Sender ---")
            sender.close()
        print("--- All jobs processed. Exiting. ---")

if __name__ == "__main__":
    main()
