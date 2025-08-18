#!/usr/bin/env python3
"""
Setup script for the LMS Hybrid WhatsApp System

This script helps configure the hybrid system where:
1. Railway hosts the main Flask application
2. Your local computer handles WhatsApp messaging using Selenium
"""

import os
import secrets
import sys
from pathlib import Path

def generate_secure_token():
    """Generate a secure random token for worker authentication"""
    return secrets.token_urlsafe(32)

def find_chrome_profile():
    """Attempt to find the default Chrome profile directory"""
    home = Path.home()
    
    # Common Chrome profile locations
    locations = [
        # Windows
        home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default",
        # Mac
        home / "Library" / "Application Support" / "Google" / "Chrome" / "Default",
        # Linux
        home / ".config" / "google-chrome" / "default",
        home / ".config" / "google-chrome" / "Default",
    ]
    
    for location in locations:
        if location.exists():
            return str(location)
    
    return None

def main():
    print("🚀 LMS Hybrid WhatsApp System Setup")
    print("=" * 50)
    
    # Check if .env already exists
    env_file = Path(".env")
    if env_file.exists():
        print("⚠️  .env file already exists!")
        overwrite = input("Do you want to overwrite it? (y/N): ").lower()
        if overwrite != 'y':
            print("Setup cancelled.")
            return
    
    print("\n📝 Let's configure your environment variables:")
    
    # Generate worker token
    worker_token = generate_secure_token()
    print(f"✅ Generated secure worker token: {worker_token[:20]}...")
    
    # Get Railway app URL
    base_url = input("\n🌐 Enter your Railway app URL (e.g., https://your-app.railway.app): ").strip()
    if not base_url:
        print("❌ Railway app URL is required!")
        return
    
    # Get Football Data API token
    football_token = input("\n⚽ Enter your Football-Data.org API token (optional): ").strip()
    
    # Find Chrome profile
    chrome_profile = find_chrome_profile()
    if chrome_profile:
        print(f"\n🔍 Found Chrome profile: {chrome_profile}")
        use_found = input("Use this Chrome profile? (Y/n): ").lower()
        if use_found == 'n':
            chrome_profile = input("Enter Chrome profile path: ").strip()
    else:
        print("\n🔍 Chrome profile not automatically detected.")
        chrome_profile = input("Enter Chrome profile path: ").strip()
    
    # Generate secret key
    secret_key = secrets.token_urlsafe(32)
    
    # Create .env file
    env_content = f"""# Flask Configuration
SECRET_KEY={secret_key}
DATABASE_URL=sqlite:///lms.db

# Football Data API
FOOTBALL_DATA_API_TOKEN={football_token}

# Worker Configuration (for local WhatsApp sender)
BASE_URL={base_url}
WORKER_API_TOKEN={worker_token}
CHROME_USER_DATA_DIR={chrome_profile}
"""
    
    with open(".env", "w") as f:
        f.write(env_content)
    
    print("\n✅ .env file created successfully!")
    
    print("\n📋 Next steps:")
    print("1. Deploy your app to Railway with the Flask configuration")
    print("2. Set the environment variables on Railway (except CHROME_USER_DATA_DIR)")
    print("3. Run the local worker: python lms_automation/sender_worker.py")
    print("4. The hybrid system will be ready!")
    
    print("\n💡 Tips:")
    print("- Keep your local worker running to process WhatsApp messages")
    print("- The worker will automatically poll Railway for new messages to send")
    print("- Check the admin dashboard for queue status monitoring")

if __name__ == "__main__":
    main()