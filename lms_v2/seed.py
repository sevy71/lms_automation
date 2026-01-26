"""
LMS V2 - Seed Data
Creates 20 test players for initial setup
"""
from lms_v2.models import db, Player


TEST_PLAYERS = [
    "Alex Thompson",
    "Ben Wilson",
    "Charlie Davies",
    "Dan Roberts",
    "Eddie Murphy",
    "Frank Miller",
    "George Harris",
    "Harry Clark",
    "Ian Lewis",
    "Jack Walker",
    "Kevin Brown",
    "Leo Martinez",
    "Mike Johnson",
    "Nick Taylor",
    "Oscar White",
    "Paul Anderson",
    "Quinn Thomas",
    "Ryan Jackson",
    "Sam Williams",
    "Tom Evans",
]


def seed_players():
    """Create test players if they don't exist."""
    created = 0
    for name in TEST_PLAYERS:
        existing = Player.query.filter_by(name=name).first()
        if not existing:
            player = Player(name=name)
            db.session.add(player)
            created += 1

    if created > 0:
        db.session.commit()
        print(f"Created {created} test players")
    else:
        print("All test players already exist")

    return created


def reset_game():
    """Reset all players to active status (for new cycle)."""
    Player.query.update({Player.status: 'active'})
    db.session.commit()
    print("All players reset to active")


if __name__ == '__main__':
    from lms_v2.app import app
    with app.app_context():
        seed_players()
