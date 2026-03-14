"""
Round Summary Blueprint — generates a formatted summary for a completed round.

GET /api/round-summary/<round_id>

Returns both structured JSON and a pre-formatted text block suitable for
posting in WhatsApp or Telegram.
"""
from __future__ import annotations

from collections import Counter

from flask import Blueprint, jsonify

from lms_automation.models import Pick, Player, Round, db
from lms_automation.routes.utils import admin_required

round_summary_bp = Blueprint("round_summary", __name__)


def _build_summary(round_id: int) -> dict:
    """Compute summary statistics for a round.

    Returns a dict with both raw numbers and a pre-formatted text block.
    Raises ValueError if the round is not found.
    """
    round_obj = Round.query.get(round_id)
    if round_obj is None:
        raise ValueError(f"Round {round_id} not found")
    if round_obj.status != "completed":
        raise ValueError(
            f"Round {round_obj.round_number} is not yet finished "
            f"(status: {round_obj.status}). Summaries are only available for completed rounds."
        )

    picks = Pick.query.filter_by(round_id=round_id).all()

    # ── Pick counts by team ────────────────────────────────────────────────
    pick_counter: Counter[str] = Counter()
    elim_counter: Counter[str] = Counter()

    for pick in picks:
        team = pick.team_picked or "Unknown"
        pick_counter[team] += 1
        if pick.is_eliminated:
            elim_counter[team] += 1

    # ── Player counts ──────────────────────────────────────────────────────
    # Organiser-scoped query so numbers are consistent with the round's org
    oid = round_obj.organiser_id
    pq = Player.query.filter_by(organiser_id=oid) if oid else Player.query

    players_remaining = pq.filter_by(status="active").count()
    eliminated_this_round = sum(1 for p in picks if p.is_eliminated)

    # ── Most picked team ───────────────────────────────────────────────────
    if pick_counter:
        most_picked_team, most_picked_count = pick_counter.most_common(1)[0]
    else:
        most_picked_team, most_picked_count = "N/A", 0

    # ── Biggest eliminator ─────────────────────────────────────────────────
    if elim_counter:
        biggest_eliminator, eliminator_count = elim_counter.most_common(1)[0]
    else:
        biggest_eliminator, eliminator_count = "N/A", 0

    # ── Formatted text ─────────────────────────────────────────────────────
    text = (
        f"Round {round_obj.round_number} Summary\n"
        f"\n"
        f"Biggest eliminator: {biggest_eliminator} ({eliminator_count} elimination{'s' if eliminator_count != 1 else ''})\n"
        f"Most picked team: {most_picked_team} ({most_picked_count} pick{'s' if most_picked_count != 1 else ''})\n"
        f"\n"
        f"Players remaining: {players_remaining}\n"
        f"Eliminated this round: {eliminated_this_round}"
    )

    return {
        "round_id": round_obj.id,
        "round_number": round_obj.round_number,
        "round_status": round_obj.status,
        "players_remaining": players_remaining,
        "eliminated_this_round": eliminated_this_round,
        "most_picked_team": most_picked_team,
        "most_picked_count": most_picked_count,
        "biggest_eliminator": biggest_eliminator,
        "eliminator_count": eliminator_count,
        "formatted_text": text,
    }


@round_summary_bp.route("/api/round-summary/<int:round_id>")
@admin_required
def get_round_summary(round_id: int):
    """Return a round summary as JSON + pre-formatted text block."""
    try:
        summary = _build_summary(round_id)
        return jsonify({"success": True, **summary})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500
