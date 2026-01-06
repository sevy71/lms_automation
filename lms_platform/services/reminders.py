"""Reminder scheduling and messaging helpers."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from flask import current_app

from lms_platform.extensions import db
from lms_automation.models import Pick, PickToken, Player, ReminderSchedule, Round

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def _to_local(dt: datetime | None) -> datetime | None:
    """Convert a naive/UTC datetime into the configured display timezone."""
    if not dt:
        return dt

    tz_name = current_app.config.get('DISPLAY_TIMEZONE', 'Europe/London')
    tz = ZoneInfo(tz_name) if ZoneInfo else None

    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz) if tz else aware


def _earliest_kickoff(round_obj: Round) -> datetime | None:
    """Derive the earliest kickoff for a round from its fixtures."""
    try:
        earliest = None
        for fixture in round_obj.fixtures or []:
            if getattr(fixture, 'date', None) and getattr(fixture, 'time', None):
                kickoff = datetime.combine(fixture.date, fixture.time)
                if earliest is None or kickoff < earliest:
                    earliest = kickoff
        return earliest
    except Exception:
        return None


def _base_url() -> str:
    """Determine the base URL used in reminder messages."""
    base_url = os.environ.get('BASE_URL') or current_app.config.get('BASE_URL') or 'https://localhost:5000'
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"https://{base_url}"
    return base_url.rstrip('/')


def _reminder_payload(player: Player, round_obj: Round, reminder_type: str, token: PickToken) -> Dict[str, Any] | None:
    """Build the WhatsApp reminder payload for the UI."""
    if not player.whatsapp_number:
        return None

    base_url = _base_url()
    pick_url = token.get_pick_url(base_url)
    dashboard_url = f"{base_url}/dashboard/{token.token}"

    if reminder_type == '4_hour':
        urgency = "⏰ 4 hours left!"
        time_msg = "You have 4 hours remaining before the 1-hour cutoff"
    elif reminder_type == '2_hour':
        urgency = "⏰ 2 hours left!"
        time_msg = "Only 2 hours remaining before the 1-hour cutoff"
    else:
        urgency = "📝 Reminder"
        time_msg = "Don't forget"

    message = f"""{urgency}

Hi {player.name}! 👋

{time_msg} to submit your pick for Round {round_obj.round_number} (PL Matchday {round_obj.pl_matchday}).

Haven't picked yet? Don't get eliminated!

🎯 Make your pick: {pick_url}

📊 Check your dashboard: {dashboard_url}

Good luck! 🍀
Last Man Standing"""

    encoded_message = message.replace('\n', '%0A').replace(' ', '%20')
    clean_number = player.whatsapp_number.replace('+', '')
    whatsapp_link = f"https://web.whatsapp.com/send?phone={clean_number}&text={encoded_message}"

    return {
        'player_name': player.name,
        'player_id': player.id,
        'whatsapp_number': player.whatsapp_number,
        'message': message,
        'whatsapp_link': whatsapp_link,
        'reminder_type': reminder_type,
        'round_number': round_obj.round_number,
    }


def dashboard_context() -> Dict[str, Any]:
    """Provide template data for the reminders dashboard."""
    current_round = Round.query.filter_by(status='active').first()
    first_kickoff = None
    cutoff_time = None

    if current_round:
        anchor = current_round.first_kickoff_at or _earliest_kickoff(current_round) or current_round.end_date
        if anchor:
            first_kickoff = _to_local(anchor)
            cutoff_time = _to_local(anchor - timedelta(hours=1))

    return {
        'current_round': current_round,
        'first_kickoff': first_kickoff,
        'cutoff_time': cutoff_time,
    }


def schedule_round_reminders(round_id: int) -> int:
    """Ensure reminders exist for the given round; returns count created."""
    created = ReminderSchedule.create_reminders_for_round(round_id)
    if not created:
        return 0
    return int(created)


def due_reminders_payload() -> List[Dict[str, Any]]:
    """Return reminders that are ready for manual sending."""
    # Opportunistically ensure the active round has reminders seeded
    try:
        active_round = Round.query.filter_by(status='active').first()
        if active_round:
            ReminderSchedule.create_reminders_for_round(active_round.id)
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.debug("Auto-schedule reminders skipped: %s", exc)

    pending = ReminderSchedule.get_pending_reminders()
    results: List[Dict[str, Any]] = []

    for reminder in pending:
        # Skip (and auto-mark) if the player already has a pick for the round
        existing_pick = Pick.query.filter_by(player_id=reminder.player_id, round_id=reminder.round_id).first()
        if existing_pick:
            current_app.logger.debug(
                "Skipping reminder %s: player already picked", reminder.id
            )
            reminder.mark_as_sent()
            continue

        token = PickToken.create_for_player_round(reminder.player_id, reminder.round_id)
        db.session.commit()

        payload = _reminder_payload(reminder.player, reminder.round, reminder.reminder_type, token)
        if not payload:
            continue

        payload['reminder_id'] = reminder.id
        when = reminder.scheduled_time
        try:
            payload['scheduled_time'] = _to_local(when).isoformat() if when else None
        except Exception:
            payload['scheduled_time'] = when.isoformat() if when else None

        results.append(payload)

    return results


def mark_reminder_sent(reminder_id: int) -> bool:
    """Mark a reminder as sent; returns False if it does not exist."""
    reminder = ReminderSchedule.query.get(reminder_id)
    if not reminder:
        return False
    reminder.mark_as_sent()
    return True


__all__ = [
    "dashboard_context",
    "due_reminders_payload",
    "schedule_round_reminders",
    "mark_reminder_sent",
]
