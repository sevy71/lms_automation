"""Team-related helpers shared across blueprints.

This module re-exports from the centralized team_utils module to maintain
backwards compatibility while ensuring a single source of truth for team names.
"""

from __future__ import annotations

# Import from centralized team_utils for consistency
from lms_automation.team_utils import (
    display_name,
    normalize_team_name,
    teams_match,
    CANONICAL_TEAMS,
)

# Backwards compatibility: expose TEAM_DISPLAY_NAMES
# This is now derived from the centralized module
from lms_automation.team_utils import _DISPLAY_NAMES, _TEAM_ALIASES

# Build TEAM_DISPLAY_NAMES for backwards compatibility
# Maps lowercase variants to display names
TEAM_DISPLAY_NAMES: dict[str, str] = {}
for alias, canonical in _TEAM_ALIASES.items():
    display = _DISPLAY_NAMES.get(canonical, canonical)
    TEAM_DISPLAY_NAMES[alias] = display


__all__ = [
    "display_name",
    "normalize_team_name",
    "teams_match",
    "TEAM_DISPLAY_NAMES",
    "CANONICAL_TEAMS",
]
