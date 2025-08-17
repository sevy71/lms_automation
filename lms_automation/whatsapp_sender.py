
import os
import time
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
        if user_data_dir:
            chrome_options.add_argument(f"user-data-dir={user_data_dir}")
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

        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.driver, 20)

    def send_message(self, phone_number, message):
        """
        Sends a WhatsApp message to a given phone number.
        """
        if not phone_number.startswith('+'):
            raise ValueError("Phone number must include the country code and start with '+'.")

        try:
            # URL encode the message
            import urllib.parse
            encoded_message = urllib.parse.quote(message)
            
            url = f"https://web.whatsapp.com/send?phone={phone_number}&text={encoded_message}"
            self.driver.get(url)

            # Wait for the send button to be clickable
            send_button_xpath = '//button[@aria-label="Send"] | //button[@data-testid="send"] | //span[@data-icon="send"]'
            
            send_button = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, send_button_xpath))
            )
            
            time.sleep(2) # Small delay to ensure UI is ready
            send_button.click()
            
            # Wait a bit for the message to be sent before the next action
            time.sleep(5)
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


