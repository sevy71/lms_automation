#!/usr/bin/env python3
"""
Simple test script to check if manual send can connect to API endpoints
"""
import os
import sys
import requests
from dotenv import load_dotenv, find_dotenv

# Add the script's directory to the Python path
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

def test_api_connection():
    """Test API connection without Chrome"""
    print("🔗 Testing API Connection for Manual Send")
    print("=" * 50)
    
    # Load environment variables
    load_dotenv(find_dotenv())
    
    BASE_URL = os.environ.get("BASE_URL")
    WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
    
    if not BASE_URL or not WORKER_API_TOKEN:
        print("❌ Missing required environment variables")
        return False
    
    print(f"API Base URL: {BASE_URL}")
    print("Testing API endpoints...")
    
    headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
    
    # Test 1: Get all pending messages
    try:
        api_url = f"{BASE_URL}/api/queue/all_pending"
        print(f"\n📡 Testing: GET {api_url}")
        response = requests.get(api_url, headers=headers, timeout=10)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   Response: Found {len(data)} pending messages")
            print("   ✓ API endpoint working")
        else:
            print(f"   ❌ API error: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False
    
    # Test 2: Mark job status endpoint
    try:
        api_url = f"{BASE_URL}/api/queue/mark"
        print(f"\n📡 Testing: POST {api_url}")
        
        # Test with dummy data
        test_payload = {"id": 999999, "status": "test", "error": "test_error"}
        response = requests.post(api_url, headers=headers, json=test_payload, timeout=10)
        print(f"   Status: {response.status_code}")
        
        if response.status_code in [200, 404]:  # 404 is expected for non-existent job ID
            print("   ✓ API endpoint accessible")
        else:
            print(f"   ❌ API error: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False
    
    print("\n" + "=" * 50)
    print("🎉 API connection test passed!")
    print("💡 The issue is likely with Chrome WebDriver, not the API")
    print("💡 You can manually send messages by:")
    print("   1. Queue messages through the web interface")
    print("   2. Fix Chrome/WebDriver configuration")
    print("   3. Run the manual sender script")
    
    return True

if __name__ == "__main__":
    success = test_api_connection()
    sys.exit(0 if success else 1)