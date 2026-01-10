#!/usr/bin/env python3
"""
Backfill and Verification Script for LMS Elimination Fixes

This script:
1. Re-evaluates all picks based on fixture results with proper team normalization
2. Fixes is_winner and is_eliminated flags
3. Updates player.status based on elimination state
4. Provides verification queries for the Villa vs Palace draw scenario

Usage:
    # Dry run (no changes)
    python scripts/backfill_and_verify.py --dry-run

    # Apply fixes
    python scripts/backfill_and_verify.py --apply

    # Just run verification queries
    python scripts/backfill_and_verify.py --verify-only
"""

import os
import sys
import argparse
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lms_automation.app import app
from lms_automation.models import db, Round, Fixture, Pick, Player
from lms_automation.team_utils import normalize_team_name, teams_match


def verify_villa_palace_fixture():
    """
    Verification Query 1: Show fixture result & status for Aston Villa vs Crystal Palace
    """
    print("\n" + "=" * 60)
    print("VERIFICATION 1: Aston Villa vs Crystal Palace Fixture")
    print("=" * 60)

    # Search for this fixture
    fixtures = Fixture.query.filter(
        db.or_(
            db.and_(
                Fixture.home_team.ilike('%aston villa%'),
                Fixture.away_team.ilike('%crystal palace%')
            ),
            db.and_(
                Fixture.home_team.ilike('%crystal palace%'),
                Fixture.away_team.ilike('%aston villa%')
            ),
            db.and_(
                Fixture.home_team.ilike('%villa%'),
                Fixture.away_team.ilike('%palace%')
            ),
            db.and_(
                Fixture.home_team.ilike('%palace%'),
                Fixture.away_team.ilike('%villa%')
            )
        )
    ).all()

    if not fixtures:
        print("  No fixtures found matching Aston Villa vs Crystal Palace")
        return None

    for fx in fixtures:
        round_obj = Round.query.get(fx.round_id)
        is_draw = fx.home_score == fx.away_score if fx.home_score is not None else None

        print(f"""
  Fixture ID: {fx.id}
  Round: {round_obj.round_number if round_obj else 'N/A'} (round_id={fx.round_id})
  Match: {fx.home_team} vs {fx.away_team}
  Date: {fx.date}
  Score: {fx.home_score} - {fx.away_score}
  Status: {fx.status}
  Is Draw: {is_draw}
  Canonical Home: {normalize_team_name(fx.home_team)}
  Canonical Away: {normalize_team_name(fx.away_team)}
""")

    return fixtures


def verify_villa_picks(round_id=None):
    """
    Verification Query 2: Show all picks for Aston Villa (normalized)
    """
    print("\n" + "=" * 60)
    print("VERIFICATION 2: All Picks for Aston Villa (any round)")
    print("=" * 60)

    # Get all picks
    picks_query = Pick.query

    if round_id:
        picks_query = picks_query.filter_by(round_id=round_id)

    all_picks = picks_query.all()

    villa_canonical = normalize_team_name("Aston Villa")
    villa_picks = []

    for pick in all_picks:
        if normalize_team_name(pick.team_picked) == villa_canonical:
            villa_picks.append(pick)

    print(f"  Found {len(villa_picks)} pick(s) for Aston Villa")
    print()

    for pick in villa_picks:
        round_obj = pick.round
        player = pick.player
        print(f"""  Pick ID: {pick.id}
    Player: {player.name} (player_id={player.id})
    Round: {round_obj.round_number if round_obj else 'N/A'} (round_id={pick.round_id})
    Team Picked: {pick.team_picked}
    Canonical: {normalize_team_name(pick.team_picked)}
    is_winner: {pick.is_winner}
    is_eliminated: {pick.is_eliminated}
    Player Status: {player.status}
    auto_assigned: {pick.auto_assigned}
""")

    return villa_picks


def verify_elimination_status():
    """
    Verification Query 3: Show is_winner/is_eliminated for relevant picks
    """
    print("\n" + "=" * 60)
    print("VERIFICATION 3: Pick Results Summary")
    print("=" * 60)

    # Get counts by status
    total_picks = Pick.query.count()
    winner_picks = Pick.query.filter_by(is_winner=True).count()
    loser_picks = Pick.query.filter_by(is_winner=False).count()
    pending_picks = Pick.query.filter(Pick.is_winner.is_(None)).count()
    eliminated_picks = Pick.query.filter_by(is_eliminated=True).count()

    print(f"""
  Total Picks: {total_picks}
  Winners (is_winner=True): {winner_picks}
  Losers (is_winner=False): {loser_picks}
  Pending (is_winner=None): {pending_picks}
  Eliminated (is_eliminated=True): {eliminated_picks}
""")

    # Show picks with is_winner=False but is_eliminated=False (should not exist)
    inconsistent = Pick.query.filter_by(is_winner=False, is_eliminated=False).all()
    if inconsistent:
        print(f"  WARNING: {len(inconsistent)} picks have is_winner=False but is_eliminated=False!")
        for pick in inconsistent[:10]:
            print(f"    - pick_id={pick.id}, player_id={pick.player_id}, team={pick.team_picked}")


