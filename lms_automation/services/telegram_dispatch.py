"""
Telegram notification dispatch — the single place where Telegram API calls live.

All functions here:
  - Read TELEGRAM_BOT_TOKEN from the environment.
  - Never raise (log and return on failure).
  - Must be called inside a Flask app_context when they touch the DB.
  - Use marker helpers from services.markers for idempotency.

DELIVERY MODEL
==============
Game-event functions (send_winner_notification, send_rollover_notification, …)
no longer make synchronous HTTP calls.  Instead they call ``enqueue_notification``
to write pending rows to the ``notification_outbox`` table inside the *caller's*
transaction.  The scheduler's ``deliver_notifications`` job delivers those rows
asynchronously with retries and exponential backoff.

IDEMPOTENCY KEYS
================
Every enqueue call passes a deterministic ``idempotency_key`` so that duplicate
enqueues (e.g. on a retried caller path) are silently skipped rather than
producing duplicate messages.  Key format: ``'{event}:{telegram_id}:{scope}'``.

Commit contract (unchanged from before):
  send_winner_notification         — NO commit; caller commits
  send_rollover_notification       — NO commit; caller commits
  send_admin_rollover_notification — NO commit; caller commits
  send_cycle_complete_notification — NO commit; caller commits
  send_picks_published_notification — NO commit; caller commits
  send_round_results_notification  — NO commit; caller commits
"""

from __future__ import annotations

import os
import logging
import requests

from lms_automation.models import db, Player, Pick
from lms_automation.services.markers import has_marker, add_marker
from lms_automation.services.notifications import enqueue_notification

logger = logging.getLogger(__name__)

_SYNC_SEND_TIMEOUT_SECS = 10  # used by send_telegram_message (direct/sync path)


# ---------------------------------------------------------------------------
# Low-level synchronous send — kept for one-off / urgent messages
# ---------------------------------------------------------------------------

def send_telegram_message(
    telegram_id: str,
    message: str,
    button_url: str = None,
    button_text: str = "Make Your Pick",
) -> bool:
    """
    Send a single Telegram message synchronously via the Bot API.

    Returns True on HTTP 200, False on any error (never raises).
    Times out after ``_SYNC_SEND_TIMEOUT_SECS`` seconds.

    Use only for urgent one-off sends (reminder delivery, pick-deadline alerts).
    For game-event broadcasts use the send_* functions below, which enqueue
    messages for async delivery.
    """
    try:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            logger.error("TELEGRAM_BOT_TOKEN not set")
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": telegram_id, "text": message}

        if button_url:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": button_text, "url": button_url}]]
            }

        response = requests.post(url, json=payload, timeout=_SYNC_SEND_TIMEOUT_SECS)
        if response.status_code != 200:
            logger.error(
                "Telegram sendMessage failed: status=%s chat_id=%s response=%s",
                response.status_code,
                telegram_id,
                response.text[:300],
            )
        return response.status_code == 200
    except requests.Timeout:
        logger.error(
            "Telegram sendMessage timed out after %ss for chat_id=%s",
            _SYNC_SEND_TIMEOUT_SECS, telegram_id,
        )
        return False
    except Exception as e:
        logger.error("Error sending Telegram message: %s", e)
        return False


def get_picks_grid_url() -> str:
    """Return the picks-grid URL derived from BASE_URL env var."""
    base_url = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
    return f"{base_url}/picks-grid"


# ---------------------------------------------------------------------------
# Game-event notifications (all enqueue — no direct HTTP, no commit)
# ---------------------------------------------------------------------------

