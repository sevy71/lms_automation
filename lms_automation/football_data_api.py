# lms_automation/football_data_api.py
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

def get_upcoming_premier_league_fixtures(limit=20):
    token = os.getenv('FOOTBALL_DATA_API_TOKEN')
    if not token:
        print("Error: FOOTBALL_DATA_API_TOKEN environment variable not set.")
        return []

    try:
        # fetch a forward window and then take the next N by time
        from datetime import datetime, timedelta, timezone
        tz = ZoneInfo("Europe/London")

        today_utc = datetime.now(timezone.utc).date()
        date_from = today_utc.strftime("%Y-%m-%d")
        date_to = (today_utc + timedelta(days=60)).strftime("%Y-%m-%d")  # look ahead ~2 months

        url = (
            "https://api.football-data.org/v4/competitions/PL/matches"
            f"?dateFrom={date_from}&dateTo={date_to}"
        )

        r = requests.get(url, headers={'X-Auth-Token': token})
        r.raise_for_status()
        matches = r.json().get('matches', [])

        # keep only future/near-future match statuses
        allowed_status = {"SCHEDULED", "TIMED"}
        def parse_iso(dt_str: str) -> datetime:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))

        upcoming = [
            m for m in matches
            if m.get('status') in allowed_status
        ]

        # sort by kickoff (utcDate) ascending and take the first `limit`
        upcoming.sort(key=lambda m: parse_iso(m['utcDate']))
        upcoming = upcoming[:max(0, int(limit))]

        cleaned_fixtures = []
        for m in upcoming:
            dt_local = parse_iso(m['utcDate']).astimezone(tz)
            cleaned_fixtures.append({
                'event_id': m['id'],
                'date': dt_local.isoformat(),
                'home_team_name': m['homeTeam']['name'],
                'away_team_name': m['awayTeam']['name'],
                'matchday': m.get('matchday'),
                'status': m.get('status'),
            })
        return cleaned_fixtures

    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        return []
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

def get_premier_league_fixtures_by_season(season_year: int | None = None):
    token = os.getenv('FOOTBALL_DATA_API_TOKEN')
    if not token:
        print("Error: FOOTBALL_DATA_API_TOKEN environment variable not set.")
        return []

    try:
        r = requests.get(
            f"https://api.football-data.org/v4/competitions/PL/matches?season={season_year}",
            headers={'X-Auth-Token': token}
        )
        r.raise_for_status()
        matches = r.json().get('matches', [])

        cleaned_fixtures = []
        for m in matches:
            ft = (m.get('score') or {}).get('fullTime') or {}
            home_score = ft.get('home')
            away_score = ft.get('away')

            cleaned_fixtures.append({
                'fixture': {
                    'id': m['id'],
                    'date': m['utcDate'],
                    'status': {'short': m['status']}
                },
                'teams': {
                    'home': {'name': m['homeTeam']['name']},
                    'away': {'name': m['awayTeam']['name']}
                },
                'league': {
                    'round': f"Regular Season - {m.get('matchday')}"
                },
                'goals': {
                    'home': home_score,
                    'away': away_score
                }
            })
        return cleaned_fixtures

    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        return []
    except Exception as e:
        print(f"An error occurred: {e}")
        return []

def get_fixture_by_id(fixture_id: int):
    token = os.getenv('FOOTBALL_DATA_API_TOKEN')
    if not token:
        print("Error: FOOTBALL_DATA_API_TOKEN environment variable not set.")
        return None

    try:
        url = f"https://api.football-data.org/v4/matches/{fixture_id}"
        r = requests.get(url, headers={'X-Auth-Token': token})
        r.raise_for_status()
        return r.json().get('match')
    except requests.exceptions.RequestException as e:
        print(f"API request for fixture {fixture_id} failed: {e}")
        return None
    except Exception as e:
        print(f"An error occurred fetching fixture {fixture_id}: {e}")
        return None

def get_fixtures_by_ids(fixture_ids: list):
    results = {}
    for fid in fixture_ids:
        fixture_data = get_fixture_by_id(fid)
        if fixture_data:
            results[str(fid)] = fixture_data # Store by string ID for consistency
    return results
