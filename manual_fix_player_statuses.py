#!/usr/bin/env python3
"""
Manual fix script to update player.status based on picks.is_eliminated

This should be run ONCE to fix the current state, then the automated
elimination job will keep it in sync going forward.

Usage:
    python3 manual_fix_player_statuses.py

Or in Railway console:
    python manual_fix_player_statuses.py
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from lms_automation.app import app
from lms_automation.models import db, Player, Pick

def fix_player_statuses():
    """Update player.status to 'eliminated' for players with is_eliminated=True picks"""

    with app.app_context():
        print("=" * 60)
        print("MANUAL PLAYER STATUS FIX")
        print("=" * 60)

        # Get all players
        all_players = Player.query.all()
        print(f"\nTotal players in database: {len(all_players)}")

        # Check current status distribution
        status_counts = {}
        for player in all_players:
            status_counts[player.status] = status_counts.get(player.status, 0) + 1

        print("\nCurrent player status distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")

        # Find players who should be eliminated (have is_eliminated=True picks)
        eliminated_players = set()
        eliminated_picks = Pick.query.filter_by(is_eliminated=True).all()

        for pick in eliminated_picks:
            eliminated_players.add(pick.player_id)

        print(f"\nPlayers with is_eliminated=TRUE picks: {len(eliminated_players)}")

        # Update player statuses
        updated_count = 0
        already_correct = 0

        for player_id in eliminated_players:
            player = Player.query.get(player_id)
            if player:
                if player.status != 'eliminated':
                    print(f"  Updating: {player.name} (id={player.id}) from '{player.status}' to 'eliminated'")
                    player.status = 'eliminated'
                    updated_count += 1
                else:
                    already_correct += 1

        # Commit changes
        if updated_count > 0:
            print(f"\nCommitting {updated_count} player status updates...")
            db.session.commit()
            print("✅ Commit successful!")
        else:
            print("\n✅ No updates needed - all player statuses already correct!")

        # Verify final state
        print("\n" + "=" * 60)
        print("FINAL STATE")
        print("=" * 60)

        final_status_counts = {}
        for player in Player.query.all():
            final_status_counts[player.status] = final_status_counts.get(player.status, 0) + 1

        print("\nFinal player status distribution:")
        for status, count in sorted(final_status_counts.items()):
            print(f"  {status}: {count}")

        print("\nSummary:")
        print(f"  Players updated: {updated_count}")
        print(f"  Already correct: {already_correct}")
        print(f"  Total eliminated: {len(eliminated_players)}")

        # Show eliminated players
        if eliminated_players:
            print("\nEliminated players:")
            for player_id in sorted(eliminated_players):
                player = Player.query.get(player_id)
                if player:
                    # Find which round they were eliminated in
                    elim_pick = Pick.query.filter_by(
                        player_id=player_id,
                        is_eliminated=True
                    ).first()
                    if elim_pick:
                        print(f"  {player.name} (id={player.id}) - Round {elim_pick.round.round_number}, picked {elim_pick.team_picked}")

        print("\n" + "=" * 60)
        print("✅ FIX COMPLETE - Check Picks Grid to verify")
        print("=" * 60)

if __name__ == '__main__':
    try:
        fix_player_statuses()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
