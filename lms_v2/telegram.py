"""
LMS V2 - Telegram Integration
Simple Telegram bot for sending notifications
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)


class TelegramBot:
    """Simple Telegram bot for sending messages."""

    def __init__(self):
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN')
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else None

    @property
    def is_configured(self):
        return bool(self.token)

    def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
        """Send a message to a Telegram chat."""
        if not self.is_configured:
            logger.warning("Telegram not configured, skipping message")
            return False

        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    'chat_id': chat_id,
                    'text': text,
                    'parse_mode': parse_mode
                },
                timeout=10
            )
            if response.ok:
                logger.info(f"Telegram message sent to {chat_id}")
                return True
            else:
                logger.error(f"Telegram API error: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    def send_pick_link(self, chat_id: str, player_name: str, round_number: int, pick_url: str) -> bool:
        """Send a pick link to a player."""
        message = (
            f"⚽ <b>Round {round_number} is open!</b>\n\n"
            f"Hi {player_name}, time to make your pick.\n\n"
            f"🔗 <a href='{pick_url}'>Click here to submit your pick</a>\n\n"
            f"Good luck!"
        )
        return self.send_message(chat_id, message)

    def send_reminder(self, chat_id: str, player_name: str, round_number: int, pick_url: str, hours_left: int) -> bool:
        """Send a reminder to a player."""
        message = (
            f"⏰ <b>Reminder: {hours_left}h until deadline!</b>\n\n"
            f"Hi {player_name}, you haven't submitted your pick for Round {round_number} yet.\n\n"
            f"🔗 <a href='{pick_url}'>Submit your pick now</a>"
        )
        return self.send_message(chat_id, message)

    def send_elimination(self, chat_id: str, player_name: str, team: str, round_number: int) -> bool:
        """Notify a player of elimination."""
        message = (
            f"😔 <b>Eliminated</b>\n\n"
            f"Sorry {player_name}, your pick ({team}) didn't win in Round {round_number}.\n\n"
            f"Better luck next cycle!"
        )
        return self.send_message(chat_id, message)

    def send_survival(self, chat_id: str, player_name: str, team: str, round_number: int) -> bool:
        """Notify a player of survival."""
        message = (
            f"✅ <b>You survived Round {round_number}!</b>\n\n"
            f"Nice pick {player_name}! {team} won.\n\n"
            f"Stay tuned for the next round."
        )
        return self.send_message(chat_id, message)

    def send_winner(self, chat_id: str, player_name: str) -> bool:
        """Notify the cycle winner."""
        message = (
            f"🏆🏆🏆 <b>CONGRATULATIONS!</b> 🏆🏆🏆\n\n"
            f"{player_name}, you are the Last Man Standing!\n\n"
            f"You've won this cycle. Amazing job!"
        )
        return self.send_message(chat_id, message)


# Global instance
telegram = TelegramBot()