def verify_player_status():
    """
    Verification Query 4: Show player status counts after propagation
    """
    print("\n" + "=" * 60)
    print("VERIFICATION 4: Player Status Distribution")
    print("=" * 60)

    active_count = Player.query.filter_by(status='active').count()
    eliminated_count = Player.query.filter_by(status='eliminated').count()
    winner_count = Player.query.filter_by(status='winner').count()
    other_count = Player.query.filter(
        ~Player.status.in_(['active', 'eliminated', 'winner'])
    ).count()

    print(f"""
  Active Players: {active_count}
  Eliminated Players: {eliminated_count}
  Winners: {winner_count}
  Other/Unknown: {other_count}
""")

    # List players with elimination status
    eliminated_players = Player.query.filter_by(status='eliminated').all()
    if eliminated_players:
        print("  Eliminated Players:")
        for p in eliminated_players:
            # Find which round they were eliminated
            elim_pick = Pick.query.filter_by(player_id=p.id, is_eliminated=True).first()
            round_info = f"R{elim_pick.round.round_number}" if elim_pick and elim_pick.round else "unknown"
            team_info = elim_pick.team_picked if elim_pick else "unknown"
            print(f"    - {p.name} (id={p.id}): eliminated in {round_info} with pick '{team_info}'")


def backfill_pick_results(dry_run=True):
    """
    Re-evaluate all picks based on fixture results.

    This function:
    1. For each completed fixture, marks picks appropriately:
       - Home win: home team picks -> is_winner=True, away team picks -> is_winner=False
       - Away win: away team picks -> is_winner=True, home team picks -> is_winner=False
       - Draw: both teams -> is_winner=False (draws eliminate)
    2. Sets is_eliminated=True for all picks where is_winner=False
    3. Updates player.status='eliminated' for eliminated picks
    """
    print("\n" + "=" * 60)
    print(f"BACKFILL: Re-evaluating picks {'(DRY RUN)' if dry_run else '(APPLYING CHANGES)'}")
    print("=" * 60)

    # Get all completed fixtures
    completed_fixtures = Fixture.query.filter_by(status='completed').all()
    print(f"\n  Found {len(completed_fixtures)} completed fixture(s)")

    picks_updated = 0
    eliminations_set = 0
    players_eliminated = set()

    for fixture in completed_fixtures:
        if fixture.home_score is None or fixture.away_score is None:
            continue

        home_canonical = normalize_team_name(fixture.home_team)
        away_canonical = normalize_team_name(fixture.away_team)

        # Get all picks for this round
        picks = Pick.query.filter_by(round_id=fixture.round_id).all()

        for pick in picks:
            pick_canonical = normalize_team_name(pick.team_picked)

            # Check if this pick matches this fixture
            if pick_canonical == home_canonical:
                # This pick is for the home team
                if fixture.home_score > fixture.away_score:
                    new_winner = True  # Home win
                elif fixture.home_score < fixture.away_score:
                    new_winner = False  # Home loss
                else:
                    new_winner = False  # Draw = elimination

                if pick.is_winner != new_winner:
                    print(f"    Pick {pick.id}: {pick.team_picked} -> is_winner={new_winner} "
                          f"(was {pick.is_winner}) [fixture: {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}]")
                    if not dry_run:
                        pick.is_winner = new_winner
                    picks_updated += 1

            elif pick_canonical == away_canonical:
                # This pick is for the away team
                if fixture.away_score > fixture.home_score:
                    new_winner = True  # Away win
                elif fixture.away_score < fixture.home_score:
                    new_winner = False  # Away loss
                else:
                    new_winner = False  # Draw = elimination

                if pick.is_winner != new_winner:
                    print(f"    Pick {pick.id}: {pick.team_picked} -> is_winner={new_winner} "
                          f"(was {pick.is_winner}) [fixture: {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}]")
                    if not dry_run:
                        pick.is_winner = new_winner
                    picks_updated += 1

    print(f"\n  Picks to update: {picks_updated}")

    # Now set is_eliminated for all losing picks
    print("\n  Setting is_eliminated for losing picks...")
    losing_picks = Pick.query.filter_by(is_winner=False).all()

    for pick in losing_picks:
        if not pick.is_eliminated:
            print(f"    Pick {pick.id} (player={pick.player.name}, team={pick.team_picked}): is_eliminated=True")
            if not dry_run:
                pick.is_eliminated = True
            eliminations_set += 1

        # Update player status
        if pick.player.status != 'eliminated':
            print(f"    Player {pick.player.id} ({pick.player.name}): status='eliminated'")
            if not dry_run:
                pick.player.status = 'eliminated'
            players_eliminated.add(pick.player.id)

    print(f"\n  Eliminations to set: {eliminations_set}")
    print(f"  Players to eliminate: {len(players_eliminated)}")

    if not dry_run:
        db.session.commit()
        print("\n  Changes committed to database.")
    else:
        print("\n  DRY RUN - No changes made. Run with --apply to apply changes.")


