"""
Admin operations Blueprint.

Thin HTTP handlers that delegate business logic to the service layer.
These routes replace the large embedded handlers that were in app.py:
  - manual_process_results  (~295 lines → ~80 lines here)
  - finalize_round          (~240 lines → ~90 lines here)
  - get_round_readiness     (~20 lines → unchanged but imported cleanly)
  - start_new_game          (~145 lines → unchanged, still delegates to _auto_create_rollover_round)

URL paths are unchanged so no frontend updates are needed.
"""
from __future__ import annotations

import logging
import traceback

from flask import Blueprint, request, jsonify, current_app

from lms_automation.models import db, Round, Fixture, Pick, Player
from lms_automation.team_utils import normalize_team_name
from lms_automation.eligibility import get_eligible_players_for_round
from lms_automation.services.markers import has_marker, add_marker
from lms_automation.services.round_lifecycle import (
    evaluate_game_state_after_round,
    check_round_readiness,
)
from lms_automation.routes.utils import admin_required
from lms_automation.services.audit import log_admin_action

logger = logging.getLogger(__name__)

admin_ops_bp = Blueprint("admin_ops", __name__)


def _get_announce_callback():
    """Return scheduler.announce_round_now if the scheduler is available."""
    try:
        from lms_automation.scheduler import scheduler
        return scheduler.announce_round_now
    except Exception:
        return None


# ---------------------------------------------------------------------------
# /api/admin/round-readiness/<round_id>
# ---------------------------------------------------------------------------

@admin_ops_bp.route("/api/admin/round-readiness/<int:round_id>", methods=["GET"])
@admin_required
def get_round_readiness(round_id: int):
    readiness = check_round_readiness(round_id)
    return jsonify(readiness)


# ---------------------------------------------------------------------------
# /api/admin/process-results  (manual elimination trigger)
# ---------------------------------------------------------------------------

