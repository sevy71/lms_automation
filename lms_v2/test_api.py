#!/usr/bin/env python3
"""
Test script for Football Data API
Run: python -m lms_v2.test_api
"""
import os
from dotenv import load_dotenv

# Load .env.local
load_dotenv('.env.local')

# Check token
token = os.environ.get('FOOTBALL_DATA_API_TOKEN') or os.environ.get('FOOTBALL_API_TOKEN')
print(f"Token found: {'Yes' if token else 'No'}")

if not token:
    print("ERROR: No FOOTBALL_DATA_API_TOKEN found in environment")
    print("Set it in .env.local or environment variables")
    exit(1)

# Set it for the API client
os.environ['FOOTBALL_DATA_API_TOKEN'] = token

from lms_v2.football_api import FootballAPI

api = FootballAPI()
print(f"API configured: {api.is_configured}")

# Test getting fixtures for a matchday
print("\n--- Testing Matchday 23 Fixtures ---")
fixtures = api.get_matchday_fixtures(23)

if not fixtures:
    print("No fixtures returned - API may be having issues or matchday invalid")
else:
    print(f"Found {len(fixtures)} fixtures:\n")
    for f in fixtures:
        score = ""
        if f['home_score'] is not None:
            score = f" ({f['home_score']}-{f['away_score']})"
        kickoff = f['kickoff'].strftime('%Y-%m-%d %H:%M') if f['kickoff'] else 'TBD'
        print(f"  {f['home_team']} vs {f['away_team']}{score}")
        print(f"    Kickoff: {kickoff} | Status: {f['status']}")
        print()
