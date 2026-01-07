#!/usr/bin/env python3

import os
import sys


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from lms_automation.app import app
from lms_automation.extensions import db
from lms_automation.models import Player


def main() -> None:
    seed_count = int(os.environ.get("SEED_COUNT", "30"))
    created = 0

    with app.app_context():
        tony = Player.query.filter_by(name="Tony").first()
        if not tony:
            tony = Player(
                name="Tony",
                whatsapp_number="07545851594",
                telegram_id=None
            )
            db.session.add(tony)
            created += 1

        for i in range(1, seed_count + 1):
            name = f"Player {i}"
            player = Player.query.filter_by(name=name).first()
            if player:
                continue
            player = Player(name=name)
            db.session.add(player)
            created += 1

        db.session.commit()

    print(f"Seed complete. Created {created} players (Tony + Player 1..{seed_count}).")


if __name__ == "__main__":
    main()
