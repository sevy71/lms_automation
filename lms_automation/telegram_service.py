"""
Telegram Bot Integration Service
Handles sending messages and notifications via Telegram
"""

import os
import logging
import requests
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self):
        self.bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
        """
        Send a message to a Telegram chat

        Args:
            chat_id: Telegram chat ID (user ID)
            text: Message text to send
            parse_mode: 'HTML' or 'Markdown' for formatting

        Returns:
            bool: True if successful, False otherwise
        """
        if not self.bot_token:
            logger.error("TELEGRAM_BOT_TOKEN not configured")
            return False

        try:
            url = f"{self.api_base}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode
            }

            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                logger.info(f"Message sent to Telegram chat {chat_id}")
                return True
            else:
                logger.error(f"Failed to send Telegram message: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    def send_pick_reminder(self, chat_id: str, player_name: str, round_number: int,
                          pick_url: str, hours_until_deadline: int) -> bool:
        """
        Send a pick reminder to a player via Telegram

        Args:
            chat_id: Player's Telegram chat ID
            player_name: Player's name
            round_number: Current round number
            pick_url: URL to submit pick
            hours_until_deadline: Hours remaining until deadline

        Returns:
            bool: True if successful
        """
        if hours_until_deadline <= 2:
            emoji = "🚨"
            urgency = "FINAL REMINDER"
        elif hours_until_deadline <= 4:
            emoji = "⏰"
            urgency = "URGENT"
        else:
            emoji = "⚽"
            urgency = "Reminder"

        message = f"""
{emoji} <b>{urgency}: Round {round_number} Pick</b>

Hi {player_name}!

Round {round_number} picks close in <b>{hours_until_deadline} hours</b>.

<b>Submit your pick now:</b>
{pick_url}

Or use the command:
<code>/pick {pick_url.split('/')[-1]}</code>

Good luck! 🍀
"""
        return self.send_message(chat_id, message)

    def send_pick_confirmation(self, chat_id: str, player_name: str,
                              team_picked: str, round_number: int) -> bool:
        """
        Send pick confirmation to a player

        Args:
            chat_id: Player's Telegram chat ID
            player_name: Player's name
            team_picked: Team the player picked
            round_number: Round number

        Returns:
            bool: True if successful
        """
        message = f"""
✅ <b>Pick Confirmed!</b>

Hi {player_name}!

Your pick for Round {round_number} has been saved:
<b>{team_picked}</b>

Good luck! May your team win! 🏆
"""
        return self.send_message(chat_id, message)

    def send_elimination_notice(self, chat_id: str, player_name: str,
                                round_number: int, team_picked: str) -> bool:
        """
        Send elimination notice to a player

        Args:
            chat_id: Player's Telegram chat ID
            player_name: Player's name
            round_number: Round where eliminated
            team_picked: Team that lost

        Returns:
            bool: True if successful
        """
        base_url = os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')
        message = f"""
😔 <b>Elimination Notice</b>

Hi {player_name},

Unfortunately, {team_picked} did not win in Round {round_number}.

You have been eliminated from the Last Man Standing competition.

Thank you for playing! Better luck next time! 🍀

———

Want to run your own Last Man Standing competition?

It takes under 30 seconds to set up and invite your friends.

Start your league:
{base_url}/get-started
"""
        return self.send_message(chat_id, message)

    def send_winner_announcement(self, chat_id: str, winner_name: str) -> bool:
        """
        Send winner announcement to all players

        Args:
            chat_id: Player's Telegram chat ID
            winner_name: Name of the winner

        Returns:
            bool: True if successful
        """
        message = f"""
🏆 <b>GAME OVER - WE HAVE A WINNER!</b> 🏆

Congratulations to <b>{winner_name}</b> for winning the Last Man Standing competition!

All other players have been eliminated.

A new game will start soon. Stay tuned! 🎮
"""
        return self.send_message(chat_id, message)

    def send_rollover_announcement(self, chat_id: str, new_cycle: int) -> bool:
        """
        Send rollover announcement when all players are eliminated

        Args:
            chat_id: Player's Telegram chat ID
            new_cycle: New cycle number

        Returns:
            bool: True if successful
        """
        message = f"""
🔄 <b>ROLLOVER - ALL PLAYERS ELIMINATED!</b>

An extraordinary event has occurred - all players were eliminated in the same round!

<b>Good news:</b> Everyone is back in the game! 🎉

Starting fresh with <b>Cycle {new_cycle}</b>.
All teams are available for selection again.

The next round will begin soon. Good luck to all! 🍀
"""
        return self.send_message(chat_id, message)

    def send_cycle_complete_announcement(self, chat_id: str, new_cycle: int,
                                        survivors: List[str], is_survivor: bool) -> bool:
        """
        Send cycle completion announcement

        Args:
            chat_id: Player's Telegram chat ID
            new_cycle: New cycle number
            survivors: List of survivor names
            is_survivor: Whether this player is a survivor

        Returns:
            bool: True if successful
        """
        survivor_list = ", ".join(survivors)

        if is_survivor:
            status_msg = "🎊 <b>Congratulations! You survived Cycle {}</b>".format(new_cycle - 1)
        else:
            status_msg = "📊 <b>Cycle {} Complete</b>".format(new_cycle - 1)

        message = f"""
{status_msg}

Round 20 has been completed with <b>{len(survivors)} survivors</b>:
{survivor_list}

<b>Starting Cycle {new_cycle}</b>
{'✅ You advance to the next cycle!' if is_survivor else '❌ You did not survive this cycle.'}

All surviving players can now use all 20 teams again.
{'The competition continues!' if is_survivor else 'Better luck if you join the next game!'}
"""
        return self.send_message(chat_id, message)

    def send_auto_pick_notice(self, chat_id: str, player_name: str,
                             team_picked: str, round_number: int) -> bool:
        """
        Notify player that an auto-pick was made for them

        Args:
            chat_id: Player's Telegram chat ID
            player_name: Player's name
            team_picked: Auto-selected team
            round_number: Round number

        Returns:
            bool: True if successful
        """
        message = f"""
🤖 <b>Auto-Pick Applied</b>

Hi {player_name},

You missed the deadline for Round {round_number}.

An automatic pick has been made for you:
<b>{team_picked}</b>

Remember to submit your picks on time to choose your preferred team!

Set a reminder with: /reminders on
"""
        return self.send_message(chat_id, message)

    def send_round_results(self, chat_id: str, round_number: int,
                          results: List[Dict]) -> bool:
        """
        Send round results to a player

        Args:
            chat_id: Player's Telegram chat ID
            round_number: Round number
            results: List of match results

        Returns:
            bool: True if successful
        """
        results_text = "\n".join([
            f"• {r['home_team']} {r['home_score']} - {r['away_score']} {r['away_team']}"
            for r in results
        ])

        message = f"""
📊 <b>Round {round_number} Results</b>

{results_text}

Check your pick status with: /status
"""
        return self.send_message(chat_id, message)

    def broadcast_message(self, chat_ids: List[str], text: str) -> Dict[str, bool]:
        """
        Broadcast a message to multiple Telegram users

        Args:
            chat_ids: List of Telegram chat IDs
            text: Message to broadcast

        Returns:
            dict: Mapping of chat_id to success status
        """
        results = {}
        for chat_id in chat_ids:
            results[chat_id] = self.send_message(chat_id, text)
        return results


# Create global instance
telegram_service = TelegramService()