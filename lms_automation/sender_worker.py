# lms_automation/sender_worker.py
import os
import time
import random
import requests
from dotenv import load_dotenv, find_dotenv

# It's crucial that this worker can import the existing WhatsAppSender
from whatsapp_sender import WhatsAppSender

# Load environment variables from .env file
# Explicitly load from the project root's .env file
load_dotenv(find_dotenv())

# --- Configuration ---
# The BASE_URL of your deployed Flask application
BASE_URL = os.environ.get("BASE_URL")
# The secret token to authenticate with the API
WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
# Path to your Chrome user data directory
CHROME_USER_DATA_DIR = os.environ.get("CHROME_USER_DATA_DIR")

# --- Validation ---
if not all([BASE_URL, WORKER_API_TOKEN, CHROME_USER_DATA_DIR]):
    print("FATAL: Missing one or more required environment variables:")
    print(" - BASE_URL (e.g., https://your-app-name.onrender.com)")
    print(" - WORKER_API_TOKEN (the shared secret)")
    print(" - CHROME_USER_DATA_DIR (path to your chrome profile)")
    exit(1)


def get_jobs(limit=10):
    """Polls the server for the next batch of pending messages."""
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    api_url = f"{BASE_URL}/api/queue/next?limit={limit}"
    
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"API Error (get_jobs): {e}")
        return None

def mark_job_status(job_id, status, error=None):
    """Reports the outcome of a job back to the server."""
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    api_url = f"{BASE_URL}/api/queue/mark"
    
    payload = {"id": job_id, "status": status}
    if error:
        payload["error"] = str(error)
        
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"API Error (mark_job_status for job {job_id}): {e}")
        return False

def main_loop():
    """
    The main worker loop.
    Initializes the sender, then continuously polls for and processes jobs.
    """
    print("--- Initializing WhatsApp Sender ---")
    print(f"Using Chrome user data dir: {CHROME_USER_DATA_DIR}")
    # Initialize the sender once at the start
    sender = WhatsAppSender(user_data_dir=CHROME_USER_DATA_DIR)
    print("--- WhatsApp Sender Initialized ---")
    
    while True:
        print("\nPolling for new jobs...")
        jobs = get_jobs()
        
        if jobs is None:
            # An error occurred in get_jobs, wait before retrying
            print("Waiting 60 seconds due to API error.")
            time.sleep(60)
            continue

        if not jobs:
            # No jobs in the queue, wait for a bit
            print("No pending jobs found. Waiting 30 seconds.")
            time.sleep(30)
            continue
            
        print(f"Found {len(jobs)} job(s). Processing...")
        for job in jobs:
            job_id = job.get("id")
            number = job.get("number")
            message = job.get("message")
            
            print(f"  - Sending message to {number} (Job ID: {job_id})")
            
            try:
                # The core sending logic
                success, status_message = sender.send_message(number, message)
                
                if success:
                    print(f"    -> Success.")
                    mark_job_status(job_id, "sent")
                else:
                    # The sender returns a reason for failure
                    print(f"    -> Failed: {status_message}")
                    mark_job_status(job_id, "failed", error=status_message)

                # Be human, wait a random amount of time between sends
                sleep_time = random.randint(8, 15)
                print(f"    (Sleeping for {sleep_time}s)")
                time.sleep(sleep_time)

            except Exception as e:
                # Catch any unexpected exceptions during the sending process
                print(f"    -> CRITICAL ERROR during send: {e}")
                mark_job_status(job_id, "failed", error=e)
        
        # Wait a shorter time before polling for the next batch
        print("Batch complete. Waiting 5 seconds.")
        time.sleep(5)

if __name__ == "__main__":
    print("--- Starting Local WhatsApp Worker ---")
    main_loop()
