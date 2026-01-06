"""Team-related helpers shared across blueprints."""

from __future__ import annotations

TEAM_DISPLAY_NAMES: dict[str, str] = {
    'arsenal': 'Arsenal',
    'arsenal fc': 'Arsenal',
    'aston villa': 'Villa',
    'aston villa fc': 'Villa',
    'afc bournemouth': 'Bournmouth',
    'bournemouth': 'Bournmouth',
    'bournemouth afc': 'Bournmouth',
    'brentford': 'Brentford',
    'brentford fc': 'Brentford',
    'brighton': 'Brighton',
    'brighton & hove albion': 'Brighton',
    'brighton and hove albion': 'Brighton',
    'brighton hove albion': 'Brighton',
    'burnley': 'Burnley',
    'burnley fc': 'Burnley',
    'chelsea': 'Chelsea',
    'chelsea fc': 'Chelsea',
    'crystal palace': 'Palace',
    'crystal palace fc': 'Palace',
    'palace': 'Palace',
    'everton': 'Everton',
    'everton fc': 'Everton',
    'fulham': 'Fulham',
    'fulham fc': 'Fulham',
    'leeds': 'Leeds',
    'leeds united': 'Leeds',
    'leeds united fc': 'Leeds',
    'liverpool': 'Liverpool',
    'liverpool fc': 'Liverpool',
    'manchester city': 'Man City',
    'manchester city fc': 'Man City',
    'man city': 'Man City',
    'manchester united': 'Man UTD',
    'manchester united fc': 'Man UTD',
    'man united': 'Man UTD',
    'newcastle': 'Newcastle',
    'newcastle united': 'Newcastle',
    'newcastle united fc': 'Newcastle',
    'nottingham forest': 'Forest',
    'nottm forest': 'Forest',
    'forest': 'Forest',
    'sunderland': 'Sunderland',
    'sunderland afc': 'Sunderland',
    'tottenham': 'Spurs',
    'tottenham hotspur': 'Spurs',
    'tottenham hotspur fc': 'Spurs',
    'spurs': 'Spurs',
    'west ham': 'West Ham',
    'west ham united': 'West Ham',
    'west ham united fc': 'West Ham',
    'wolverhampton wanderers': 'Wolves',
    'wolverhampton': 'Wolves',
    'wolves': 'Wolves',
}


def display_name(team_name: str | None) -> str:
    """Return the preferred display label for a team."""
    if not team_name:
        return ''
    trimmed = team_name.strip()
    return TEAM_DISPLAY_NAMES.get(trimmed.lower(), trimmed)


__all__ = ["display_name", "TEAM_DISPLAY_NAMES"]
