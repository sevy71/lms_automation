"""
Canonical eligibility logic for Last Man Standing.

This module provides the SINGLE SOURCE OF TRUTH for determining
which players are eligible for a given round.

All code paths (scheduler jobs, admin API endpoints, manual triggers)
MUST use this function to ensure consistent eligibility calculations.
"""
import logging

logger = logging.getLogger(__name__)


def get_eligible_players_for_round(round_obj):
    """
    Get eligible players for a round based on survival from previous rounds.

    A player is eligible if:
    1. Their global status is 'active' (not already globally eliminated/winner)
    2. They have NOT been eliminated in any previous round of the SAME CYCLE
       with a LOWER round.id (not just round_number)

    The round.id check is critical: it ensures that if a game is restarted within
    the same cycle_number (e.g., admin resets and creates new Round 1 without
    incrementing cycle), old elimination history from rounds with higher IDs
    won't incorrectly exclude players. Only rounds created BEFORE this round
    (lower ID) in the same cycle can eliminate players.

    Args:
        round_obj: The Round object to check eligibility for, or None

    Returns:
        List of Player objects who are eligible for this round
    """
    # Import here to avoid circular dependency (app.py imports this, models imports db)
    from lms_automation.models import Player, Round, Pick

    all_active_players = Player.query.filter_by(status='active').all()

    if not round_obj:
        logger.info("[eligibility] No round provided, returning all active players")
        return all_active_players

    cycle_number = round_obj.cycle_number or 1
    eliminated_player_ids = set()

    # Find all rounds in this cycle created BEFORE the current round (by ID)
    previous_rounds = Round.query.filter(
        Round.cycle_number == cycle_number,
        Round.id < round_obj.id
    ).all()

    for prev_round in previous_rounds:
        eliminated_picks = Pick.query.filter_by(
            round_id=prev_round.id,
            is_eliminated=True
        ).all()
        for pick in eliminated_picks:
            eliminated_player_ids.add(pick.player_id)

    eligible_players = [
        player for player in all_active_players
        if player.id not in eliminated_player_ids
    ]

    logger.info(
        f"[eligibility] Round {round_obj.round_number} (id={round_obj.id}, cycle={cycle_number}): "
        f"active={len(all_active_players)}, eliminated_prior={len(eliminated_player_ids)}, "
        f"eligible={len(eligible_players)}"
    )

    return eligible_players
