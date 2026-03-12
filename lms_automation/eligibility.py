"""
Canonical eligibility logic for Last Man Standing.

This module provides the SINGLE SOURCE OF TRUTH for determining
which players are eligible for a given round.

All code paths (scheduler jobs, admin API endpoints, manual triggers)
MUST use this function to ensure consistent eligibility calculations.

Phase 2a: All queries are scoped to the organiser that owns the round.
Cross-organiser queries are forbidden — if organiser context is missing
the function fails safe by returning an empty list (never a global query).
"""
import logging

logger = logging.getLogger(__name__)


def get_eligible_players_for_round(round_obj, organiser_id=None):
    """
    Get eligible players for a round, scoped strictly to one organiser.

    A player is eligible if:
    1. Their status is 'active'
    2. Their organiser_id matches the round's organiser
    3. They have NOT been eliminated in any previous round of the SAME CYCLE
       with a LOWER round.id (not just round_number)

    The round.id ordering is critical: if a game is restarted within the same
    cycle_number (e.g., admin resets and creates new Round 1 without incrementing
    cycle), old elimination history from rounds with higher IDs won't incorrectly
    exclude players.  Only rounds created BEFORE this round (lower ID) in the same
    cycle and same organiser can eliminate players.

    Args:
        round_obj:    The Round object to check eligibility for, or None.
        organiser_id: Optional explicit organiser override.  If supplied it MUST
                      match round_obj.organiser_id — a mismatch is treated as a
                      security violation and raises ValueError.

    Returns:
        List of Player objects who are eligible for this round.

    Raises:
        ValueError: if organiser_id and round_obj.organiser_id are both set but
                    differ (security mismatch guard).

    Fail-safe behaviour:
        If no organiser context can be resolved (neither round_obj.organiser_id
        nor an explicit organiser_id) the function returns [] and logs a warning.
        It never falls back to a global, cross-organiser query.
    """
    # Import here to avoid circular dependency (app.py imports this, models imports db)
    from lms_automation.models import Player, Round, Pick

    # ------------------------------------------------------------------ #
    # 1. Resolve organiser_id                                              #
    # ------------------------------------------------------------------ #
    derived_organiser_id = getattr(round_obj, 'organiser_id', None) if round_obj else None

    # Defensive mismatch guard — explicit caller arg must agree with round
    if organiser_id is not None and derived_organiser_id is not None:
        if organiser_id != derived_organiser_id:
            logger.error(
                "[eligibility] SECURITY VIOLATION: organiser_id mismatch — "
                "explicit=%s but round.organiser_id=%s (round_id=%s). Aborting.",
                organiser_id,
                derived_organiser_id,
                getattr(round_obj, 'id', None),
            )
            raise ValueError(
                f"organiser_id mismatch: explicit {organiser_id} "
                f"does not match round.organiser_id {derived_organiser_id}"
            )

    # Authoritative: prefer derived from round, fall back to explicit kwarg
    effective_organiser_id = derived_organiser_id or organiser_id

    if not effective_organiser_id:
        logger.warning(
            "[eligibility] No organiser_id available "
            "(round_obj=%s, explicit=%s). "
            "Refusing global query — returning empty list.",
            round_obj,
            organiser_id,
        )
        return []

    # ------------------------------------------------------------------ #
    # 2. Fetch active players scoped to this organiser                     #
    # ------------------------------------------------------------------ #
    all_active_players = Player.query.filter_by(
        status='active',
        organiser_id=effective_organiser_id,
    ).all()

    if not round_obj:
        logger.info(
            "[eligibility] No round provided (organiser=%s), "
            "returning all active players (%d)",
            effective_organiser_id,
            len(all_active_players),
        )
        return all_active_players

    # ------------------------------------------------------------------ #
    # 3. Find eliminations from prior rounds in the same cycle & organiser #
    # ------------------------------------------------------------------ #
    cycle_number = round_obj.cycle_number or 1
    eliminated_player_ids = set()

    # Scope previous-round lookup to the SAME organiser — never cross boundaries
    previous_rounds = Round.query.filter(
        Round.organiser_id == effective_organiser_id,
        Round.cycle_number == cycle_number,
        Round.id < round_obj.id,
    ).all()

    for prev_round in previous_rounds:
        eliminated_picks = Pick.query.filter_by(
            round_id=prev_round.id,
            is_eliminated=True,
        ).all()
        for pick in eliminated_picks:
            eliminated_player_ids.add(pick.player_id)

    eligible_players = [
        player for player in all_active_players
        if player.id not in eliminated_player_ids
    ]

    logger.info(
        "[eligibility] Round %s (id=%s, cycle=%s, organiser=%s): "
        "active=%d, eliminated_prior=%d, eligible=%d",
        round_obj.round_number,
        round_obj.id,
        cycle_number,
        effective_organiser_id,
        len(all_active_players),
        len(eliminated_player_ids),
        len(eligible_players),
    )

    return eligible_players
