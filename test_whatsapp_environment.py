#!/usr/bin/env python3
"""
Test script to validate WhatsApp sender environment and configuration
"""
import os
import sys
from dotenv import load_dotenv, find_dotenv

# Add the script's directory to the Python path to resolve local imports
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

def test_environment():
    """Test environment configuration"""
    print("🧪 Testing WhatsApp Sender Environment")
    print("=" * 50)
    
    # Load environment variables
    load_dotenv(find_dotenv())
    
    # Check required environment variables
    BASE_URL = os.environ.get("BASE_URL")
    WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
    CHROME_USER_DATA_DIR = os.environ.get("CHROME_USER_DATA_DIR")
    
    print("Environment Variables:")
    print(f"  BASE_URL: {'✓' if BASE_URL else '✗'} {BASE_URL}")
    print(f"  WORKER_API_TOKEN: {'✓' if WORKER_API_TOKEN else '✗'} {'***' if WORKER_API_TOKEN else 'Not set'}")
    print(f"  CHROME_USER_DATA_DIR: {'✓' if CHROME_USER_DATA_DIR else '✗'} {CHROME_USER_DATA_DIR}")
    
    # Check Chrome user data directory
    if CHROME_USER_DATA_DIR:
        if os.path.exists(CHROME_USER_DATA_DIR):
            print(f"  Chrome data dir exists: ✓")
        else:
            print(f"  Chrome data dir exists: ✗ (will be created)")
    
    print("\nPython Dependencies:")
    
    # Check required packages
    required_packages = [
        'selenium',
        'webdriver_manager',
        'requests',
        'python-dotenv'
    ]
    
    all_good = True
    for package in required_packages:
        try:
            if package == 'python-dotenv':
                import dotenv
                print(f"  {package}: ✓")
            elif package == 'webdriver_manager':
                import webdriver_manager
                print(f"  {package}: ✓")
            else:
                __import__(package)
                print(f"  {package}: ✓")
        except ImportError:
            print(f"  {package}: ✗")
            all_good = False
    
    # Test Chrome browser availability
    print("\nChrome Browser Test:")
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.chrome.service import Service
        
        chrome_options = Options()
        chrome_options.add_argument('--headless')  # Run in headless mode for test
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get("https://www.google.com")
        driver.quit()
        print("  Chrome browser: ✓")
    except Exception as e:
        print(f"  Chrome browser: ✗ ({str(e)[:50]}...)")
        all_good = False
    
    print("\n" + "=" * 50)
    if all_good and BASE_URL and WORKER_API_TOKEN:
        print("🎉 Environment test passed! Ready to send WhatsApp messages.")
        return True
    else:
        print("❌ Environment test failed. Please fix the issues above.")
        return False

if __name__ == "__main__":
    success = test_environment()
    sys.exit(0 if success else 1)