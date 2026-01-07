#!/usr/bin/env python3

import os
import sys


project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from lms_automation.app import app
from lms_automation.extensions import db
from lms_automation.football_api import FootballDataAPI
from lms_automation.models import Fixture, Round


def _rounds_to_sync(matchday: int | None, statuses: list[str]) -> list[Round]:
    if matchday is not None:
        return Round.query.filter_by(pl_matchday=matchday).all()
    if statuses:
        return Round.query.filter(Round.status.in_(statuses)).all()
    return Round.query.all()


def main() -> None:
    season = os.environ.get("FOOTBALL_SEASON") or os.environ.get("SEASON")
    matchday_env = os.environ.get("MATCHDAY")
    matchday = int(matchday_env) if matchday_env else None
    status_env = os.environ.get("ROUND_STATUSES", "pending,active")
    statuses = [s.strip() for s in status_env.split(",") if s.strip()]

    created = 0
    updated = 0
    skipped_rounds = 0

    api = FootballDataAPI()

    with app.app_context():
        rounds = _rounds_to_sync(matchday, statuses)
        if not rounds:
            print("No rounds found to sync fixtures.")
            return

        fixtures_data = api.get_premier_league_fixtures(matchday=matchday, season=season)

        for round_obj in rounds:
            if not round_obj.pl_matchday:
                skipped_rounds += 1
                print(f"Skipping round {round_obj.id} (no pl_matchday)")
                continue

            formatted = api.format_fixtures_for_db(fixtures_data, round_obj.pl_matchday)
            if not formatted:
                print(f"No fixtures returned for matchday {round_obj.pl_matchday}")
                continue

            for fx in formatted:
                event_id = fx.get("event_id") or None
                existing = None

                if event_id:
                    existing = Fixture.query.filter_by(
                        round_id=round_obj.id,
                        event_id=event_id
                    ).first()
                if not existing:
                    existing = Fixture.query.filter_by(
                        round_id=round_obj.id,
                        home_team=fx["home_team"],
                        away_team=fx["away_team"],
                        date=fx["date"]
                    ).first()

                if existing:
                    existing.event_id = event_id or existing.event_id
                    existing.home_team = fx["home_team"]
                    existing.away_team = fx["away_team"]
                    existing.date = fx["date"]
                    existing.time = fx["time"]
                    existing.home_score = fx["home_score"]
                    existing.away_score = fx["away_score"]
                    existing.status = fx["status"]
                    updated += 1
                else:
                    fixture = Fixture(
                        round_id=round_obj.id,
                        event_id=event_id,
                        home_team=fx["home_team"],
                        away_team=fx["away_team"],
                        date=fx["date"],
                        time=fx["time"],
                        home_score=fx["home_score"],
                        away_score=fx["away_score"],
                        status=fx["status"]
                    )
                    db.session.add(fixture)
                    created += 1

        db.session.commit()

    print(
        "Fixture sync complete. "
        f"Created={created}, Updated={updated}, Skipped rounds={skipped_rounds}"
    )


if __name__ == "__main__":
    main()
