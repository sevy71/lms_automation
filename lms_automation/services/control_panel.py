"""
Control Panel Service — aggregates data for the organiser Competition Control Panel.

Reads only. No writes, no scheduler interaction.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from lms_automation.models import AutomationRun, Pick, Player, ReminderSchedule, Round


def get_control_panel_data(org) -> dict:
    """Return all data needed to render the Competition Control Panel.

    Parameters
    ----------
    org:
        An Organiser ORM instance.

    Returns
    -------
    dict with keys:
        current_round, cycle_number, active_players, eliminated_players,
        deadline_dt, automation_status, last_run_at, last_run_ago,
        reminders_24h, reminders_4h, reminders_2h,
        last_completed_round, round_snapshot
    """
    # ── Game status ─────────────────────────────────────────────────────────
    active_players = org.players.filter_by(status="active").count()
    eliminated_players = org.players.filter_by(status="eliminated").count()

    current_round = (
        org.rounds.filter_by(status="active")
        .order_by(Round.round_number.desc())
        .first()
    )
    if not current_round:
        current_round = (
            org.rounds.filter_by(status="pending")
            .order_by(Round.round_number.asc())
            .first()
        )

    cycle_number = current_round.cycle_number if current_round else None

    # ── Next deadline ────────────────────────────────────────────────────────
    deadline_dt = None
    if current_round and current_round.status == "active" and current_round.first_kickoff_at:
        deadline_dt = current_round.first_kickoff_at

    # ── Automation health ────────────────────────────────────────────────────
    last_run = (
        AutomationRun.query
        .filter(
            (AutomationRun.organiser_id == org.id) | (AutomationRun.organiser_id.is_(None))
        )
        .order_by(AutomationRun.started_at.desc())
        .first()
    )

    automation_status = "unknown"
    last_run_at = None
    last_run_ago = None

    if last_run:
        last_run_at = last_run.completed_at or last_run.started_at
        automation_status = "warning" if last_run.status in ("warn", "failed") else "running"
        last_run_ago = _humanise_ago(last_run_at)

    # ── Reminder indicators ──────────────────────────────────────────────────
    reminders_24h = reminders_4h = reminders_2h = False
    if current_round and current_round.status == "active":
        rid = current_round.id
        reminders_24h = _has_reminder(rid, "24_hour")
        reminders_4h = _has_reminder(rid, "4_hour")
        reminders_2h = _has_reminder(rid, "2_hour")

    # ── Round snapshot (last completed round) ─────────────────────────────
    last_completed_round = (
        org.rounds.filter_by(status="completed")
        .order_by(Round.round_number.desc())
        .first()
    )

    round_snapshot = None
    if last_completed_round:
        round_snapshot = _quick_round_snapshot(last_completed_round)

    return {
        "current_round": current_round,
        "cycle_number": cycle_number,
        "active_players": active_players,
        "eliminated_players": eliminated_players,
        "deadline_dt": deadline_dt,
        "automation_status": automation_status,
        "last_run_at": last_run_at,
        "last_run_ago": last_run_ago,
        "reminders_24h": reminders_24h,
        "reminders_4h": reminders_4h,
        "reminders_2h": reminders_2h,
        "last_completed_round": last_completed_round,
        "round_snapshot": round_snapshot,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _has_reminder(round_id: int, reminder_type: str) -> bool:
    return (
        ReminderSchedule.query
        .filter_by(round_id=round_id, reminder_type=reminder_type)
        .first()
    ) is not None


def _quick_round_snapshot(round_obj) -> dict:
    """Return most-picked-team and biggest-eliminator for a completed round."""
    picks = Pick.query.filter_by(round_id=round_obj.id).all()

    pick_counter: Counter[str] = Counter()
    elim_counter: Counter[str] = Counter()

    for pick in picks:
        team = pick.team_picked or "Unknown"
        pick_counter[team] += 1
        if pick.is_eliminated:
            elim_counter[team] += 1

    most_picked_team = pick_counter.most_common(1)[0][0] if pick_counter else None
    biggest_eliminator = elim_counter.most_common(1)[0][0] if elim_counter else None

    return {
        "round_number": round_obj.round_number,
        "most_picked_team": most_picked_team,
        "biggest_eliminator": biggest_eliminator,
    }


def _humanise_ago(dt: datetime) -> str:
    """Return a human-friendly 'X ago' string for a past datetime."""
    if dt is None:
        return "unknown"
    now = datetime.utcnow()
    # Strip tzinfo if present so subtraction works
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    diff = now - dt
    total_seconds = int(diff.total_seconds())
    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        m = total_seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if total_seconds < 86400:
        h = total_seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = total_seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"
