
import os
import time
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

class WhatsAppSender:
    def __init__(self, user_data_dir=None):
        """
        Initializes the WhatsAppSender.
        :param user_data_dir: Path to the Chrome user data directory for persistent sessions.
        """
        chrome_options = Options()
        
        # Add Chrome options for better compatibility
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        if user_data_dir:
            chrome_options.add_argument(f"user-data-dir={user_data_dir}")
            print(f"Using Chrome profile: {user_data_dir}")
        else:
            # If no user_data_dir is provided, Selenium will use a temporary profile.
            # This will require QR code scanning on each run.
            print("Warning: No user_data_dir provided. You will need to scan the QR code.")

        # Use webdriver-manager to handle chromedriver
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        except ImportError:
            raise ImportError("webdriver-manager is not installed. Please install it with 'pip install webdriver-manager'")

        print("🚀 Starting Chrome browser...")
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.wait = WebDriverWait(self.driver, 60) # Increased wait time
        
        # Initialize WhatsApp Web
        self._initialize_whatsapp()

    def _initialize_whatsapp(self):
        """Initialize WhatsApp Web and wait for login"""
        print("🌐 Loading WhatsApp Web...")
        try:
            self.driver.get("https://web.whatsapp.com")
            print("📱 Please scan QR code if not already logged in...")
            
            # Wait for WhatsApp to load (either QR code or chat interface)
            self.wait.until(
                lambda driver: "web.whatsapp.com" in driver.current_url
            )
            
            # Wait a bit more for the interface to fully load
            time.sleep(random.uniform(2, 5))
            print("✅ WhatsApp Web loaded successfully")
            
        except TimeoutException:
            print("❌ Failed to load WhatsApp Web")
            raise Exception("Could not load WhatsApp Web")
    
    def send_message(self, phone_number, message):
        """
        Sends a WhatsApp message to a given phone number.
        """
        if not phone_number.startswith('+'):
            raise ValueError("Phone number must include the country code and start with '+'.")

        try:
            print(f"📤 Sending message to {phone_number}...")
            
            # URL encode the message
            import urllib.parse
            encoded_message = urllib.parse.quote(message)
            
            # Clean phone number (remove + if present for URL)
            clean_number = phone_number.replace('+', '')
            url = f"https://web.whatsapp.com/send?phone={clean_number}&text={encoded_message}"
            
            print(f"🔗 Navigating to: {url[:50]}...")
            self.driver.get(url)

            # Wait for the chat to load and send button to appear
            print("⏳ Waiting for chat interface...")
            
            # More robust send button selectors
            send_button_selectors = [
                '//button[@aria-label="Send"]',
                '//span[@data-icon="send"]',
                '//div[contains(@class, "_4sWnG")]//button',
            ]
            
            send_button = None
            for selector in send_button_selectors:
                try:
                    send_button = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    print(f"✅ Found send button with selector: {selector}")
                    break
                except TimeoutException:
                    continue
            
            if not send_button:
                return False, "Could not find send button"
            
            # Add a random delay before clicking to appear more human-like
            delay_before = random.uniform(2.0, 4.5)
            print(f"⏰ Waiting {delay_before:.1f}s before clicking send...")
            time.sleep(delay_before)
            
            send_button.click()
            print("✅ Send button clicked")
            
            # Wait for the message to be sent with random delay
            delay_after = random.uniform(3, 6)
            print(f"⏰ Waiting {delay_after:.1f}s for message delivery...")
            time.sleep(delay_after)
            return True, "Message sent successfully."

        except TimeoutException:
            # Check if the error is due to an invalid number
            try:
                error_element = self.driver.find_element(By.XPATH, '//*[contains(text(), "Phone number shared via url is invalid.")]')
                return False, "Invalid phone number."
            except NoSuchElementException:
                return False, "Failed to send message: Timed out waiting for the send button. Is WhatsApp Web loaded and are you logged in?"
        except Exception as e:
            return False, f"An unexpected error occurred: {e}"

    def close(self):
        """
        Closes the browser.
        """
        self.driver.quit()


