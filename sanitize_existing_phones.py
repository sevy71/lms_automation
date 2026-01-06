"""One-time script to sanitize all existing phone numbers in the database."""

import os
import sys

project_root = os.path.dirname(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from lms_automation.app import app, sanitize_phone_number
from lms_automation.extensions import db
from lms_automation.models import Player

def sanitize_all_phone_numbers():
    """Sanitize all existing phone numbers in the database."""
    with app.app_context():
        players = Player.query.filter(Player.whatsapp_number.isnot(None)).all()

        updated_count = 0
        for player in players:
            original = player.whatsapp_number
            sanitized = sanitize_phone_number(original)

            if original != sanitized:
                print(f"Updating {player.name}: '{original}' -> '{sanitized}'")
                player.whatsapp_number = sanitized
                updated_count += 1

        if updated_count > 0:
            db.session.commit()
            print(f"\n✓ Updated {updated_count} phone number(s)")
        else:
            print("✓ All phone numbers are already sanitized")

if __name__ == '__main__':
    print("Sanitizing existing phone numbers in database...")
    sanitize_all_phone_numbers()
