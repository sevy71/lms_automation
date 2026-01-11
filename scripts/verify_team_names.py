#!/usr/bin/env python3
"""
Team Name Canonicalization Verification Script

This script verifies that:
1. All team names in the database are canonical
2. Alphabetical ordering is correct (Arsenal before Bournemouth, not AFC Bournemouth)
3. The normalize_team_name function works correctly

Run with:
    python scripts/verify_team_names.py

Or with database connection:
    DATABASE_URL=... python scripts/verify_team_names.py --check-db
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lms_automation.team_utils import (
    normalize_team_name,
    teams_match,
    CANONICAL_TEAMS,
    _TEAM_ALIASES
)


def test_canonical_teams_ordering():
    """Verify that CANONICAL_TEAMS sort correctly alphabetically."""
    print("=" * 60)
    print("TEST: Canonical Teams Alphabetical Ordering")
    print("=" * 60)

    sorted_teams = sorted(CANONICAL_TEAMS)
    print(f"\nCanonical teams in alphabetical order:")
    for i, team in enumerate(sorted_teams, 1):
        print(f"  {i:2}. {team}")

    # Specific check: Bournemouth must come AFTER Arsenal and Aston Villa
    arsenal_idx = sorted_teams.index('Arsenal')
    aston_villa_idx = sorted_teams.index('Aston Villa')
    bournemouth_idx = sorted_teams.index('Bournemouth')

    print(f"\nCritical ordering check:")
    print(f"  Arsenal index: {arsenal_idx}")
    print(f"  Aston Villa index: {aston_villa_idx}")
    print(f"  Bournemouth index: {bournemouth_idx}")

    assert arsenal_idx < bournemouth_idx, "Arsenal must come before Bournemouth!"
    assert aston_villa_idx < bournemouth_idx, "Aston Villa must come before Bournemouth!"

    print("\n  PASSED: Bournemouth correctly comes AFTER Arsenal and Aston Villa")
    return True


def test_normalization():
    """Test that normalize_team_name works correctly for common cases."""
    print("\n" + "=" * 60)
    print("TEST: Team Name Normalization")
    print("=" * 60)

    test_cases = [
        # (input, expected_output)
        ('AFC Bournemouth', 'Bournemouth'),
        ('Bournemouth', 'Bournemouth'),
        ('bournemouth', 'Bournemouth'),
        ('Bournemouth AFC', 'Bournemouth'),
        ('Aston Villa FC', 'Aston Villa'),
        ('Villa', 'Aston Villa'),
        ('aston villa', 'Aston Villa'),
        ('Brighton & Hove Albion', 'Brighton'),
        ('Brighton and Hove Albion FC', 'Brighton'),
        ('Man United', 'Manchester United'),
        ('Manchester Utd', 'Manchester United'),
        ('Spurs', 'Tottenham'),
        ('Wolves', 'Wolverhampton'),
        ('Forest', 'Nottingham Forest'),
        ('Palace', 'Crystal Palace'),
    ]

    all_passed = True
    for input_name, expected in test_cases:
        result = normalize_team_name(input_name)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_passed = False
        print(f"  {status}: '{input_name}' -> '{result}' (expected: '{expected}')")

    if all_passed:
        print("\n  All normalization tests PASSED")
    else:
        print("\n  Some normalization tests FAILED")

    return all_passed


def test_teams_match():
    """Test that teams_match function works correctly."""
    print("\n" + "=" * 60)
    print("TEST: teams_match Function")
    print("=" * 60)

    test_cases = [
        # (team1, team2, should_match)
        ('AFC Bournemouth', 'Bournemouth', True),
        ('Aston Villa FC', 'Villa', True),
        ('Brighton & Hove Albion', 'Brighton', True),
        ('Arsenal', 'Arsenal', True),
        ('Arsenal', 'Chelsea', False),
        ('AFC Bournemouth', 'Arsenal', False),
    ]

    all_passed = True
    for team1, team2, should_match in test_cases:
        result = teams_match(team1, team2)
        status = "PASS" if result == should_match else "FAIL"
        if result != should_match:
            all_passed = False
        print(f"  {status}: teams_match('{team1}', '{team2}') = {result} (expected: {should_match})")

    if all_passed:
        print("\n  All teams_match tests PASSED")
    else:
        print("\n  Some teams_match tests FAILED")

    return all_passed


def test_auto_pick_alphabetical_fallback():
    """Simulate auto-pick alphabetical fallback to verify correct ordering."""
    print("\n" + "=" * 60)
    print("TEST: Auto-Pick Alphabetical Fallback Simulation")
    print("=" * 60)

    # Simulate a set of available teams (as would come from fixtures)
    available_raw = [
        'AFC Bournemouth',  # Should normalize to 'Bournemouth'
        'Arsenal',
        'Aston Villa FC',  # Should normalize to 'Aston Villa'
        'Chelsea',
        'Liverpool',
    ]

    # Normalize all teams
    available_canonical = sorted({normalize_team_name(t) for t in available_raw})

    print(f"\n  Raw fixture teams: {available_raw}")
    print(f"  Normalized & sorted: {available_canonical}")
    print(f"  First alphabetically: {available_canonical[0]}")

    # The first team should be Arsenal, NOT AFC Bournemouth
    assert available_canonical[0] == 'Arsenal', \
        f"Expected 'Arsenal' to be first, got '{available_canonical[0]}'"

    # Bournemouth should come after Arsenal and Aston Villa
    assert available_canonical.index('Arsenal') < available_canonical.index('Bournemouth'), \
        "Arsenal must come before Bournemouth"
    assert available_canonical.index('Aston Villa') < available_canonical.index('Bournemouth'), \
        "Aston Villa must come before Bournemouth"

    print("\n  PASSED: Auto-pick would correctly select 'Arsenal' as first alphabetical choice")
    return True


def check_database():
    """Check the actual database for non-canonical team names."""
    print("\n" + "=" * 60)
    print("DATABASE CHECK: Looking for non-canonical team names")
    print("=" * 60)

    try:
        from lms_automation.app import app, db
        from lms_automation.models import Fixture, Pick

        with app.app_context():
            # Check fixtures
            fixtures = Fixture.query.all()
            non_canonical_fixtures = []

            for f in fixtures:
                home_canonical = normalize_team_name(f.home_team)
                away_canonical = normalize_team_name(f.away_team)

                if f.home_team != home_canonical:
                    non_canonical_fixtures.append(
                        f"Fixture {f.id}: home_team '{f.home_team}' should be '{home_canonical}'"
                    )
                if f.away_team != away_canonical:
                    non_canonical_fixtures.append(
                        f"Fixture {f.id}: away_team '{f.away_team}' should be '{away_canonical}'"
                    )

            # Check picks
            picks = Pick.query.all()
            non_canonical_picks = []

            for p in picks:
                team_canonical = normalize_team_name(p.team_picked)
                if p.team_picked != team_canonical:
                    non_canonical_picks.append(
                        f"Pick {p.id}: team_picked '{p.team_picked}' should be '{team_canonical}'"
                    )

            # Specific AFC Bournemouth check
            afc_bournemouth_fixtures = Fixture.query.filter(
                (Fixture.home_team == 'AFC Bournemouth') |
                (Fixture.away_team == 'AFC Bournemouth')
            ).count()

            afc_bournemouth_picks = Pick.query.filter_by(team_picked='AFC Bournemouth').count()

            print(f"\n  Fixtures scanned: {len(fixtures)}")
            print(f"  Non-canonical fixtures: {len(non_canonical_fixtures)}")
            print(f"  Picks scanned: {len(picks)}")
            print(f"  Non-canonical picks: {len(non_canonical_picks)}")
            print(f"\n  'AFC Bournemouth' in fixtures: {afc_bournemouth_fixtures}")
            print(f"  'AFC Bournemouth' in picks: {afc_bournemouth_picks}")

            if non_canonical_fixtures:
                print("\n  Non-canonical fixtures (first 10):")
                for issue in non_canonical_fixtures[:10]:
                    print(f"    - {issue}")

            if non_canonical_picks:
                print("\n  Non-canonical picks (first 10):")
                for issue in non_canonical_picks[:10]:
                    print(f"    - {issue}")

            if not non_canonical_fixtures and not non_canonical_picks:
                print("\n  PASSED: All team names in database are canonical")
                return True
            else:
                print("\n  NEEDS BACKFILL: Run POST /admin/backfill_team_names to fix")
                return False

    except Exception as e:
        print(f"\n  ERROR: Could not connect to database: {e}")
        print("  Set DATABASE_URL environment variable and try again")
        return None


def main():
    """Run all verification tests."""
    print("\n" + "=" * 60)
    print("TEAM NAME CANONICALIZATION VERIFICATION")
    print("=" * 60)

    results = []

    # Run unit tests
    results.append(("Canonical Teams Ordering", test_canonical_teams_ordering()))
    results.append(("Team Name Normalization", test_normalization()))
    results.append(("teams_match Function", test_teams_match()))
    results.append(("Auto-Pick Fallback Simulation", test_auto_pick_alphabetical_fallback()))

    # Check database if requested
    if '--check-db' in sys.argv:
        db_result = check_database()
        if db_result is not None:
            results.append(("Database Check", db_result))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n  ALL TESTS PASSED")
        return 0
    else:
        print("\n  SOME TESTS FAILED")
        return 1


if __name__ == '__main__':
    sys.exit(main())
