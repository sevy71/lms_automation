#!/usr/bin/env python3
"""
Test script for WhatsApp sender
This script helps test the WhatsApp sender functionality
"""

import os
from dotenv import load_dotenv
from whatsapp_sender import WhatsAppSender

def test_whatsapp_sender():
    # Load environment variables
    load_dotenv()
    
    chrome_profile = os.environ.get('CHROME_USER_DATA_DIR')
    
    if not chrome_profile:
        print("❌ CHROME_USER_DATA_DIR not set in .env file")
        print("Please run: python3 setup_hybrid_system.py")
        return
    
    print("🧪 Testing WhatsApp Sender...")
    print(f"📁 Using Chrome profile: {chrome_profile}")
    
    try:
        # Initialize sender (this will open Chrome and load WhatsApp Web)
        sender = WhatsAppSender(user_data_dir=chrome_profile)
        
        print("\n✅ WhatsApp sender initialized successfully!")
        print("💡 You should see WhatsApp Web loaded in the Chrome window")
        print("📱 If you see a QR code, scan it with your phone to log in")
        
        # Ask if user wants to send a test message
        test_send = input("\nDo you want to send a test message? (y/N): ").lower()
        
        if test_send == 'y':
            phone = input("Enter phone number (with country code, e.g., +447123456789): ")
            if phone:
                test_message = "🧪 Test message from LMS WhatsApp system"
                success, result = sender.send_message(phone, test_message)
                
                if success:
                    print("✅ Test message sent successfully!")
                else:
                    print(f"❌ Failed to send test message: {result}")
            else:
                print("No phone number provided, skipping test message")
        
        input("\nPress Enter to close the browser...")
        sender.close()
        print("✅ Test completed!")
        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = test_whatsapp_sender()
    if not success:
        exit(1)