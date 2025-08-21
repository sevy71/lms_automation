#!/usr/bin/env python3
"""
Local WhatsApp Worker - Connects to Railway database and processes queued messages
Run this on your local machine while the web app runs on Railway
"""
import os
import sys
import time
import requests
from dotenv import load_dotenv

# Add project directories to Python path
project_root = os.path.dirname(os.path.abspath(__file__))
lms_automation_dir = os.path.join(project_root, 'lms_automation')
sys.path.insert(0, project_root)
sys.path.insert(0, lms_automation_dir)

def main():
    print("🤖 Local WhatsApp Worker Starting...")
    print("=" * 50)
    
    # Load environment variables
    load_dotenv()
    
    # Configuration
    BASE_URL = os.environ.get("BASE_URL")
    WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
    CHROME_USER_DATA_DIR = os.environ.get("CHROME_USER_DATA_DIR")
    
    print(f"Base URL: {BASE_URL}")
    print(f"Chrome Data Dir: {CHROME_USER_DATA_DIR}")
    print(f"Worker Token: {'✓' if WORKER_API_TOKEN else '✗'}")
    
    if not all([BASE_URL, WORKER_API_TOKEN]):
        print("❌ Missing required environment variables (BASE_URL, WORKER_API_TOKEN)")
        print("Make sure your .env file is configured properly")
        return 1
    
    # Test API connection first
    print("\n🔗 Testing API connection...")
    try:
        headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
        response = requests.get(f"{BASE_URL}/api/queue/all_pending", headers=headers, timeout=10)
        
        if response.status_code == 200:
            pending_count = len(response.json())
            print(f"✅ API connection successful - {pending_count} pending messages")
        else:
            print(f"❌ API error: HTTP {response.status_code}")
            return 1
            
    except Exception as e:
        print(f"❌ Cannot connect to API: {e}")
        print("Make sure your Railway app is running and BASE_URL is correct")
        return 1
    
    # Start the WhatsApp sender
    print("\n📱 Starting WhatsApp sender...")
    try:
        from lms_automation.send_all_queued_messages import main as sender_main
        sender_main()
        print("✅ WhatsApp sending completed!")
        return 0
        
    except Exception as e:
        print(f"❌ WhatsApp sender failed: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())