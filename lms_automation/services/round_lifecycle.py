"""
Round lifecycle domain service.

This is the SINGLE SOURCE OF TRUTH for post-round game-state transitions
and round creation logic.  It replaces:

  - LMSScheduler._evaluate_game_state_after_round (scheduler.py)
  - _evaluate_game_state_after_round_standalone    (app.py)   ← DELETE THAT
  - LMSScheduler._create_rollover_round            (scheduler.py)
  - LMSScheduler._create_next_round               (scheduler.py)
  - LMSScheduler._populate_fixtures_for_rollover_round (scheduler.py)
  - _auto_create_rollover_round                    (app.py)   ← DELETE THAT

Contract:
  - All functions must be called inside a Flask app_context.
  - Functions do NOT enter app_context themselves.
  - Functions use db.session directly but do NOT commit unless their
    docstring says they do.
  - announce_callback, when provided, is called with the newly-created
    round (e.g. scheduler.announce_round_now).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from lms_automation.models import db, Round, Fixture, Pick, Player, PickToken
from lms_automation.football_api import FootballDataAPI
from lms_automation.team_utils import normalize_team_name
from lms_automation.services.markers import has_marker, add_marker
from lms_automation.services import telegram_dispatch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Game-state evaluation
# ---------------------------------------------------------------------------

def evaluate_game_state_after_round(
    completed_round: Round,
    announce_callback: Optional[Callable] = None,
) -> str:
    """
    Post-round game-state evaluation — the single source of truth.

    DEFINITIONS:
      WINNER   — exactly 1 Player.status == 'active' after round completion.
      ROLLOVER — 0 active players (everyone eliminated).
      CONTINUE — 2+ active players (normal progression).

    Parameters:
      completed_round   — must already be committed with status='completed'.
      announce_callback — optional callable(round_obj) triggered on any newly
                          created round (e.g. scheduler.announce_round_now).

    Returns: 'winner' | 'rollover' | 'continue'
    """
    logger.info("=" * 60)
    logger.info("=== GAME STATE EVALUATION START ===")
    logger.info(
        "Evaluating after Round %s (cycle=%s)",
        completed_round.round_number,
        completed_round.cycle_number,
    )

    active_players = Player.query.filter_by(status="active").all()
    active_count = len(active_players)
    eliminated_count = Player.query.filter_by(status="eliminated").count()
    winner_count = Player.query.filter_by(status="winner").count()

    outcome = "winner" if active_count == 1 else ("rollover" if active_count == 0 else "continue")

    logger.info(
        "GAME STATE EVAL: round_id=%s cycle=%s round=%s "
        "active_count=%s eliminated_count=%s winner_count=%s outcome=%s",
        completed_round.id,
        completed_round.cycle_number,
        completed_round.round_number,
        active_count,
        eliminated_count,
        winner_count,
        outcome,
    )

    # ------------------------------------------------------------------
    # WINNER — exactly 1 player still active
    # ------------------------------------------------------------------
    if active_count == 1:
        winner = active_players[0]
        logger.info("=" * 60)
        logger.info("GAME STATE: winner — %s (id=%s)", winner.name, winner.id)
        logger.info("=" * 60)

        if has_marker(completed_round, "game_winner_announced"):
            logger.info("Winner already announced (idempotent), skipping notifications")
            return "winner"

        winner.status = "winner"
        telegram_dispatch.send_winner_notification(winner, completed_round)
        # Commits: winner.status + game_winner_announced marker + queued outbox rows.
        # send_winner_notification now only enqueues (no longer commits internally).
        db.session.commit()

        logger.info("=== GAME STATE EVALUATION COMPLETE (winner) ===")
        return "winner"

    # ------------------------------------------------------------------
    # ROLLOVER — all players eliminated
    # ------------------------------------------------------------------
    elif active_count == 0:
        logger.info("=" * 60)
        logger.info("GAME STATE: rollover — all players eliminated")
        logger.info("=" * 60)

        next_cycle = (completed_round.cycle_number or 1) + 1
        rollover_marker = f"rollover_processed_cycle_{next_cycle}"

        if has_marker(completed_round, rollover_marker):
            logger.info("Rollover already processed for cycle %s (idempotent)", next_cycle)
            return "rollover"

        add_marker(completed_round, rollover_marker)

        # Reset ALL players to active for new cycle
        Player.query.update({"status": "active"}, synchronize_session=False)
        logger.info("All players reset to status='active'")

        telegram_dispatch.send_rollover_notification(next_cycle)
        telegram_dispatch.send_admin_rollover_notification(next_cycle, completed_round)

        new_round = create_rollover_round(completed_round, next_cycle)
        if new_round:
            db.session.commit()
            add_marker(completed_round, f"next_round_created_cycle_{next_cycle}")

            if announce_callback:
                try:
                    announce_callback(new_round.id)
                except Exception as e:
                    logger.error("announce_callback failed for round %s: %s", new_round.id, e)

            # Activate immediately if prerequisites are met — replicates original
            # _trigger_rollover_automation step 2 so the round enters 'active'
            # without waiting for the next round_progression_orchestrator tick.
            # Guards: tokens_generated marker + at least one fixture + still pending.
            db.session.refresh(new_round)
            fixtures_exist = Fixture.query.filter_by(round_id=new_round.id).count() > 0
            if (
                new_round.status == "pending"
                and has_marker(new_round, "tokens_generated")
                and fixtures_exist
            ):
                new_round.status = "active"
                db.session.commit()
                logger.info(
                    "ROLLOVER: Round %s activated immediately (tokens_generated=True, fixtures=%s)",
                    new_round.id,
                    fixtures_exist,
                )
            else:
                logger.info(
                    "ROLLOVER: Round %s not activated inline "
                    "(status=%s, tokens_generated=%s, fixtures=%s) — "
                    "orchestrator will activate on next tick",
                    new_round.id,
                    new_round.status,
                    has_marker(new_round, "tokens_generated"),
                    fixtures_exist,
                )

            logger.info(
                "New rollover round created: round_id=%s round_number=%s",
                new_round.id,
                new_round.round_number,
            )
        else:
            db.session.commit()

        logger.info("=== GAME STATE EVALUATION COMPLETE (rollover) ===")
        return "rollover"

    # ------------------------------------------------------------------
    # CONTINUE — 2+ players still active, normal progression
    # ------------------------------------------------------------------
    else:
        logger.info("=" * 60)
        logger.info("GAME STATE: continue — %s players still active", active_count)
        logger.info("=" * 60)

        current_cycle = completed_round.cycle_number or 1
        next_round_number = completed_round.round_number + 1

        # End of cycle (Round 20 with 2+ survivors)
        if completed_round.round_number == 20 and active_count >= 2:
            next_cycle = current_cycle + 1
            logger.info(
                "END OF CYCLE %s: %s survivors advance to Cycle %s",
                current_cycle,
                active_count,
                next_cycle,
            )
            telegram_dispatch.send_cycle_complete_notification(active_players, next_cycle)
            next_round_number = 1
            current_cycle = next_cycle

        new_round = create_next_round(completed_round, next_round_number, current_cycle)
        if new_round:
            logger.info(
                "AUTO-CREATED: Round %s (Cycle %s, PL Matchday %s)",
                new_round.round_number,
                new_round.cycle_number,
                new_round.pl_matchday,
            )

        logger.info("=== GAME STATE EVALUATION COMPLETE (continue) ===")
        return "continue"


# ---------------------------------------------------------------------------
# Round creation helpers
# ---------------------------------------------------------------------------

def create_rollover_round(completed_round: Round, next_cycle: int) -> Optional[Round]:
    """
    Create a new pending Round for the first round of the next cycle.

    Idempotent: 'rollover_seeded_cycle_{next_cycle}' marker prevents duplicates.
    Does NOT commit — caller commits.
    Returns the new Round object, or None if already exists / on error.
    """
    rollover_marker = f"rollover_seeded_cycle_{next_cycle}"

    existing = Round.query.filter(
        Round.special_note.isnot(None),
        Round.special_note.contains(rollover_marker),
    ).first()

    if existing:
        logger.info(
            "ROLLOVER: Round for Cycle %s already seeded (round_id=%s), skipping",
            next_cycle,
            existing.id,
        )
        return None

    try:
        current_matchday = completed_round.pl_matchday or 1
        next_matchday = min(current_matchday + 1, 38)
        if next_matchday == 38 and current_matchday == 38:
            logger.warning("ROLLOVER: End of season (matchday 38), using matchday 38 again")

        new_round = Round(
            round_number=1,
            pl_matchday=next_matchday,
            cycle_number=next_cycle,
            status="pending",
            special_note=rollover_marker,
        )

        db.session.add(new_round)
        db.session.flush()

        logger.info(
            "ROLLOVER: Created round_id=%s round_number=1 cycle=%s matchday=%s",
            new_round.id,
            next_cycle,
            next_matchday,
        )

        fixtures_created = populate_fixtures_for_round(new_round)
        if fixtures_created == 0:
            logger.warning(
                "ROLLOVER: Round 1 created but no fixtures for matchday %s", next_matchday
            )
        else:
            logger.info("ROLLOVER: Created %s fixtures", fixtures_created)

        return new_round

    except Exception as e:
        import traceback
        logger.error("ROLLOVER: Error creating round: %s\n%s", e, traceback.format_exc())
        db.session.rollback()
        return None


def create_next_round(
    completed_round: Round,
    next_round_number: int,
    cycle_number: int,
) -> Optional[Round]:
    """
    Auto-create the next round for normal game progression (2+ survivors).

    Idempotent: returns None if the round already exists.
    Commits internally (needs flush→fixture insert→commit sequence).
    Returns the new Round object, or None if already exists / on error.
    """
    try:
        existing = Round.query.filter_by(
            cycle_number=cycle_number,
            round_number=next_round_number,
        ).first()

        if existing:
            logger.info(
                "Round %s (Cycle %s) already exists (id=%s), skipping",
                next_round_number,
                cycle_number,
                existing.id,
            )
            return None

        current_matchday = completed_round.pl_matchday or 1
        next_matchday = min(current_matchday + 1, 38)

        new_round = Round(
            round_number=next_round_number,
            pl_matchday=next_matchday,
            cycle_number=cycle_number,
            status="pending",
        )

        db.session.add(new_round)
        db.session.flush()

        logger.info(
            "AUTO-CREATE: Round %s (Cycle %s, PL Matchday %s) created (id=%s)",
            next_round_number,
            cycle_number,
            next_matchday,
            new_round.id,
        )

        fixtures_created = populate_fixtures_for_round(new_round)
        if fixtures_created > 0:
            logger.info("AUTO-CREATE: Added %s fixtures", fixtures_created)
        else:
            logger.warning("AUTO-CREATE: Round created without fixtures (API may be unavailable)")

        db.session.commit()
        return new_round

    except Exception as e:
        import traceback
        logger.error("AUTO-CREATE: Error creating Round %s: %s\n%s", next_round_number, e, traceback.format_exc())
        db.session.rollback()
        return None


def populate_fixtures_for_round(round_obj: Round, api: Optional[FootballDataAPI] = None) -> int:
    """
    Fetch and insert fixtures for *round_obj* from the Football API.

    Does NOT commit — caller commits.
    Returns the count of Fixture rows added.
    """
    try:
        if api is None:
            try:
                api = FootballDataAPI()
            except Exception as e:
                logger.warning("Football API unavailable: %s", e)
                return 0

        season_param = None
        if hasattr(round_obj, "get_api_season_year"):
            season_year = round_obj.get_api_season_year()
            if season_year:
                season_param = str(season_year)

        fixtures_data = api.get_premier_league_fixtures(
            matchday=round_obj.pl_matchday,
            season=season_param,
        )
        formatted = api.format_fixtures_for_db(fixtures_data, round_obj.pl_matchday)

        if not formatted:
            return 0

        earliest_kickoff = None
        count = 0
        for fx in formatted:
            fixture = Fixture(
                round_id=round_obj.id,
                event_id=fx.get("event_id"),
                home_team=fx["home_team"],
                away_team=fx["away_team"],
                date=fx.get("date"),
                time=fx.get("time"),
                home_score=fx.get("home_score"),
                away_score=fx.get("away_score"),
                status=fx.get("status", "scheduled"),
            )
            db.session.add(fixture)
            count += 1

            if fx.get("date") and fx.get("time"):
                dt = datetime.combine(fx["date"], fx["time"])
                if earliest_kickoff is None or dt < earliest_kickoff:
                    earliest_kickoff = dt

        if earliest_kickoff:
            round_obj.first_kickoff_at = earliest_kickoff

        return count

    except Exception as e:
        logger.error("Error populating fixtures for round %s: %s", round_obj.id, e)
        return 0


# ---------------------------------------------------------------------------
# Round readiness check
# ---------------------------------------------------------------------------

def check_round_readiness(round_id: int) -> dict:
    """
    Check whether a round is ready to finalize based on RELEVANT fixtures only.

    "Relevant" means a fixture whose home_team or away_team matches a team
    that a player actually picked.  A round is ready when every relevant
    fixture has status='completed' with both scores present.

    Read-only and idempotent.

    Returns:
        dict with keys:
          ready              bool
          picked_teams       list[str]  (normalized)
          relevant_fixtures  list[dict]
          pending_relevant   list[dict]
          message            str
    """
    round_obj = Round.query.get(round_id)
    if not round_obj:
        return {
            "ready": False,
            "picked_teams": [],
            "relevant_fixtures": [],
            "pending_relevant": [],
            "message": f"Round {round_id} not found",
        }

    picks = Pick.query.filter_by(round_id=round_id).all()
    if not picks:
        logger.info(
            "ROUND READY CHECK: round_id=%s picked_teams=0 relevant_fixtures=0 "
            "pending_relevant=0 ready=false (no picks)",
            round_id,
        )
        return {
            "ready": False,
            "picked_teams": [],
            "relevant_fixtures": [],
            "pending_relevant": [],
            "message": "No picks in round - cannot finalize",
        }

    picked_teams_normalized = {
        normalize_team_name(p.team_picked) for p in picks if p.team_picked
    }

    if not picked_teams_normalized:
        return {
            "ready": False,
            "picked_teams": [],
            "relevant_fixtures": [],
            "pending_relevant": [],
            "message": "No valid teams picked in round",
        }

    fixtures = Fixture.query.filter_by(round_id=round_id).all()
    if not fixtures:
        return {
            "ready": False,
            "picked_teams": list(picked_teams_normalized),
            "relevant_fixtures": [],
            "pending_relevant": [],
            "message": "No fixtures in round",
        }

    relevant_fixtures = [
        fx
        for fx in fixtures
        if normalize_team_name(fx.home_team) in picked_teams_normalized
        or normalize_team_name(fx.away_team) in picked_teams_normalized
    ]

    pending_relevant = [
        {
            "fixture_id": fx.id,
            "home_team": fx.home_team,
            "away_team": fx.away_team,
            "status": fx.status,
            "has_scores": fx.home_score is not None and fx.away_score is not None,
        }
        for fx in relevant_fixtures
        if not (
            fx.status == "completed"
            and fx.home_score is not None
            and fx.away_score is not None
        )
    ]

    ready = len(pending_relevant) == 0 and len(relevant_fixtures) > 0

    logger.info(
        "ROUND READY CHECK: round_id=%s picked_teams=%s relevant_fixtures=%s "
        "pending_relevant=%s ready=%s",
        round_id,
        len(picked_teams_normalized),
        len(relevant_fixtures),
        len(pending_relevant),
        ready,
    )

    if ready:
        message = f"Round ready: all {len(relevant_fixtures)} relevant fixtures completed"
    elif len(relevant_fixtures) == 0:
        message = f"No fixtures involve the {len(picked_teams_normalized)} picked teams"
    else:
        pending_teams = set()
        for pf in pending_relevant:
            pending_teams.add(pf["home_team"])
            pending_teams.add(pf["away_team"])
        pending_teams_in_picks = pending_teams & picked_teams_normalized
        message = (
            f"{len(pending_relevant)} relevant fixture(s) still pending. "
            f"Waiting for: "
            f"{', '.join(sorted(pending_teams_in_picks)) if pending_teams_in_picks else 'fixtures to complete'}"
        )

    return {
        "ready": ready,
        "picked_teams": list(picked_teams_normalized),
        "relevant_fixtures": [
            {
                "fixture_id": fx.id,
                "home_team": fx.home_team,
                "away_team": fx.away_team,
                "status": fx.status,
                "home_score": fx.home_score,
                "away_score": fx.away_score,
            }
            for fx in relevant_fixtures
        ],
        "pending_relevant": pending_relevant,
        "message": message,
    }
