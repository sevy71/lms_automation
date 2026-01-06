#!/usr/bin/env python3
"""Manual trigger for round announcements - for testing"""

import os
import sys

# Set up path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import after path is set
from lms_automation.app import app
from lms_automation.models import db, Round, Player, PickToken, ReminderSchedule
from datetime import datetime, timedelta
import requests

def send_telegram_message(telegram_id, message):
    """Send message via Telegram bot API"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN', '7358039234:AAGJ_8H1IOvQKS1Qe2-zYc6Z5EeYrW50c0g')

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        response = requests.post(url, json={
            'chat_id': telegram_id,
            'text': message,
            'parse_mode': 'HTML'
        })
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False

def main():
    with app.app_context():
        print("Checking for active rounds...")

        # Get active rounds
        active_rounds = Round.query.filter_by(status='active').all()
        print(f"Found {len(active_rounds)} active round(s)")

        for round_obj in active_rounds:
            print(f"\nProcessing Round {round_obj.round_number}...")
            active_players = Player.query.filter_by(status='active').all()
            print(f"Found {len(active_players)} active players")

            sent_count = 0
            for player in active_players:
                # Get pick token
                pick_token = PickToken.query.filter_by(
                    player_id=player.id,
                    round_id=round_obj.id
                ).first()

                if not pick_token:
                    print(f"  No token for {player.name}, skipping...")
                    continue

                # Check if already announced (has reminders scheduled)
                existing_reminder = ReminderSchedule.query.filter_by(
                    player_id=player.id,
                    round_id=round_obj.id
                ).first()

                if existing_reminder:
                    print(f"  Already announced to {player.name}, skipping...")
                    continue

                # Send announcement
                if hasattr(player, 'telegram_id') and player.telegram_id:
                    pick_url = f"{os.environ.get('BASE_URL', 'http://localhost:5001')}/pick/{pick_token.token}"

                    if round_obj.first_kickoff_at:
                        deadline_str = round_obj.first_kickoff_at.strftime('%A %d %B at %H:%M')
                    else:
                        deadline_str = "soon"

                    message = f"⚽ <b>NEW ROUND {round_obj.round_number} IS LIVE!</b>\n\n"
                    message += f"Make your pick before {deadline_str}\n\n"
                    message += f"Click here to make your pick:\n{pick_url}"

                    sent = send_telegram_message(player.telegram_id, message)
                    if sent:
                        print(f"  ✓ Sent announcement to {player.name} (ID: {player.telegram_id})")
                        sent_count += 1

                        # Schedule reminders
                        if round_obj.first_kickoff_at:
                            reminder_4h = ReminderSchedule(
                                player_id=player.id,
                                round_id=round_obj.id,
                                reminder_type='4_hour',
                                scheduled_time=round_obj.first_kickoff_at - timedelta(hours=4),
                                is_sent=False
                            )
                            db.session.add(reminder_4h)

                            reminder_2h = ReminderSchedule(
                                player_id=player.id,
                                round_id=round_obj.id,
                                reminder_type='2_hour',
                                scheduled_time=round_obj.first_kickoff_at - timedelta(hours=2),
                                is_sent=False
                            )
                            db.session.add(reminder_2h)
                    else:
                        print(f"  ✗ Failed to send to {player.name}")

            db.session.commit()
            print(f"\n✓ Sent {sent_count} announcements for Round {round_obj.round_number}")

if __name__ == '__main__':
    main()
