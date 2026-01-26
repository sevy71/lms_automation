"""
LMS V2 - Football Data API Integration
"""
import os
import logging
from datetime import datetime
from typing import List, Dict, Optional
import requests

logger = logging.getLogger(__name__)


class FootballAPI:
    """Client for football-data.org API."""

    BASE_URL = 'https://api.football-data.org/v4'
    PL_COMPETITION_ID = 2021  # Premier League

    def __init__(self):
        # Support both variable names
        self.token = os.environ.get('FOOTBALL_DATA_API_TOKEN') or os.environ.get('FOOTBALL_API_TOKEN')

    @property
    def is_configured(self):
        return bool(self.token)

    def _request(self, endpoint: str) -> Optional[Dict]:
        """Make an API request."""
        if not self.is_configured:
            logger.warning("Football API not configured")
            return None

        try:
            response = requests.get(
                f"{self.BASE_URL}{endpoint}",
                headers={'X-Auth-Token': self.token},
                timeout=30
            )
            if response.ok:
                return response.json()
            else:
                logger.error(f"Football API error {response.status_code}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Football API request error: {e}")
            return None

    def get_matchday_fixtures(self, matchday: int, season: int = None) -> List[Dict]:
        """Get fixtures for a Premier League matchday."""
        if season is None:
            # Default to current season (e.g., 2024 for 2024/25 season)
            season = int(os.environ.get('PL_SEASON', datetime.now().year))
            # If we're past July, use current year, otherwise previous
            if datetime.now().month < 7:
                season -= 1

        endpoint = f"/competitions/{self.PL_COMPETITION_ID}/matches?matchday={matchday}&season={season}"
        data = self._request(endpoint)

        if not data or 'matches' not in data:
            return []

        fixtures = []
        for match in data['matches']:
            fixture = {
                'id': match['id'],
                'home_team': match['homeTeam']['name'],
                'away_team': match['awayTeam']['name'],
                'kickoff': self._parse_date(match.get('utcDate')),
                'status': self._map_status(match.get('status')),
                'home_score': match.get('score', {}).get('fullTime', {}).get('home'),
                'away_score': match.get('score', {}).get('fullTime', {}).get('away'),
            }
            fixtures.append(fixture)

        return fixtures

    def get_live_scores(self) -> List[Dict]:
        """Get currently live Premier League matches."""
        endpoint = f"/competitions/{self.PL_COMPETITION_ID}/matches?status=IN_PLAY"
        data = self._request(endpoint)

        if not data or 'matches' not in data:
            return []

        return [{
            'id': m['id'],
            'home_team': m['homeTeam']['name'],
            'away_team': m['awayTeam']['name'],
            'home_score': m.get('score', {}).get('fullTime', {}).get('home'),
            'away_score': m.get('score', {}).get('fullTime', {}).get('away'),
            'minute': m.get('minute'),
        } for m in data['matches']]

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse ISO date string."""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except Exception:
            return None

    def _map_status(self, api_status: str) -> str:
        """Map API status to our status."""
        status_map = {
            'SCHEDULED': 'scheduled',
            'TIMED': 'scheduled',
            'IN_PLAY': 'live',
            'PAUSED': 'live',
            'FINISHED': 'finished',
            'POSTPONED': 'postponed',
            'CANCELLED': 'postponed',
            'SUSPENDED': 'postponed',
        }
        return status_map.get(api_status, 'scheduled')


# Global instance
football_api = FootballAPI()
