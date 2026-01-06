"""Admin facing orchestration helpers."""

from __future__ import annotations

from typing import Any

from lms_automation.models import Player, Round


def dashboard_context() -> dict[str, Any]:
    players = Player.query.order_by(Player.name).all()
    current_round = Round.query.filter_by(status='active').first()
    return {
        'players': players,
        'current_round': current_round,
    }


__all__ = ["dashboard_context"]
