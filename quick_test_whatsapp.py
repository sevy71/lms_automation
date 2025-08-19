#!/usr/bin/env python3
"""
Quick test to send a WhatsApp message using your existing setup.
This bypasses the API and tests WhatsApp sending directly.
"""

import os
import sys
from dotenv import load_dotenv

# Add the lms_automation directory to Python path
sys.path.append('lms_automation')

# Load environment
load_dotenv()

try:
    from whatsapp_sender import WhatsAppSender
    
    print("🚀 Quick WhatsApp Test")
    print(f"Chrome profile: {os.environ.get('CHROME_USER_DATA_DIR')}")
    
    # Initialize sender
    sender = WhatsAppSender(user_data_dir=os.environ.get('CHROME_USER_DATA_DIR'))
    print("✅ WhatsApp sender initialized")
    print("📱 Chrome should open with WhatsApp Web")
    
    # Test with your own number
    test_number = "+447545851594"  # Your number from the database
    test_message = "🎯 LMS WhatsApp Test - System is working!"
    
    print(f"\n📞 Testing send to {test_number}")
    print(f"💬 Message: {test_message}")
    
    input("\n⏳ Press Enter once WhatsApp Web is ready (scan QR if needed)...")
    
    success, status = sender.send_message(test_number, test_message)
    
    if success:
        print("✅ SUCCESS: Message sent successfully!")
        print("🎉 Your WhatsApp system is working!")
    else:
        print(f"❌ FAILED: {status}")
        print("💡 Check WhatsApp Web interface for issues")
    
    input("\nPress Enter to close...")
    sender.close()
    
except Exception as e:
    print(f"❌ Error: {e}")
    print("💡 Make sure Chrome is closed before running this test")