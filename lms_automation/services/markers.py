"""
Phase marker helpers for Round.special_note.

Round lifecycle phases are tracked as semicolon-separated tokens in
Round.special_note (e.g. "tokens_generated; announced; picks_locked").

These helpers are the single source of truth for reading and writing
those tokens.  No Flask imports, no DB commits — pure string operations
on the round object.  The caller owns committing the session.

Known markers:
  tokens_generated        All eligible players have PickTokens for this round.
  announced               Pick-link messages sent to all eligible players.
  picks_locked            Deadline passed + all picks in; no more edits.
  admin_locked            Admin manually locked picks (bypasses deadline check).
  picks_published         "Picks locked" notification sent to ACTIVE players.
  results_sent            Round results notification sent to participants.
  game_winner_announced   Winner DM + broadcast sent.
  rollover_processed_cycle_{N}   Rollover for cycle N already processed.
  rollover_seeded_cycle_{N}      Rollover round for cycle N already created.
  next_round_created_cycle_{N}   Next round for cycle N already created.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lms_automation.models import Round


def has_marker(round_obj: "Round", marker: str) -> bool:
    """Return True if *marker* is present in round.special_note."""
    return bool(round_obj.special_note and marker in round_obj.special_note)


def add_marker(round_obj: "Round", marker: str) -> bool:
    """
    Append *marker* to round.special_note if not already present.

    Returns True if the marker was added, False if it was already there.
    Does NOT commit — the caller is responsible for db.session.commit().
    """
    if has_marker(round_obj, marker):
        return False
    if round_obj.special_note:
        round_obj.special_note += f"; {marker}"
    else:
        round_obj.special_note = marker
    return True


def get_phase_status(round_obj: "Round") -> dict:
    """
    Return a dict of all known phase markers for a round.

    Useful for logging and admin status endpoints.
    """
    known = [
        "tokens_generated",
        "announced",
        "picks_locked",
        "admin_locked",
        "picks_published",
        "results_sent",
        "game_winner_announced",
    ]
    return {
        "status": round_obj.status,
        **{marker: has_marker(round_obj, marker) for marker in known},
    }
