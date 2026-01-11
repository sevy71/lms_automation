"""
Team name normalization utilities for LMS automation.

This module provides a SINGLE SOURCE OF TRUTH for team name handling:
1. normalize_team_name() - Converts any team name variant to a canonical form
2. CANONICAL_TEAMS - The canonical name for each team (alphabetically sorted)
3. display_name() - Converts canonical names to short display names

IMPORTANT: Canonical names are designed for:
- Consistent comparison (equality checks)
- Alphabetical ordering (for auto-pick fallback)
- Storage consistency

Usage:
    from lms_automation.team_utils import normalize_team_name, display_name, teams_match

    # Normalizing for storage/comparison
    canonical = normalize_team_name("Aston Villa FC")  # Returns "Aston Villa"
    canonical = normalize_team_name("aston villa")     # Returns "Aston Villa"
    canonical = normalize_team_name("Villa")           # Returns "Aston Villa"

    # Checking if two team names refer to the same team
    if teams_match("Aston Villa FC", "Villa"):  # Returns True

    # Getting display name
    short = display_name("Aston Villa")  # Returns "Villa"

    # Alphabetical ordering for auto-pick fallback
    sorted_teams = sorted(CANONICAL_TEAMS)
    # Result: Arsenal, Aston Villa, Bournemouth, Brentford, ...
"""

import logging

logger = logging.getLogger(__name__)

# Canonical team names - these are the "official" names we store
# IMPORTANT: These are the 20 current Premier League teams (2024-25 season)
# Use short, consistent names that sort alphabetically as expected
# "Bournemouth" NOT "AFC Bournemouth" - so B comes after A, not after Z
CANONICAL_TEAMS = {
    'Arsenal',
    'Aston Villa',
    'Bournemouth',      # Changed from "AFC Bournemouth" for correct alphabetical sort
    'Brentford',
    'Brighton',
    'Chelsea',
    'Crystal Palace',
    'Everton',
    'Fulham',
    'Ipswich Town',
    'Leicester City',
    'Liverpool',
    'Manchester City',
    'Manchester United',
    'Newcastle United',
    'Nottingham Forest',
    'Southampton',
    'Tottenham',
    'West Ham',
    'Wolverhampton',
}

# Map from any known variation to canonical name
# Key: lowercase variant, Value: canonical name
_TEAM_ALIASES = {
    # Arsenal
    'arsenal': 'Arsenal',
    'arsenal fc': 'Arsenal',

    # Aston Villa
    'aston villa': 'Aston Villa',
    'aston villa fc': 'Aston Villa',
    'villa': 'Aston Villa',

    # Bournemouth
    'afc bournemouth': 'Bournemouth',
    'bournemouth': 'Bournemouth',
    'bournemouth fc': 'Bournemouth',
    'bournemouth afc': 'Bournemouth',

    # Brentford
    'brentford': 'Brentford',
    'brentford fc': 'Brentford',

    # Brighton
    'brighton': 'Brighton',
    'brighton fc': 'Brighton',
    'brighton & hove albion': 'Brighton',
    'brighton and hove albion': 'Brighton',
    'brighton & hove albion fc': 'Brighton',
    'brighton and hove albion fc': 'Brighton',
    'brighton hove albion': 'Brighton',

    # Chelsea
    'chelsea': 'Chelsea',
    'chelsea fc': 'Chelsea',

    # Crystal Palace
    'crystal palace': 'Crystal Palace',
    'crystal palace fc': 'Crystal Palace',
    'palace': 'Crystal Palace',

    # Everton
    'everton': 'Everton',
    'everton fc': 'Everton',

    # Fulham
    'fulham': 'Fulham',
    'fulham fc': 'Fulham',

    # Ipswich
    'ipswich': 'Ipswich Town',
    'ipswich town': 'Ipswich Town',
    'ipswich town fc': 'Ipswich Town',

    # Leicester
    'leicester': 'Leicester City',
    'leicester city': 'Leicester City',
    'leicester city fc': 'Leicester City',

    # Liverpool
    'liverpool': 'Liverpool',
    'liverpool fc': 'Liverpool',

    # Man City
    'manchester city': 'Manchester City',
    'manchester city fc': 'Manchester City',
    'man city': 'Manchester City',
    'man city fc': 'Manchester City',

    # Man United
    'manchester united': 'Manchester United',
    'manchester united fc': 'Manchester United',
    'man united': 'Manchester United',
    'man utd': 'Manchester United',
    'man utd fc': 'Manchester United',

    # Newcastle
    'newcastle': 'Newcastle United',
    'newcastle united': 'Newcastle United',
    'newcastle united fc': 'Newcastle United',

    # Nottingham Forest
    'nottingham forest': 'Nottingham Forest',
    'nottingham forest fc': 'Nottingham Forest',
    'nottm forest': 'Nottingham Forest',
    'forest': 'Nottingham Forest',

    # Southampton
    'southampton': 'Southampton',
    'southampton fc': 'Southampton',

    # Tottenham
    'tottenham': 'Tottenham',
    'tottenham hotspur': 'Tottenham',
    'tottenham hotspur fc': 'Tottenham',
    'spurs': 'Tottenham',

    # West Ham
    'west ham': 'West Ham',
    'west ham united': 'West Ham',
    'west ham united fc': 'West Ham',

    # Wolves
    'wolverhampton': 'Wolverhampton',
    'wolverhampton wanderers': 'Wolverhampton',
    'wolverhampton wanderers fc': 'Wolverhampton',
    'wolves': 'Wolverhampton',
}

