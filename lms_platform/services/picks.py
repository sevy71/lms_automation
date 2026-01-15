"""Picks-related domain logic extracted from the legacy application."""

from __future__ import annotations

from collections import defaultdict

from lms_automation.models import Player, Round, Pick, db

from .teams import display_name


def build_picks_grid(cycle: int | None = None) -> dict[str, object]:
    """Return data needed to populate the picks grid UI.

    Args:
        cycle: Optional cycle number to filter by. If None, uses current cycle.

    Returns:
        dict with rounds, players, current_cycle, and available_cycles.
    """
    # Determine available cycles
    all_cycles = (
        db.session.query(Round.cycle_number)
        .filter(Round.cycle_number.isnot(None))
        .distinct()
        .order_by(Round.cycle_number)
        .all()
    )
    available_cycles = [c[0] for c in all_cycles] or [1]

    # Determine current cycle (default):
    # - If there is an active/pending round, use its cycle
    # - Otherwise, use max(cycle_number)
    if cycle is not None:
        current_cycle = cycle
    else:
        active_round = Round.query.filter(Round.status.in_(['active', 'pending'])).order_by(Round.id.desc()).first()
        if active_round:
            current_cycle = active_round.cycle_number or 1
        else:
            current_cycle = max(available_cycles)

    # Query rounds only in the selected cycle, ordered by round_number
    rounds = (
        Round.query
        .filter(Round.cycle_number == current_cycle)
        .order_by(Round.round_number)
        .all()
    )

    # Build round_id set for efficient filtering
    round_ids = {r.id for r in rounds}

    players = Player.query.order_by(Player.name).all()

    # Fetch only picks whose round is in the selected cycle
    picks = Pick.query.filter(Pick.round_id.in_(round_ids)).all() if round_ids else []

    picks_map: dict[tuple[int, int], Pick] = {}
    results_map: dict[tuple[int, int], dict[str, bool | None]] = {}

    for pick in picks:
        key = (pick.player_id, pick.round_id)
        picks_map[key] = pick
        results_map[key] = {
            'is_winner': pick.is_winner,
            'is_eliminated': pick.is_eliminated,
        }

    players_payload: list[dict[str, object]] = []
    for player in players:
        player_picks: dict[int, dict[str, object] | None] = {}
        for round_obj in rounds:
            key = (player.id, round_obj.id)
            pick = picks_map.get(key)
            if not pick:
                player_picks[round_obj.round_number] = None
                continue
            result = results_map.get(key, {})
            player_picks[round_obj.round_number] = {
                'team': pick.team_picked,
                'team_display': display_name(pick.team_picked),
                'is_winner': result.get('is_winner'),
                'is_eliminated': result.get('is_eliminated'),
            }
        players_payload.append({
            'name': player.name,
            'status': player.status,
            'picks': player_picks,
        })

    return {
        'rounds': [round_obj.round_number for round_obj in rounds],
        'players': players_payload,
        'current_cycle': current_cycle,
        'available_cycles': available_cycles,
    }


def picks_by_round(players_payload: list[dict[str, object]]) -> dict[int, list[dict[str, object]]]:
    """Transform player-centric picks into round-centric buckets."""
    rounds: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
    for player in players_payload:
        for round_number, pick_data in (player.get('picks') or {}).items():
            if pick_data:
                rounds[round_number].append({
                    'player': player['name'],
                    'team': pick_data['team'],
                    'team_display': pick_data.get('team_display') or display_name(pick_data['team']),
                    'is_winner': pick_data.get('is_winner'),
                    'is_eliminated': pick_data.get('is_eliminated'),
                })
    return rounds


__all__ = ["build_picks_grid", "picks_by_round"]