def generate_sql_queries():
    """
    Generate raw SQL queries for manual verification
    """
    print("\n" + "=" * 60)
    print("SQL QUERIES FOR MANUAL VERIFICATION")
    print("=" * 60)

    print("""
-- Query 1: Show Aston Villa vs Crystal Palace fixture
SELECT
    f.id as fixture_id,
    r.round_number,
    f.home_team,
    f.away_team,
    f.home_score,
    f.away_score,
    f.status,
    f.date,
    CASE WHEN f.home_score = f.away_score THEN 'DRAW' ELSE 'NOT DRAW' END as is_draw
FROM fixtures f
JOIN rounds r ON f.round_id = r.id
WHERE (
    LOWER(f.home_team) LIKE '%aston villa%'
    OR LOWER(f.away_team) LIKE '%aston villa%'
    OR LOWER(f.home_team) LIKE '%villa%'
    OR LOWER(f.away_team) LIKE '%villa%'
);

-- Query 2: Show all Aston Villa picks with results
SELECT
    pk.id as pick_id,
    p.id as player_id,
    p.name as player_name,
    r.round_number,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated,
    p.status as player_status,
    pk.auto_assigned
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE LOWER(pk.team_picked) LIKE '%villa%'
   OR LOWER(pk.team_picked) LIKE '%aston%'
ORDER BY r.round_number, p.name;

-- Query 3: Show pick results summary
SELECT
    CASE
        WHEN is_winner = true THEN 'Winner'
        WHEN is_winner = false THEN 'Loser'
        ELSE 'Pending'
    END as result,
    COUNT(*) as count,
    SUM(CASE WHEN is_eliminated THEN 1 ELSE 0 END) as eliminated_count
FROM picks
GROUP BY is_winner
ORDER BY result;

-- Query 4: Show player status distribution
SELECT
    status,
    COUNT(*) as count
FROM players
GROUP BY status;

-- Query 5: Find inconsistent picks (loser but not eliminated)
SELECT
    pk.id as pick_id,
    p.name as player_name,
    r.round_number,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated,
    p.status as player_status
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.is_winner = false AND pk.is_eliminated = false;

-- Query 6: Show draws that should have eliminated picks
SELECT
    f.id as fixture_id,
    r.round_number,
    f.home_team,
    f.away_team,
    f.home_score || '-' || f.away_score as score,
    (SELECT COUNT(*) FROM picks WHERE round_id = f.round_id
     AND (LOWER(team_picked) LIKE '%' || LOWER(SPLIT_PART(f.home_team, ' ', 1)) || '%'
          OR LOWER(team_picked) LIKE '%' || LOWER(SPLIT_PART(f.away_team, ' ', 1)) || '%')
    ) as affected_picks
FROM fixtures f
JOIN rounds r ON f.round_id = r.id
WHERE f.status = 'completed'
  AND f.home_score IS NOT NULL
  AND f.home_score = f.away_score
ORDER BY r.round_number;
""")


def main():
    parser = argparse.ArgumentParser(description='Backfill and verify LMS elimination data')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be changed without applying')
    parser.add_argument('--apply', action='store_true', help='Apply the backfill changes')
    parser.add_argument('--verify-only', action='store_true', help='Only run verification queries')
    parser.add_argument('--sql', action='store_true', help='Generate SQL queries for manual verification')

    args = parser.parse_args()

    if not any([args.dry_run, args.apply, args.verify_only, args.sql]):
        parser.print_help()
        print("\n\nPlease specify one of: --dry-run, --apply, --verify-only, or --sql")
        sys.exit(1)

    with app.app_context():
        print("\n" + "=" * 60)
        print("LMS ELIMINATION BACKFILL AND VERIFICATION")
        print(f"Time: {datetime.now().isoformat()}")
        print("=" * 60)

        if args.sql:
            generate_sql_queries()
            return

        # Run verification queries
        verify_villa_palace_fixture()
        verify_villa_picks()
        verify_elimination_status()
        verify_player_status()

        if args.verify_only:
            return

        # Run backfill
        backfill_pick_results(dry_run=not args.apply)

        # Run verification again after backfill
        if args.apply:
            print("\n\n" + "=" * 60)
            print("POST-BACKFILL VERIFICATION")
            print("=" * 60)
            verify_elimination_status()
            verify_player_status()


if __name__ == '__main__':
    main()
