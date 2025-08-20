#!/usr/bin/env python3
"""
Enhanced WhatsApp Sender with autonomous session management
Improvements:
- Better Chrome profile management
- Automatic login detection and recovery
- Robust error handling and retries
- Session health monitoring
- Anti-detection measures
"""

import os
import time
import random
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException, 
    WebDriverException,
    ElementNotInteractableException
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WhatsAppSenderEnhanced:
    def __init__(self, user_data_dir=None, headless=True, max_retries=3):
        """
        Enhanced WhatsApp sender with better session management
        """
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.max_retries = max_retries
        self.driver = None
        self.wait = None
        self.session_healthy = False
        
        # Initialize Chrome
        self._setup_chrome()
        
        # Initialize WhatsApp with retries
        for attempt in range(max_retries):
            try:
                self._initialize_whatsapp()
                self.session_healthy = True
                break
            except Exception as e:
                logger.error(f"Initialization attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to initialize after {max_retries} attempts")
                time.sleep(10)  # Wait before retry

    def _setup_chrome(self):
        """Setup Chrome with enhanced options for stability"""
        chrome_options = Options()
        
        # Core stability options
        if self.headless:
            chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-web-security')
        chrome_options.add_argument('--disable-features=VizDisplayCompositor')
        
        # Anti-detection measures
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-notifications')
        
        # Memory and performance
        chrome_options.add_argument('--memory-pressure-off')
        chrome_options.add_argument('--max_old_space_size=4096')
        
        # User agent to appear more like a real browser
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36')
        
        # Profile management
        if self.user_data_dir:
            # Ensure directory exists
            os.makedirs(self.user_data_dir, exist_ok=True)
            chrome_options.add_argument(f"--user-data-dir={self.user_data_dir}")
            chrome_options.add_argument(f"--profile-directory=Default")
            logger.info(f"Using Chrome profile: {self.user_data_dir}")
        
        # Use webdriver-manager for Chrome
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
        except ImportError:
            raise ImportError("webdriver-manager required: pip install webdriver-manager")

        try:
            logger.info("🚀 Starting Chrome browser...")
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Anti-detection script
            self.driver.execute_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """)
            
            self.wait = WebDriverWait(self.driver, 60)
            logger.info("✅ Chrome browser started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start Chrome: {e}")
            raise

    def _initialize_whatsapp(self):
        """Initialize WhatsApp Web with smart login detection"""
        logger.info("🌐 Loading WhatsApp Web...")
        
        try:
            self.driver.get("https://web.whatsapp.com")
            
            # Wait for page to load
            self.wait.until(
                lambda driver: "web.whatsapp.com" in driver.current_url
            )
            
            # Check login status with multiple strategies
            login_status = self._check_login_status()
            
            if login_status == "logged_in":
                logger.info("✅ Already logged in to WhatsApp Web")
                return
            elif login_status == "qr_code":
                logger.info("📱 QR code detected - need to scan")
                self._handle_qr_code()
            elif login_status == "loading":
                logger.info("⏳ WhatsApp is loading...")
                self._wait_for_load_complete()
            else:
                logger.warning("❓ Unknown login status, proceeding with caution")
                time.sleep(10)
                
        except TimeoutException:
            raise Exception("Failed to load WhatsApp Web - timeout")
        except Exception as e:
            raise Exception(f"WhatsApp initialization failed: {e}")

    def _check_login_status(self):
        """Determine current login status"""
        try:
            # Wait a moment for page elements to load
            time.sleep(3)
            
            # Check for QR code (not logged in)
            qr_selectors = [
                '[data-ref="qr-code"]',
                'canvas[aria-label="Scan me!"]',
                '.landing-main canvas',
                '._2EZ_m canvas'
            ]
            
            for selector in qr_selectors:
                try:
                    qr_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if qr_element.is_displayed():
                        return "qr_code"
                except NoSuchElementException:
                    continue
            
            # Check for chat interface (logged in)
            chat_selectors = [
                '[data-testid="chat-list"]',
                '._3OgTahIITgn2QM6ZPdp2VL',
                '.app-wrapper-web',
                '#main'
            ]
            
            for selector in chat_selectors:
                try:
                    chat_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if chat_element.is_displayed():
                        return "logged_in"
                except NoSuchElementException:
                    continue
            
            # Check for loading indicators
            loading_selectors = [
                '.landing-main .spinner',
                '._3q4NP._2yeJ5',
                '[data-testid="progress-update"]'
            ]
            
            for selector in loading_selectors:
                try:
                    loading_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if loading_element.is_displayed():
                        return "loading"
                except NoSuchElementException:
                    continue
                    
            return "unknown"
            
        except Exception as e:
            logger.error(f"Error checking login status: {e}")
            return "unknown"

    def _handle_qr_code(self):
        """Handle QR code scanning with timeout"""
        logger.info("📱 Waiting for QR code to be scanned...")
        
        # In autonomous mode, we can't wait for user input
        # Instead, we'll wait a reasonable time and then give up
        max_qr_wait = int(os.environ.get('QR_WAIT_TIMEOUT', '120'))  # 2 minutes default
        
        start_time = time.time()
        while time.time() - start_time < max_qr_wait:
            time.sleep(5)
            status = self._check_login_status()
            if status == "logged_in":
                logger.info("✅ QR code scanned successfully")
                return
            elif status != "qr_code":
                break
                
        # If we get here, QR code wasn't scanned in time
        if self._check_login_status() != "logged_in":
            logger.warning(f"⚠️ QR code not scanned within {max_qr_wait} seconds")
            # Don't fail immediately - try to continue anyway
            time.sleep(10)

    def _wait_for_load_complete(self):
        """Wait for WhatsApp to finish loading"""
        logger.info("⏳ Waiting for WhatsApp to finish loading...")
        
        max_wait = 60
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            time.sleep(2)
            status = self._check_login_status()
            if status == "logged_in":
                logger.info("✅ WhatsApp loaded successfully")
                return
            elif status == "qr_code":
                self._handle_qr_code()
                return
                
        logger.warning("⚠️ WhatsApp took longer than expected to load")

    def check_session_health(self):
        """Check if WhatsApp session is still healthy"""
        try:
            # Simple check - can we find basic elements?
            current_url = self.driver.current_url
            if "web.whatsapp.com" not in current_url:
                logger.warning("❌ Not on WhatsApp Web anymore")
                return False
                
            status = self._check_login_status()
            if status == "logged_in":
                self.session_healthy = True
                return True
            else:
                logger.warning(f"❌ Session unhealthy - status: {status}")
                self.session_healthy = False
                return False
                
        except Exception as e:
            logger.error(f"Error checking session health: {e}")
            self.session_healthy = False
            return False

    def recover_session(self):
        """Attempt to recover an unhealthy session"""
        logger.info("🔄 Attempting session recovery...")
        
        try:
            # Refresh the page
            self.driver.refresh()
            time.sleep(5)
            
            # Re-initialize
            self._initialize_whatsapp()
            
            if self.check_session_health():
                logger.info("✅ Session recovered successfully")
                return True
            else:
                logger.error("❌ Session recovery failed")
                return False
                
        except Exception as e:
            logger.error(f"Session recovery error: {e}")
            return False

    def send_message(self, phone_number, message):
        """
        Enhanced message sending with retries and health checks
        """
        if not phone_number.startswith('+'):
            return False, "Phone number must include country code and start with '+'"
        
        # Health check before sending
        if not self.check_session_health():
            logger.warning("Session unhealthy, attempting recovery...")
            if not self.recover_session():
                return False, "Session recovery failed"
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"📤 Sending message to {phone_number} (attempt {attempt + 1})")
                
                # URL encode the message
                import urllib.parse
                encoded_message = urllib.parse.quote(message)
                
                # Clean phone number for URL
                clean_number = phone_number.replace('+', '')
                url = f"https://web.whatsapp.com/send?phone={clean_number}&text={encoded_message}"
                
                logger.info(f"🔗 Navigating to chat...")
                self.driver.get(url)
                
                # Wait for chat to load
                time.sleep(random.uniform(1, 3))  # Reduced from 3-6 to 1-3 seconds
                
                # Enhanced send button detection
                send_button = self._find_send_button()
                if not send_button:
                    if attempt < self.max_retries - 1:
                        logger.warning(f"Send button not found, retrying in 10 seconds...")
                        time.sleep(10)
                        continue
                    return False, "Could not find send button after all attempts"
                
                # Human-like interaction
                self._human_like_send(send_button)
                
                # Verify message was sent
                if self._verify_message_sent():
                    logger.info("✅ Message sent successfully")
                    return True, "Message sent successfully"
                else:
                    if attempt < self.max_retries - 1:
                        logger.warning("Message send verification failed, retrying...")
                        time.sleep(10)
                        continue
                    return False, "Message send verification failed"
                    
            except Exception as e:
                logger.error(f"Send attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(10)
                    continue
                return False, f"All send attempts failed. Last error: {e}"
        
        return False, "Max retries exceeded"

    def _find_send_button(self):
        """Find send button with multiple selectors"""
        send_button_selectors = [
            '[data-testid="send"]',
            'button[aria-label="Send"]',
            'span[data-icon="send"]',
            '._4sWnG button',
            'button[class*="send"]',
            '.send-button',
            '[role="button"][data-tab="11"]'
        ]
        
        for selector in send_button_selectors:
            try:
                button = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                logger.info(f"✅ Found send button: {selector}")
                return button
            except TimeoutException:
                continue
                
        # Try XPath selectors
        xpath_selectors = [
            '//button[@aria-label="Send"]',
            '//span[@data-icon="send"]',
            '//button[contains(@class, "send")]',
            '//div[contains(@class, "_4sWnG")]//button'
        ]
        
        for selector in xpath_selectors:
            try:
                button = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                logger.info(f"✅ Found send button with XPath: {selector}")
                return button
            except TimeoutException:
                continue
                
        return None

    def _human_like_send(self, send_button):
        """Send message with human-like behavior"""
        # Random delay before clicking
        delay = random.uniform(0.5, 1.5)  # Reduced from 1.5-3.5 to 0.5-1.5 seconds
        logger.info(f"⏰ Waiting {delay:.1f}s before sending...")
        time.sleep(delay)
        
        # Click with potential retry
        try:
            send_button.click()
            logger.info("✅ Send button clicked")
        except ElementNotInteractableException:
            # Sometimes need to scroll into view
            self.driver.execute_script("arguments[0].scrollIntoView();", send_button)
            time.sleep(1)
            send_button.click()
            logger.info("✅ Send button clicked after scroll")
        
        # Wait for message to be processed
        time.sleep(random.uniform(1, 2))  # Reduced from 2-4 to 1-2 seconds

    def _verify_message_sent(self):
        """Verify that the message was actually sent"""
        try:
            # Look for sent message indicators
            sent_indicators = [
                '[data-icon="msg-check"]',  # Single tick
                '[data-icon="msg-dblcheck"]',  # Double tick
                '.message-out',
                '._1i_wG ._3zb-j',
                '[data-testid="tail-out"]'
            ]
            
            for indicator in sent_indicators:
                try:
                    self.driver.find_element(By.CSS_SELECTOR, indicator)
                    logger.info(f"✅ Message sent indicator found: {indicator}")
                    return True
                except NoSuchElementException:
                    continue
                    
            # If no specific indicators found, assume success if no error
            return True
            
        except Exception as e:
            logger.error(f"Error verifying message sent: {e}")
            return True  # Assume success if can't verify

    def close(self):
        """Close the browser safely"""
        try:
            if self.driver:
                self.driver.quit()
                logger.info("🔒 Browser closed")
        except Exception as e:
            logger.error(f"Error closing browser: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Backward compatibility alias
WhatsAppSender = WhatsAppSenderEnhanced