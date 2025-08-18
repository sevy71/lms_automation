#!/usr/bin/env python3
"""
Test script for admin dashboard
This script tests if the admin dashboard route works correctly
"""

import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

def test_admin_dashboard():
    """Test the admin dashboard route"""
    try:
        from app import app
        
        with app.test_client() as client:
            # Test the admin dashboard route
            print("🧪 Testing admin dashboard route...")
            response = client.get('/admin_dashboard')
            
            print(f"📊 Status Code: {response.status_code}")
            
            if response.status_code == 200:
                print("✅ Admin dashboard loads successfully!")
                print(f"📄 Response length: {len(response.data)} bytes")
                
                # Check if it contains expected content
                if b'LMS Admin Dashboard' in response.data:
                    print("✅ Page contains expected title")
                else:
                    print("⚠️  Page missing expected title")
                
                return True
            else:
                print(f"❌ Admin dashboard failed with status {response.status_code}")
                print("Response data:", response.data.decode('utf-8', errors='ignore')[:500])
                return False
                
    except Exception as e:
        print(f"❌ Error testing admin dashboard: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_admin_dashboard()
    if not success:
        exit(1)
    print("🎉 Admin dashboard test completed successfully!")