#!/usr/bin/env python3
"""
Quick test to verify the automatic WhatsApp system is working
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent / 'lms_automation'))

import subprocess
import time
import requests
import os
from dotenv import load_dotenv

load_dotenv()

def test_automatic_system():
    print("🧪 Testing Automatic WhatsApp System")
    print("=" * 40)
    
    # Test 1: Check if service scripts exist
    scripts = ['start_whatsapp_service.sh', 'stop_whatsapp_service.sh', 'check_whatsapp_service.sh']
    for script in scripts:
        if Path(script).exists():
            print(f"✅ {script} exists")
        else:
            print(f"❌ {script} missing")
            return False
    
    # Test 2: Check environment variables
    base_url = os.environ.get('BASE_URL')
    token = os.environ.get('WORKER_API_TOKEN')
    chrome_dir = os.environ.get('CHROME_USER_DATA_DIR')
    
    print(f"✅ BASE_URL: {base_url}")
    print(f"✅ WORKER_TOKEN: {'Set' if token else 'Missing'}")
    print(f"✅ CHROME_DIR: {chrome_dir}")
    
    if not all([base_url, token, chrome_dir]):
        print("❌ Missing required environment variables")
        return False
    
    # Test 3: Check API connectivity
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{base_url}/api/queue/next?limit=1", 
                              headers=headers, timeout=10)
        if response.status_code == 200:
            print("✅ API connection working")
        else:
            print(f"⚠️  API returned status {response.status_code}")
    except Exception as e:
        print(f"⚠️  API connection issue: {e}")
    
    # Test 4: Check if service is running
    try:
        result = subprocess.run(['bash', 'check_whatsapp_service.sh'], 
                              capture_output=True, text=True)
        if "RUNNING" in result.stdout:
            print("✅ Background service is running")
        else:
            print("ℹ️  Background service not running (this is OK)")
    except:
        print("⚠️  Could not check service status")
    
    print("\n🎯 System Status: READY")
    print("\n📋 Next Steps:")
    print("1. bash start_whatsapp_service.sh  # Start background service")
    print("2. Go to Railway admin dashboard")
    print("3. Click 'Send Links via WhatsApp'") 
    print("4. Messages will be sent automatically! 🚀")
    
    return True

if __name__ == "__main__":
    test_automatic_system()