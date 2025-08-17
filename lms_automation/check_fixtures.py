from .app import app, db
from .models import Fixture
from datetime import datetime

with app.app_context():
    fixtures_2025 = Fixture.query.filter(db.extract('year', Fixture.date) == 2025).all()
    if fixtures_2025:
        print(f"Found {len(fixtures_2025)} fixtures for the 2025 season:")
        for fixture in fixtures_2025:
            print(f"- {fixture.home_team} vs {fixture.away_team} on {fixture.date}")
    else:
        print("No fixtures found for the 2025 season.")
