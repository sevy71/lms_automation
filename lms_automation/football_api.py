import logging
import os
import time
import requests
from datetime import datetime
from typing import Dict, List, Optional
from lms_automation.team_utils import normalize_team_name

logger = logging.getLogger(__name__)

class FootballDataAPI:
    def __init__(self):
        self.api_token = os.environ.get('FOOTBALL_API_TOKEN')
        if not self.api_token:
            raise RuntimeError("FOOTBALL_API_TOKEN is required to use the Football API.")
        self.base_url = 'https://api.football-data.org/v4'
        self.headers = {
            'X-Auth-Token': self.api_token,
            'Content-Type': 'application/json'
        }
        # Premier League competition ID
        self.premier_league_id = 2021

    @staticmethod
    def _default_season() -> Optional[str]:
        return os.environ.get('FOOTBALL_SEASON') or os.environ.get('SEASON')

    def get_premier_league_fixtures(self, matchday: Optional[int] = None, season: str = None) -> Dict:
        """
        Fetch Premier League fixtures for the current season
        
        Args:
            matchday: Specific matchday to fetch (1-38), if None fetches all
            season: Season year to fetch (defaults to current season)
            
        Returns:
            Dictionary containing fixtures data
        """
        try:
            url = f"{self.base_url}/competitions/{self.premier_league_id}/matches"
            
            # Use current season (2025/26) if no season specified  
            if season is None:
                season = self._default_season()

            params = {}
            if season:
                params['season'] = season
            
            if matchday:
                params['matchday'] = matchday
            
            logger.info("Football API request: %s params=%s", url, params)

            # Add a small delay to avoid rate limiting
            time.sleep(0.1)
            
            response = requests.get(url, headers=self.headers, params=params, timeout=10)

            logger.info("Football API response status: %s", response.status_code)

            if response.status_code == 429:
                logger.warning("Football API rate limit hit (429).")
                return {'matches': []}
            
            if response.status_code == 403:
                logger.error("Football API error 403: check token/subscription.")
                return {'matches': []}
            elif response.status_code == 404:
                logger.error("Football API error 404: season or competition not found.")
                return {'matches': []}  
            elif response.status_code != 200:
                logger.error("Football API error %s: %s", response.status_code, response.text[:200])
                return {'matches': []}
            
            data = response.json()
            logger.info("Football API response: %s matches", len(data.get('matches', [])))
            
            return data
            
        except requests.RequestException as e:
            logger.error("Error fetching fixtures: %s", e)
            return {'matches': []}

    def get_available_matchdays(self) -> List[int]:
        """
        Get list of available matchdays for the current season
        
        Returns:
            List of matchday numbers
        """
        try:
            fixtures_data = self.get_premier_league_fixtures()
            matchdays = set()
            
            for match in fixtures_data.get('matches', []):
                if match.get('matchday'):
                    matchdays.add(match['matchday'])
            
            return sorted(list(matchdays))
            
        except Exception as e:
            logger.error("Error getting matchdays: %s", e)
            return list(range(1, 39))  # Default to 1-38

    def format_fixtures_for_db(self, fixtures_data: Dict, target_matchday: int) -> List[Dict]:
        """
        Format fixtures data for database insertion
        
        Args:
            fixtures_data: Raw API response
            target_matchday: The matchday to filter for
            
        Returns:
            List of formatted fixture dictionaries
        """
        formatted_fixtures = []
        
        for match in fixtures_data.get('matches', []):
            if match.get('matchday') != target_matchday:
                continue
                
            # Parse match date
            match_date = None
            match_time = None
            if match.get('utcDate'):
                try:
                    dt = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
                    match_date = dt.date()
                    match_time = dt.time()
                except ValueError:
                    pass
            
            # Extract team names and normalize to canonical form
            # This ensures "AFC Bournemouth" -> "Bournemouth", etc.
            home_team_raw = match.get('homeTeam', {}).get('name', 'TBD')
            away_team_raw = match.get('awayTeam', {}).get('name', 'TBD')
            home_team = normalize_team_name(home_team_raw)
            away_team = normalize_team_name(away_team_raw)
            
            # Extract scores if available
            score = match.get('score', {})
            full_time = score.get('fullTime', {})
            home_score = full_time.get('home')
            away_score = full_time.get('away')
            
            # Determine match status
            status_map = {
                'SCHEDULED': 'scheduled',
                'LIVE': 'live', 
                'IN_PLAY': 'live',
                'PAUSED': 'live',
                'FINISHED': 'completed',
                'POSTPONED': 'postponed',
                'SUSPENDED': 'postponed',
                'CANCELLED': 'postponed'
            }
            match_status = status_map.get(match.get('status'), 'scheduled')
            
            formatted_fixture = {
                'event_id': str(match.get('id')) if match.get('id') is not None else None,
                'home_team': home_team,
                'away_team': away_team,
                'date': match_date,
                'time': match_time,
                'home_score': home_score,
                'away_score': away_score,
                'status': match_status,
                'pl_matchday': target_matchday
            }
            
            formatted_fixtures.append(formatted_fixture)
        
        return formatted_fixtures

    def get_matchday_info(self, matchday: int) -> Dict:
        """
        Get information about a specific matchday
        
        Args:
            matchday: The matchday number (1-38)
            
        Returns:
            Dictionary with matchday information
        """
        try:
            fixtures_data = self.get_premier_league_fixtures(matchday)
            matches = fixtures_data.get('matches', [])
            
            if not matches:
                return {
                    'matchday': matchday,
                    'fixture_count': 0,
                    'earliest_date': None,
                    'latest_date': None
                }
            
            dates = []
            for match in matches:
                if match.get('utcDate'):
                    try:
                        dt = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
                        dates.append(dt.date())
                    except ValueError:
                        pass
            
            return {
                'matchday': matchday,
                'fixture_count': len(matches),
                'earliest_date': min(dates) if dates else None,
                'latest_date': max(dates) if dates else None
            }
            
        except Exception as e:
            logger.error("Error getting matchday info: %s", e)
            return {
                'matchday': matchday,
                'fixture_count': 0,
                'earliest_date': None,
                'latest_date': None
            }
