#!/usr/bin/env python3
"""
Quick test to verify the worker can connect to the Flask server.
Run this before testing WhatsApp to ensure the hybrid system will work.
"""

import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_URL = os.environ.get("BASE_URL")
WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")

print("=== Testing Hybrid WhatsApp System Connection ===")
print(f"BASE_URL: {BASE_URL}")
print(f"WORKER_API_TOKEN: {'✅ Set' if WORKER_API_TOKEN else '❌ Missing'}")

if not BASE_URL or not WORKER_API_TOKEN:
    print("❌ Missing required environment variables!")
    print("Make sure .env file is configured correctly.")
    exit(1)

# Test connection to the API
print(f"\n🔗 Testing connection to {BASE_URL}")

try:
    # Test /api/queue/next endpoint
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    api_url = f"{BASE_URL}/api/queue/next?limit=1"
    
    response = requests.get(api_url, headers=headers, timeout=10)
    print(f"📡 Response status: {response.status_code}")
    
    if response.status_code == 200:
        jobs = response.json()
        print(f"✅ Connection successful!")
        print(f"📥 Found {len(jobs)} pending job(s) in queue")
        
        if jobs:
            print("📄 Sample job:")
            job = jobs[0]
            print(f"   ID: {job.get('id')}")
            print(f"   Number: {job.get('number', '')[:10]}...")
            print(f"   Message: {job.get('message', '')[:50]}...")
        else:
            print("ℹ️  No pending messages (queue is empty)")
            
        print("\n🎯 Ready for WhatsApp sending!")
        
    elif response.status_code == 401:
        print("❌ Authentication failed - check WORKER_API_TOKEN")
    else:
        print(f"❌ Unexpected response: {response.status_code}")
        print(f"Response: {response.text}")
        
except requests.exceptions.ConnectionError as e:
    print("❌ Connection failed!")
    print("Possible issues:")
    if "localhost" in BASE_URL:
        print("  • Is your Flask server running? (python app.py)")
        print("  • Is it running on the correct port? (default: 5000)")
    else:
        print("  • Is the Railway app running?")
        print("  • Is the URL correct?")
    print(f"  • Error: {e}")
    
except Exception as e:
    print(f"❌ Error: {e}")

print("\n=== Connection Test Complete ===")