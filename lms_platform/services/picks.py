"""Picks-related domain logic extracted from the legacy application."""

from __future__ import annotations

from collections import defaultdict

from lms_automation.models import Player, Round, Pick

from .teams import display_name


def build_picks_grid() -> dict[str, object]:
    """Return data needed to populate the picks grid UI."""
    rounds = Round.query.order_by(Round.round_number).all()
    players = Player.query.order_by(Player.name).all()
    picks = Pick.query.all()

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