def send_winner_notification(winner: "Player", completed_round: "Round") -> None:
    """
    Enqueue winner DM + broadcast to ALL players.

    Idempotent: guarded by 'game_winner_announced' marker on completed_round.
    Adds the marker but does NOT commit — caller commits so the marker and the
    queued outbox rows land in the same transaction as game-state changes.
    """
    if has_marker(completed_round, "game_winner_announced"):
        logger.info(
            "Winner broadcast: SKIPPED (already announced) winner=%s(id=%s)",
            winner.name, winner.id,
        )
        return

    picks_grid_url = get_picks_grid_url()
    rid = completed_round.id
    winner_dm_text = (
        "🏆 You've won the competition!\n\n"
        "Congratulations! You are the Last Man Standing! 🎉"
    )
    broadcast_text = (
        f"🏁 Competition over — Winner is {winner.name}.\n\n"
        "Thanks for playing! 🎉"
    )

    queued = skipped_missing_telegram = 0

    # 1) Winner DM
    if enqueue_notification(
        winner.telegram_id,
        winner_dm_text,
        button_url=picks_grid_url,
        button_text="📊 View Final Grid",
        round_id=rid,
        idempotency_key=f"winner_dm:{winner.telegram_id}:{rid}",
    ):
        queued += 1
    else:
        skipped_missing_telegram += 1

    # 2) Broadcast to ALL players (including eliminated)
    for player in Player.query.all():
        if enqueue_notification(
            player.telegram_id,
            broadcast_text,
            button_url=picks_grid_url,
            button_text="📊 View Final Grid",
            round_id=rid,
            idempotency_key=f"winner_broadcast:{player.telegram_id}:{rid}",
        ):
            queued += 1
        else:
            skipped_missing_telegram += 1

    # 3) Admin alert
    admin_telegram_id = os.environ.get("ADMIN_TELEGRAM_ID")
    admin_message = (
        f"🏆 WINNER DECLARED: {winner.name} (id={winner.id})\n\n"
        f"Round {completed_round.round_number} complete.\n"
        "Game over - competition ended."
    )
    enqueue_notification(
        admin_telegram_id, admin_message,
        round_id=rid,
        idempotency_key=f"winner_admin:{admin_telegram_id}:{rid}",
    )

    # Mark as announced — NO COMMIT: caller commits atomically with game state
    add_marker(completed_round, "game_winner_announced")

    logger.info(
        "Winner broadcast queued: queued=%s skipped_missing_telegram=%s winner=%s(id=%s)",
        queued, skipped_missing_telegram, winner.name, winner.id,
    )


def send_rollover_notification(new_cycle: int) -> None:
    """Enqueue rollover message to ALL players (active + eliminated). No commit."""
    picks_grid_url = get_picks_grid_url()
    message = (
        f"🔄 ROLLOVER!\n\n"
        f"All players eliminated. Starting Cycle {new_cycle}.\n"
        "Everyone is back in!"
    )

    players = Player.query.all()
    queued = skipped_missing = 0
    for player in players:
        tid = getattr(player, "telegram_id", None)
        if enqueue_notification(
            tid, message,
            button_url=picks_grid_url,
            button_text="📊 View Grid",
            idempotency_key=f"rollover:{tid}:cycle_{new_cycle}" if tid else None,
        ):
            queued += 1
        else:
            skipped_missing += 1

    logger.info(
        "Rollover announcement queued: queued=%s skipped_no_telegram=%s total_players=%s",
        queued, skipped_missing, len(players),
    )


def send_cycle_complete_notification(survivors: list, new_cycle: int) -> None:
    """Enqueue cycle-completion message to ALL players. No commit."""
    picks_grid_url = get_picks_grid_url()
    message = f"📊 Cycle Complete!\n\n{len(survivors)} survivor(s) advance to Cycle {new_cycle}."

    players = Player.query.all()
    queued = skipped_missing = 0
    for player in players:
        tid = getattr(player, "telegram_id", None)
        if enqueue_notification(
            tid, message,
            button_url=picks_grid_url,
            button_text="📊 View Grid",
            idempotency_key=f"cycle_complete:{tid}:cycle_{new_cycle}" if tid else None,
        ):
            queued += 1
        else:
            skipped_missing += 1

    logger.info(
        "Cycle complete announcement queued: queued=%s skipped_no_telegram=%s total_players=%s",
        queued, skipped_missing, len(players),
    )


