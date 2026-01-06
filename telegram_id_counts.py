#!/usr/bin/env python3

from lms_automation.app import app
from lms_automation.models import Player


def main() -> None:
    with app.app_context():
        total = Player.query.count()
        with_telegram = Player.query.filter(
            Player.telegram_id.isnot(None),
            Player.telegram_id != ''
        ).count()
        without_telegram = total - with_telegram

    print("Telegram ID coverage:")
    print(f"- total players: {total}")
    print(f"- with telegram_id: {with_telegram}")
    print(f"- missing telegram_id: {without_telegram}")


if __name__ == "__main__":
    main()