@admin_ops_bp.route("/api/admin/process-results", methods=["POST"])
@admin_required
def manual_process_results():
    """
    On-demand elimination processor for MANUAL_MODE operation.

    Query params:
      update_fixtures  '1'|'true' — sync fixture scores from Football API first
      round_id         int        — process only this round (default: all active)
    """
    logger.info("=" * 60)
    logger.info("=== MANUAL PROCESS RESULTS START ===")
    logger.info("=" * 60)

    try:
        update_fixtures = request.args.get("update_fixtures", "0") in ("1", "true", "True")
        specific_round_id = request.args.get("round_id", type=int)

        result = {
            "success": True,
            "rounds_processed": [],
            "eliminations": [],
            "winners": [],
            "fixture_updates": 0,
            "errors": [],
            "summary": {},
        }

        # ------------------------------------------------------------------
        # Step 1: Optionally sync fixture scores from Football API
        # ------------------------------------------------------------------
        if update_fixtures:
            logger.info("Step 1: Updating fixtures from Football API...")
            try:
                from lms_automation.football_api import FootballDataAPI
                api = FootballDataAPI()

                if specific_round_id:
                    rounds_to_update = Round.query.filter_by(id=specific_round_id).all()
                else:
                    rounds_to_update = Round.query.filter(
                        Round.status.in_(["active", "pending"])
                    ).all()

                status_map = {
                    "FINISHED": "completed",
                    "IN_PLAY": "live",
                    "PAUSED": "live",
                    "POSTPONED": "postponed",
                    "CANCELLED": "postponed",
                    "SCHEDULED": "scheduled",
                    "TIMED": "scheduled",
                }

                for round_obj in rounds_to_update:
                    if not round_obj.pl_matchday:
                        continue
                    season_param = (
                        str(round_obj.get_api_season_year())
                        if round_obj.get_api_season_year()
                        else None
                    )
                    api_data = api.get_premier_league_fixtures(
                        matchday=round_obj.pl_matchday, season=season_param
                    )
                    if not (api_data and "matches" in api_data):
                        continue

                    matches_by_id = {
                        str(m.get("id")): m
                        for m in api_data["matches"]
                        if m.get("id") is not None
                    }
                    for fixture in Fixture.query.filter_by(round_id=round_obj.id).all():
                        match = (
                            matches_by_id.get(str(fixture.event_id)) if fixture.event_id else None
                        )
                        if not match:
                            for candidate in api_data["matches"]:
                                if (
                                    candidate.get("homeTeam", {}).get("name") == fixture.home_team
                                    and candidate.get("awayTeam", {}).get("name") == fixture.away_team
                                ):
                                    match = candidate
                                    break
                        if not match:
                            continue

                        score = match.get("score", {}).get("fullTime", {})
                        home_score = score.get("home")
                        away_score = score.get("away")
                        new_status = status_map.get(match.get("status", ""), fixture.status)

                        if home_score is not None and away_score is not None:
                            if (
                                fixture.home_score != home_score
                                or fixture.away_score != away_score
                                or fixture.status != new_status
                            ):
                                fixture.home_score = home_score
                                fixture.away_score = away_score
                                fixture.status = new_status
                                result["fixture_updates"] += 1
                                logger.info(
                                    "Updated fixture: %s %s-%s %s (%s)",
                                    fixture.home_team,
                                    home_score,
                                    away_score,
                                    fixture.away_team,
                                    new_status,
                                )

                db.session.commit()
                logger.info("Fixture updates complete: %s updated", result["fixture_updates"])
            except Exception as e:
                logger.error("Error updating fixtures: %s", e)
                result["errors"].append(f"Fixture update error: {e}")

        # ------------------------------------------------------------------
        # Step 2: Process eliminations for active rounds
        # ------------------------------------------------------------------
        logger.info("Step 2: Processing eliminations...")

        if specific_round_id:
            active_rounds = Round.query.filter_by(id=specific_round_id).all()
        else:
            active_rounds = Round.query.filter_by(status="active").all()

        logger.info("Found %s round(s) to process", len(active_rounds))

        for round_obj in active_rounds:
            logger.info("Processing Round %s (id=%s)", round_obj.round_number, round_obj.id)
            round_result = {
                "round_id": round_obj.id,
                "round_number": round_obj.round_number,
                "status": "skipped",
                "reason": None,
                "eliminations": [],
                "winners": [],
            }

            fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
            if not fixtures:
                round_result["reason"] = "no_fixtures"
                result["errors"].append(f"Round {round_obj.round_number}: no fixtures")
                continue

            if not all(f.status == "completed" for f in fixtures):
                pending = [f for f in fixtures if f.status != "completed"]
                round_result["reason"] = f"{len(pending)}_fixtures_not_completed"
                result["errors"].append(
                    f"Round {round_obj.round_number}: {len(pending)} fixtures not completed"
                )
                continue

            eligible_players = get_eligible_players_for_round(round_obj)
            picks = Pick.query.filter_by(round_id=round_obj.id).all()

            if len(picks) == 0:
                round_result["reason"] = "no_picks"
                result["errors"].append(f"Round {round_obj.round_number}: no picks submitted")
                continue

            if len(picks) < len(eligible_players):
                round_result["reason"] = f"picks_incomplete ({len(picks)}/{len(eligible_players)})"
                result["errors"].append(
                    f"Round {round_obj.round_number}: {len(picks)}/{len(eligible_players)} picks"
                )
                continue

            # Build fixture outcome map
            fixture_teams: dict[str, bool] = {}
            for fx in fixtures:
                home_norm = normalize_team_name(fx.home_team)
                away_norm = normalize_team_name(fx.away_team)
                if fx.home_score > fx.away_score:
                    fixture_teams[home_norm] = True
                    fixture_teams[away_norm] = False
                elif fx.away_score > fx.home_score:
                    fixture_teams[home_norm] = False
                    fixture_teams[away_norm] = True
                else:
                    fixture_teams[home_norm] = False
                    fixture_teams[away_norm] = False

            for pick in picks:
                pick_norm = normalize_team_name(pick.team_picked)
                pick.is_winner = fixture_teams.get(pick_norm, False)

            unevaluated = [p for p in picks if p.is_winner is None]
            if unevaluated:
                round_result["reason"] = f"{len(unevaluated)}_unevaluated_picks"
                result["errors"].append(
                    f"Round {round_obj.round_number}: {len(unevaluated)} picks couldn't be evaluated"
                )
                continue

            for pick in picks:
                if pick.is_winner is False and not pick.is_eliminated:
                    pick.is_eliminated = True
                    pick.player.status = "eliminated"
                    info = {
                        "player_id": pick.player_id,
                        "player_name": pick.player.name,
                        "team_picked": pick.team_picked,
                        "round_number": round_obj.round_number,
                    }
                    round_result["eliminations"].append(info)
                    result["eliminations"].append(info)
                    logger.info("ELIMINATED: %s (picked %s)", pick.player.name, pick.team_picked)
                elif pick.is_winner is True:
                    info = {
                        "player_id": pick.player_id,
                        "player_name": pick.player.name,
                        "team_picked": pick.team_picked,
                        "round_number": round_obj.round_number,
                    }
                    round_result["winners"].append(info)
                    result["winners"].append(info)

            round_obj.status = "completed"
            round_result["status"] = "completed"
            add_marker(round_obj, "results_sent")

            result["rounds_processed"].append(round_result)
            logger.info(
                "Round %s: COMPLETED (winners=%s, eliminated=%s)",
                round_obj.round_number,
                len(round_result["winners"]),
                len(round_result["eliminations"]),
            )

            game_state = evaluate_game_state_after_round(
                round_obj, announce_callback=_get_announce_callback()
            )
            round_result["game_state"] = game_state
            logger.info("Game state after Round %s: %s", round_obj.round_number, game_state)

        db.session.commit()

        total_active = Player.query.filter_by(status="active").count()
        total_eliminated = Player.query.filter_by(status="eliminated").count()
        total_winner = Player.query.filter_by(status="winner").count()
        last_game_state = (
            result["rounds_processed"][-1].get("game_state", "unknown")
            if result["rounds_processed"]
            else None
        )

        result["summary"] = {
            "total_active_players": total_active,
            "total_eliminated_players": total_eliminated,
            "total_winners": total_winner,
            "rounds_completed": len(result["rounds_processed"]),
            "total_eliminations_this_run": len(result["eliminations"]),
            "fixture_updates": result["fixture_updates"],
            "last_game_state": last_game_state,
        }

        logger.info("=== MANUAL PROCESS RESULTS COMPLETE === summary=%s", result["summary"])
        log_admin_action(
            "process_results",
            "success",
            rounds_completed=result["summary"]["rounds_completed"],
            eliminations=result["summary"]["total_eliminations_this_run"],
            fixture_updates=result["summary"]["fixture_updates"],
            last_game_state=result["summary"]["last_game_state"],
        )
        return jsonify(result)

    except Exception as e:
        logger.error("Error in manual_process_results: %s\n%s", e, traceback.format_exc())
        log_admin_action("process_results", "failure", error=str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# /api/admin/finalize-round
# ---------------------------------------------------------------------------

@admin_ops_bp.route("/api/admin/finalize-round", methods=["POST"])
@admin_required
def finalize_round():
    """
    Finalize a round after manual score entry.

    Runs the full end-of-round pipeline:
      1. Guard: fixtures + picks exist, relevant fixtures ready
      2. Build fixture outcome map and evaluate all picks
      3. Mark round completed, add results_sent marker
      4. Call evaluate_game_state_after_round (winner / rollover / continue)

    Request body: {"round_id": <int>}
    """
    try:
        data = request.get_json(silent=True) or {}
        round_id = data.get("round_id")

        if not round_id:
            return jsonify({"success": False, "error": "round_id is required"}), 400

        round_obj = Round.query.get(round_id)
        if not round_obj:
            return jsonify({"success": False, "error": f"Round {round_id} not found"}), 404

        logger.info(
            "FINALIZE ROUND START: round_id=%s cycle=%s round=%s",
            round_id,
            round_obj.cycle_number,
            round_obj.round_number,
        )

        # GUARD 1: fixtures
        fixtures = Fixture.query.filter_by(round_id=round_id).all()
        if not fixtures:
            return jsonify({"success": False, "error": "Round has no fixtures"}), 400

        # GUARD 2: picks
        picks = Pick.query.filter_by(round_id=round_id).all()
        if not picks:
            return jsonify({"success": False, "error": "Round has no picks"}), 400

        # GUARD 3: relevant fixtures ready
        readiness = check_round_readiness(round_id)
        if not readiness["ready"]:
            return jsonify({
                "success": False,
                "error": readiness["message"],
                "readiness_details": {
                    "picked_teams": readiness["picked_teams"],
                    "relevant_fixtures_count": len(readiness["relevant_fixtures"]),
                    "pending_relevant": readiness["pending_relevant"],
                },
            }), 400

        # Mark scored fixtures as completed
        for fx in fixtures:
            if fx.home_score is not None and fx.away_score is not None and fx.status != "completed":
                fx.status = "completed"

        already_finalized = has_marker(round_obj, "results_sent")
        if already_finalized:
            logger.info("Round %s already finalized (results_sent marker present)", round_id)

        # Build fixture outcome map (only scored fixtures)
        fixture_outcomes: dict[str, bool] = {}
        for fx in fixtures:
            if fx.home_score is None or fx.away_score is None:
                continue
            home_norm = normalize_team_name(fx.home_team)
            away_norm = normalize_team_name(fx.away_team)
            if fx.home_score > fx.away_score:
                fixture_outcomes[home_norm] = True
                fixture_outcomes[away_norm] = False
            elif fx.away_score > fx.home_score:
                fixture_outcomes[home_norm] = False
                fixture_outcomes[away_norm] = True
            else:
                fixture_outcomes[home_norm] = False
                fixture_outcomes[away_norm] = False

        eliminations = []
        survivors = []

        for pick in picks:
            pick_norm = normalize_team_name(pick.team_picked)
            is_winner = fixture_outcomes.get(pick_norm, False)
            pick.is_winner = is_winner

            if is_winner:
                pick.is_eliminated = False
                if pick.player.status != "active":
                    logger.info(
                        "WINNER STATUS FIX: %s was '%s', setting to 'active'",
                        pick.player.name,
                        pick.player.status,
                    )
                    pick.player.status = "active"
                survivors.append({
                    "player_id": pick.player_id,
                    "player_name": pick.player.name,
                    "team_picked": pick.team_picked,
                })
            else:
                pick.is_eliminated = True
                if pick.player.status != "eliminated":
                    pick.player.status = "eliminated"
                    logger.info("ELIMINATED: %s (picked %s)", pick.player.name, pick.team_picked)
                eliminations.append({
                    "player_id": pick.player_id,
                    "player_name": pick.player.name,
                    "team_picked": pick.team_picked,
                })

        round_obj.status = "completed"
        if not already_finalized:
            add_marker(round_obj, "results_sent")

        db.session.commit()

        outcome = evaluate_game_state_after_round(
            round_obj, announce_callback=_get_announce_callback()
        )
        logger.info("FINALIZE ROUND OUTCOME: %s", outcome)
        log_admin_action(
            "finalize_round",
            "success",
            round_id=round_id,
            outcome=outcome,
            eliminations=len(eliminations),
            survivors=len(survivors),
        )

        new_round_created = False
        new_cycle_number = round_obj.cycle_number or 1
        new_round_number = round_obj.round_number

        if outcome == "rollover":
            next_cycle = new_cycle_number + 1
            new_round = Round.query.filter_by(cycle_number=next_cycle, round_number=1).first()
            if new_round:
                new_round_created = True
                new_cycle_number = next_cycle
                new_round_number = 1

        active_count = Player.query.filter_by(status="active").count()
        eliminated_count = Player.query.filter_by(status="eliminated").count()
        winner_count = Player.query.filter_by(status="winner").count()

        return jsonify({
            "success": True,
            "outcome": outcome,
            "new_round_created": new_round_created,
            "cycle_number": new_cycle_number,
            "round_number": new_round_number,
            "eliminations": eliminations,
            "survivors": survivors,
            "summary": {
                "active_players": active_count,
                "eliminated_players": eliminated_count,
                "winners": winner_count,
                "total_eliminations_this_round": len(eliminations),
                "total_survivors_this_round": len(survivors),
            },
        })

    except Exception as e:
        logger.error("Error in finalize_round: %s\n%s", e, traceback.format_exc())
        log_admin_action("finalize_round", "failure", error=str(e))
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# /api/admin/notifications/status
# ---------------------------------------------------------------------------

@admin_ops_bp.route("/api/admin/notifications/status", methods=["GET"])
@admin_required
def get_notifications_status():
    """
    Delivery health summary for the notification outbox.

    Returns counts for each status and up to 20 recent dead rows so the admin
    can assess failures without DB shell access.
    """
    from lms_automation.models import NotificationOutbox
    from lms_automation.services.notifications import _backoff_elapsed

    pending_rows = NotificationOutbox.query.filter_by(status="pending").all()
    sent_count = NotificationOutbox.query.filter_by(status="sent").count()
    dead_count = NotificationOutbox.query.filter_by(status="dead").count()

    # Split pending into due-now vs. waiting for backoff
    due_now = sum(1 for r in pending_rows if _backoff_elapsed(r))
    waiting = len(pending_rows) - due_now

    recent_dead = (
        NotificationOutbox.query
        .filter_by(status="dead")
        .order_by(NotificationOutbox.id.desc())
        .limit(20)
        .all()
    )

    return jsonify({
        "pending": len(pending_rows),
        "pending_due_now": due_now,
        "pending_waiting_backoff": waiting,
        "sent": sent_count,
        "dead": dead_count,
        "recent_dead": [
            {
                "id": r.id,
                "telegram_id": r.telegram_id,
                "round_id": r.round_id,
                "idempotency_key": r.idempotency_key,
                "attempts": r.attempts,
                "max_attempts": r.max_attempts,
                "last_error": r.last_error,
                "last_attempt_at": r.last_attempt_at.isoformat() if r.last_attempt_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "message_preview": r.message[:120],
            }
            for r in recent_dead
        ],
    })


# ---------------------------------------------------------------------------
# /api/admin/notifications/requeue
# ---------------------------------------------------------------------------

@admin_ops_bp.route("/api/admin/notifications/requeue", methods=["POST"])
@admin_required
def requeue_notifications():
    """
    Reset dead outbox rows back to pending for re-delivery.

    Request body (all optional)::

        {"round_id": <int>}   — requeue dead rows for this round only
        {}                    — requeue all dead rows

    Requeued rows have attempts/last_error/last_attempt_at cleared so the
    delivery worker treats them as fresh (no backoff wait on first attempt).
    """
    try:
        from lms_automation.services.notifications import requeue_dead_notifications
        data = request.get_json(silent=True) or {}
        round_id = data.get("round_id")

        count = requeue_dead_notifications(round_id=round_id)
        log_admin_action("notifications_requeue", "success", requeued=count, round_id=round_id)
        return jsonify({"success": True, "requeued": count})

    except Exception as e:
        logger.error("Error in requeue_notifications: %s", e)
        log_admin_action("notifications_requeue", "failure", error=str(e))
        return jsonify({"success": False, "error": str(e)}), 500
