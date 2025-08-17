from lms_automation.app import app, db
from lms_automation.models import Round

with app.app_context():
    rounds = Round.query.all()
    if rounds:
        print(f"Found {len(rounds)} rounds:")
        for r in rounds:
            print(f"- Round {r.round_number}, Status: {r.status}")
    else:
        print("No rounds found in the database.")