def send_admin_rollover_notification(new_cycle: int, completed_round: "Round") -> None:
    """Enqueue rollover DM for the admin (ADMIN_TELEGRAM_ID env var). No commit."""
    admin_telegram_id = os.environ.get("ADMIN_TELEGRAM_ID")
    if not admin_telegram_id:
        logger.warning("ADMIN_TELEGRAM_ID not set, skipping admin rollover notification")
        return

    message = (
        f"🔄 ROLLOVER: All players eliminated.\n\n"
        f"Starting Cycle {new_cycle}, Round 1.\n"
        f"Previous round: {completed_round.round_number} "
        f"(Cycle {completed_round.cycle_number or 1})"
    )

    if enqueue_notification(
        admin_telegram_id, message,
        round_id=completed_round.id,
        idempotency_key=f"rollover_admin:{admin_telegram_id}:{completed_round.id}",
    ):
        logger.info("Admin rollover notification queued for telegram_id=%s", admin_telegram_id)
    else:
        logger.warning(
            "Admin rollover notification skipped — no telegram_id (admin_telegram_id=%s)",
            admin_telegram_id,
        )


def send_picks_published_notification(round_obj: "Round") -> None:
    """
    Enqueue picks-locked notification to ACTIVE players.

    Idempotent: guarded by 'picks_published' marker on round_obj.
    Adds the marker but does NOT commit — caller commits.
    """
    if has_marker(round_obj, "picks_published"):
        logger.debug("Round %s: picks already published, skipping", round_obj.round_number)
        return

    picks_count = Pick.query.filter_by(round_id=round_obj.id).count()
    auto_pick_count = Pick.query.filter_by(round_id=round_obj.id, auto_assigned=True).count()

    picks_grid_url = get_picks_grid_url()
    message = f"📋 Round {round_obj.round_number} picks locked!\n\n{picks_count} picks submitted"
    if auto_pick_count > 0:
        message += f" ({auto_pick_count} auto-picks)"
    message += "."

    active_players = Player.query.filter(
        Player.status == "active", Player.telegram_id.isnot(None)
    ).all()

    queued = 0
    for player in active_players:
        if enqueue_notification(
            player.telegram_id, message,
            button_url=picks_grid_url,
            button_text="📊 View Picks Grid",
            round_id=round_obj.id,
            idempotency_key=f"picks_locked:{player.telegram_id}:{round_obj.id}",
        ):
            queued += 1

    all_with_telegram = Player.query.filter(Player.telegram_id.isnot(None)).count()
    skipped_not_active = all_with_telegram - len(active_players)

    logger.info(
        "Round %s: Queued picks-locked notification for %s ACTIVE players "
        "(skipped_not_active=%s)",
        round_obj.round_number, queued, skipped_not_active,
    )

    # Mark as published — NO COMMIT: caller commits
    add_marker(round_obj, "picks_published")


def send_round_results_notification(
    round_obj: "Round",
    winners_count: int,
    eliminated_count: int,
    draw_count: int,
) -> None:
    """
    Enqueue round results for everyone who participated (has a pick).

    Idempotent: guarded by 'results_sent' marker on round_obj.
    Adds the marker (caller must commit afterwards).
    """
    if has_marker(round_obj, "results_sent"):
        logger.debug("Round %s: results already sent, skipping", round_obj.round_number)
        return

    picks = Pick.query.filter_by(round_id=round_obj.id).all()
    participant_ids = {pick.player_id for pick in picks}

    picks_grid_url = get_picks_grid_url()
    message = f"🏁 Round {round_obj.round_number} results!\n\n"
    message += f"✅ {winners_count} survived\n"
    message += f"❌ {eliminated_count} eliminated"
    if draw_count > 0:
        message += f"\n🤝 {draw_count} draw fixture(s)"
    message += "\n\nGood luck next round!"

    queued = skipped_no_telegram = 0

    for player_id in participant_ids:
        player = Player.query.get(player_id)
        if not player:
            continue
        if not player.telegram_id:
            skipped_no_telegram += 1
            continue
        if enqueue_notification(
            player.telegram_id, message,
            button_url=picks_grid_url,
            button_text="📊 View Results",
            round_id=round_obj.id,
            idempotency_key=f"round_results:{player.telegram_id}:{round_obj.id}",
        ):
            queued += 1

    logger.info(
        "Round %s: Queued results notification for %s participants "
        "(skipped_no_telegram=%s, total_participants=%s)",
        round_obj.round_number, queued, skipped_no_telegram, len(participant_ids),
    )

    # Caller commits
    add_marker(round_obj, "results_sent")