# Display names for UI (short form)
_DISPLAY_NAMES = {
    'Arsenal': 'Arsenal',
    'Aston Villa': 'Villa',
    'Bournemouth': 'Bournemouth',
    'Brentford': 'Brentford',
    'Brighton': 'Brighton',
    'Chelsea': 'Chelsea',
    'Crystal Palace': 'Palace',
    'Everton': 'Everton',
    'Fulham': 'Fulham',
    'Ipswich Town': 'Ipswich',
    'Leicester City': 'Leicester',
    'Liverpool': 'Liverpool',
    'Manchester City': 'Man City',
    'Manchester United': 'Man Utd',
    'Newcastle United': 'Newcastle',
    'Nottingham Forest': 'Forest',
    'Southampton': 'Southampton',
    'Tottenham': 'Spurs',
    'West Ham': 'West Ham',
    'Wolverhampton': 'Wolves',
}


def normalize_team_name(team_name: str) -> str:
    """
    Convert any team name variant to its canonical form.

    Args:
        team_name: Any form of team name (e.g., "Aston Villa FC", "villa", "Villa")

    Returns:
        Canonical team name (e.g., "Aston Villa")
        If not recognized, returns the original name stripped and title-cased.
    """
    if not team_name:
        return ""

    # Clean up the input
    cleaned = team_name.strip()
    lookup_key = cleaned.lower()

    # Try direct lookup
    if lookup_key in _TEAM_ALIASES:
        return _TEAM_ALIASES[lookup_key]

    # If already canonical (case-insensitive check)
    for canonical in CANONICAL_TEAMS:
        if canonical.lower() == lookup_key:
            return canonical

    # Not found - log a warning and return cleaned version
    logger.warning(f"Unknown team name: '{team_name}' - returning as-is")
    return cleaned


def teams_match(team1: str, team2: str) -> bool:
    """
    Check if two team names refer to the same team.

    Args:
        team1: First team name
        team2: Second team name

    Returns:
        True if both names normalize to the same canonical team
    """
    return normalize_team_name(team1) == normalize_team_name(team2)


def display_name(team_name: str) -> str:
    """
    Get the short display name for a team.

    Args:
        team_name: Any form of team name

    Returns:
        Short display name (e.g., "Villa" for "Aston Villa")
    """
    if not team_name:
        return ""

    canonical = normalize_team_name(team_name)
    return _DISPLAY_NAMES.get(canonical, canonical)


def get_canonical_from_fixture_team(fixture_team: str) -> str:
    """
    Normalize a team name as it comes from the Football-Data API fixture.
    This is specifically for matching fixture teams to picks.

    Args:
        fixture_team: Team name from fixture (e.g., "Aston Villa FC")

    Returns:
        Canonical team name
    """
    return normalize_team_name(fixture_team)


def find_matching_picks_for_team(picks, team_name: str):
    """
    Find all picks that match a given team name (using normalization).

    Args:
        picks: List of Pick objects
        team_name: Team name to match

    Returns:
        List of picks where team_picked normalizes to the same team
    """
    canonical = normalize_team_name(team_name)
    return [p for p in picks if normalize_team_name(p.team_picked) == canonical]


__all__ = [
    'normalize_team_name',
    'teams_match',
    'display_name',
    'get_canonical_from_fixture_team',
    'find_matching_picks_for_team',
    'CANONICAL_TEAMS',
    '_TEAM_ALIASES',
    '_DISPLAY_NAMES',
]
