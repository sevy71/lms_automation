"""
Background Scheduler for LMS Automation
Handles periodic tasks for automated game management
"""

import os
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import Flask
from lms_automation.models import db, Round, Fixture, Pick, Player, ReminderSchedule, PickToken
from lms_automation.football_api import FootballDataAPI
from lms_automation.notifications import NotificationService
from lms_automation.team_utils import normalize_team_name, teams_match, find_matching_picks_for_team
import requests
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LMSScheduler:
    def __init__(self, app: Optional[Flask] = None):
        self.scheduler = BackgroundScheduler()
        self.app = app
        self.notification_service = NotificationService()
        self.telegram_bot_url = os.environ.get('TELEGRAM_BOT_URL', 'http://localhost:8080')

    def init_app(self, app: Flask):
        """Initialize scheduler with Flask app context"""
        self.app = app

    def _get_api(self) -> Optional[FootballDataAPI]:
        try:
            return FootballDataAPI()
        except Exception as e:
            logger.error("Football API unavailable: %s", e)
            return None

    def start(self):
        """Start the scheduler with all jobs"""
        if not self.scheduler.running:
            # Schedule jobs

            # Check for fixture updates every 30 minutes
            self.scheduler.add_job(
                func=self.update_fixture_results,
                trigger=IntervalTrigger(minutes=30),
                id='update_fixtures',
                name='Update fixture results from Football API',
                replace_existing=True
            )

            # Sync fixtures every 60 minutes
            self.scheduler.add_job(
                func=self.sync_fixtures,
                trigger=IntervalTrigger(minutes=60),
                id='sync_fixtures',
                name='Sync fixtures from Football API',
                replace_existing=True
            )

            # Process eliminations every hour
            self.scheduler.add_job(
                func=self.process_eliminations,
                trigger=IntervalTrigger(hours=1),
                id='process_eliminations',
                name='Process eliminations and check for rollover',
                replace_existing=True
            )

            # Send reminders every 15 minutes
            self.scheduler.add_job(
                func=self.send_due_reminders,
                trigger=IntervalTrigger(minutes=15),
                id='send_reminders',
                name='Send due reminders via Telegram',
                replace_existing=True
            )

            # Generate tokens for new rounds every hour
            self.scheduler.add_job(
                func=self.generate_round_tokens,
                trigger=IntervalTrigger(hours=1),
                id='generate_tokens',
                name='Generate pick tokens for active rounds',
                replace_existing=True
            )

            # Send initial round announcements every 30 minutes
            self.scheduler.add_job(
                func=self.send_new_round_announcements,
                trigger=IntervalTrigger(minutes=30),
                id='send_round_announcements',
                name='Send new round announcements to players via Telegram',
                replace_existing=True
            )

            # Apply missed picks at round deadline
            # Use shorter interval (5 min prod, 1 min test) to catch deadlines promptly
            autopick_interval_minutes = int(os.environ.get('AUTOPICK_INTERVAL_MINUTES', '5'))
            self.scheduler.add_job(
                func=self.apply_missed_picks,
                trigger=IntervalTrigger(minutes=autopick_interval_minutes),
                id='apply_missed_picks',
                name='Apply auto-picks for players who missed deadline',
                next_run_time=datetime.now(),
                replace_existing=True
            )
            logger.info(f"Auto-pick job configured with {autopick_interval_minutes}-minute interval")

            # Orchestrator job - ensures proper round progression
            self.scheduler.add_job(
                func=self.round_progression_orchestrator,
                trigger=IntervalTrigger(minutes=10),
                id='round_orchestrator',
                name='Orchestrate round progression (activate pending rounds with tokens)',
                next_run_time=datetime.now(),
                replace_existing=True
            )

            self.scheduler.start()
            logger.info("Scheduler started with all jobs configured")

            # Log all registered jobs for verification
            self._log_registered_jobs()

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    def update_fixture_results(self):
        """Poll Football API for fixture results and update database"""
        with self.app.app_context():
            try:
                logger.info("=== FIXTURE UPDATE JOB START ===")
                api = self._get_api()
                if not api:
                    logger.warning("Football API unavailable, skipping fixture updates")
                    return
                season = os.environ.get('FOOTBALL_SEASON') or os.environ.get('SEASON')

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) to check for fixture updates")

                if not active_rounds:
                    logger.info("No active rounds found, nothing to update")
                    return

                for round_obj in active_rounds:
                    if not round_obj.pl_matchday:
                        continue

                    api_data = api.get_premier_league_fixtures(
                        matchday=round_obj.pl_matchday,
                        season=season
                    )

                    if api_data and 'matches' in api_data:
                        matches_by_id = {
                            str(m.get('id')): m for m in api_data['matches'] if m.get('id') is not None
                        }
                        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()

                        for fixture in fixtures:
                            match = None
                            if fixture.event_id:
                                match = matches_by_id.get(str(fixture.event_id))
                            if not match:
                                for candidate in api_data['matches']:
                                    if (candidate.get('homeTeam', {}).get('name') == fixture.home_team and
                                        candidate.get('awayTeam', {}).get('name') == fixture.away_team):
                                        match = candidate
                                        break

                            if not match or match.get('status') != 'FINISHED':
                                continue

                            fixture.status = 'completed'
                            fixture.home_score = match.get('score', {}).get('fullTime', {}).get('home')
                            fixture.away_score = match.get('score', {}).get('fullTime', {}).get('away')

                            # Determine winner and update picks
                            self._update_picks_for_fixture(fixture)

                            logger.info(
                                "Updated fixture: %s %s - %s %s (Round %s)",
                                fixture.home_team,
                                fixture.home_score,
                                fixture.away_score,
                                fixture.away_team,
                                round_obj.round_number
                            )

                db.session.commit()
                logger.info("=== FIXTURE UPDATE JOB COMPLETE ===")

            except Exception as e:
                logger.error(f"Error updating fixtures: {e}")
                db.session.rollback()

    def sync_fixtures(self):
        """Sync fixtures from Football API without changing round status"""
        with self.app.app_context():
            try:
                logger.info("Syncing fixtures from Football API...")
                api = self._get_api()
                if not api:
                    return
                season = os.environ.get('FOOTBALL_SEASON') or os.environ.get('SEASON')

                rounds = Round.query.filter(Round.status.in_(['pending', 'active'])).all()
                created = 0
                updated = 0
                skipped = 0

                for round_obj in rounds:
                    if not round_obj.pl_matchday:
                        skipped += 1
                        continue

                    fixtures_data = api.get_premier_league_fixtures(
                        matchday=round_obj.pl_matchday,
                        season=season
                    )
                    formatted = api.format_fixtures_for_db(fixtures_data, round_obj.pl_matchday)
                    if not formatted:
                        continue

                    for fx in formatted:
                        event_id = fx.get('event_id') or None
                        fixture = None

                        if event_id:
                            fixture = Fixture.query.filter_by(
                                round_id=round_obj.id,
                                event_id=event_id
                            ).first()
                        if not fixture:
                            fixture = Fixture.query.filter_by(
                                round_id=round_obj.id,
                                home_team=fx['home_team'],
                                away_team=fx['away_team'],
                                date=fx['date']
                            ).first()

                        if fixture:
                            fixture.event_id = event_id or fixture.event_id
                            fixture.home_team = fx['home_team']
                            fixture.away_team = fx['away_team']
                            fixture.date = fx['date']
                            fixture.time = fx['time']
                            fixture.home_score = fx['home_score']
                            fixture.away_score = fx['away_score']
                            fixture.status = fx['status']
                            updated += 1
                        else:
                            fixture = Fixture(
                                round_id=round_obj.id,
                                event_id=event_id,
                                home_team=fx['home_team'],
                                away_team=fx['away_team'],
                                date=fx['date'],
                                time=fx['time'],
                                home_score=fx['home_score'],
                                away_score=fx['away_score'],
                                status=fx['status']
                            )
                            db.session.add(fixture)
                            created += 1

                db.session.commit()
                logger.info(
                    "Fixture sync summary: created=%s updated=%s skipped_rounds=%s",
                    created,
                    updated,
                    skipped
                )
            except Exception as e:
                logger.error("Error syncing fixtures: %s", e)
                db.session.rollback()

    def _update_picks_for_fixture(self, fixture):
        """
        Update pick results based on fixture outcome.

        Uses normalized team name comparison to handle variations like:
        - "Aston Villa FC" vs "Aston Villa" vs "Villa"

        Competition rule: ONLY a WIN progresses. Draw or loss = eliminated.
        """
        if fixture.home_score is None or fixture.away_score is None:
            logger.debug(f"Fixture {fixture.home_team} vs {fixture.away_team}: scores not available yet")
            return

        # Normalize fixture team names for comparison
        home_team_canonical = normalize_team_name(fixture.home_team)
        away_team_canonical = normalize_team_name(fixture.away_team)

        # Get all picks for this round
        all_picks = Pick.query.filter_by(round_id=fixture.round_id).all()

        # Find picks that match this fixture (either team)
        home_picks = [p for p in all_picks if normalize_team_name(p.team_picked) == home_team_canonical]
        away_picks = [p for p in all_picks if normalize_team_name(p.team_picked) == away_team_canonical]

        picks_updated = 0

        # Determine match outcome
        if fixture.home_score > fixture.away_score:
            # Home team wins
            winning_canonical = home_team_canonical
            losing_canonical = away_team_canonical
            outcome = 'home_win'

            for pick in home_picks:
                if pick.is_winner is None:
                    pick.is_winner = True
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> WIN (home win {fixture.home_score}-{fixture.away_score})"
                    )

            for pick in away_picks:
                if pick.is_winner is None:
                    pick.is_winner = False
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> LOSS (away loss {fixture.away_score}-{fixture.home_score})"
                    )

        elif fixture.away_score > fixture.home_score:
            # Away team wins
            winning_canonical = away_team_canonical
            losing_canonical = home_team_canonical
            outcome = 'away_win'

            for pick in away_picks:
                if pick.is_winner is None:
                    pick.is_winner = True
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> WIN (away win {fixture.away_score}-{fixture.home_score})"
                    )

            for pick in home_picks:
                if pick.is_winner is None:
                    pick.is_winner = False
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> LOSS (home loss {fixture.home_score}-{fixture.away_score})"
                    )

        else:
            # DRAW - Competition rule: Draw = ELIMINATION (not a win)
            outcome = 'draw'

            for pick in home_picks:
                if pick.is_winner is None:
                    pick.is_winner = False  # Draw is NOT a win
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> ELIMINATED (draw {fixture.home_score}-{fixture.away_score}) - DRAW IS NOT A WIN"
                    )

            for pick in away_picks:
                if pick.is_winner is None:
                    pick.is_winner = False  # Draw is NOT a win
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> ELIMINATED (draw {fixture.away_score}-{fixture.home_score}) - DRAW IS NOT A WIN"
                    )

        logger.info(
            f"Fixture {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}: "
            f"outcome={outcome}, home_picks_count={len(home_picks)}, away_picks_count={len(away_picks)}, "
            f"picks_updated={picks_updated}"
        )

    def process_eliminations(self):
        """
        Process eliminations for completed rounds and check for rollover.

        This job:
        1. Checks if all fixtures in active rounds are completed
        2. For each completed round, marks picks with is_winner=False as eliminated
        3. Updates player.status to 'eliminated' for eliminated picks
        4. Detects unmatched picks (picks that couldn't be matched to any fixture)
        5. Checks for winner/rollover conditions
        """
        with self.app.app_context():
            try:
                logger.info("=" * 60)
                logger.info("=== ELIMINATION PROCESSING JOB START ===")
                logger.info("=" * 60)

                # Get active rounds with all fixtures completed
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Active rounds found: {len(active_rounds)}")

                if not active_rounds:
                    logger.info("No active rounds found")
                    logger.info("=== ELIMINATION PROCESSING JOB COMPLETE (no rounds) ===")
                    return

                rounds_processed = 0
                total_eliminations = 0
                total_winners = 0
                total_draws = 0

                for round_obj in active_rounds:
                    logger.info("-" * 40)
                    logger.info(f"Processing Round {round_obj.round_number} (round_id={round_obj.id})")

                    # Check if all fixtures are completed
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    completed_fixtures = [f for f in fixtures if f.status == 'completed']
                    draw_fixtures = [f for f in completed_fixtures if f.home_score == f.away_score]

                    logger.info(
                        f"  fixtures_total={len(fixtures)}, fixtures_completed={len(completed_fixtures)}, "
                        f"fixtures_draw={len(draw_fixtures)}"
                    )

                    if not fixtures:
                        logger.warning(f"  Round {round_obj.round_number}: No fixtures found, skipping")
                        continue

                    if not all(f.status == 'completed' for f in fixtures):
                        pending_fixtures = [f for f in fixtures if f.status != 'completed']
                        logger.info(
                            f"  Round {round_obj.round_number}: {len(pending_fixtures)} fixture(s) not completed, skipping"
                        )
                        for pf in pending_fixtures[:5]:  # Log first 5
                            logger.info(f"    - {pf.home_team} vs {pf.away_team} (status={pf.status})")
                        continue

                    # Build set of all teams in fixtures (normalized)
                    fixture_teams_canonical = set()
                    for fx in fixtures:
                        fixture_teams_canonical.add(normalize_team_name(fx.home_team))
                        fixture_teams_canonical.add(normalize_team_name(fx.away_team))

                    # Get all picks for this round
                    picks = Pick.query.filter_by(round_id=round_obj.id).all()
                    logger.info(f"  picks_total={len(picks)}")

                    # Categorize picks
                    eliminated_count = 0
                    winners_count = 0
                    unmatched_picks = []
                    eliminated_players = []
                    winning_players = []

                    for pick in picks:
                        pick_canonical = normalize_team_name(pick.team_picked)

                        # Check if pick matches any fixture team
                        if pick_canonical not in fixture_teams_canonical:
                            unmatched_picks.append({
                                'pick_id': pick.id,
                                'player_id': pick.player_id,
                                'player_name': pick.player.name,
                                'team_picked': pick.team_picked,
                                'team_canonical': pick_canonical,
                            })
                            # Unmatched pick = cannot win = eliminated
                            if pick.is_winner is None:
                                pick.is_winner = False
                                logger.warning(
                                    f"  UNMATCHED PICK: player_id={pick.player_id} ({pick.player.name}) "
                                    f"picked '{pick.team_picked}' (canonical: '{pick_canonical}') "
                                    f"which doesn't match any fixture team - marking as LOSS"
                                )

                        # Process elimination based on is_winner status
                        if pick.is_winner == False and not pick.is_eliminated:
                            pick.is_eliminated = True
                            pick.player.status = 'eliminated'
                            eliminated_count += 1
                            eliminated_players.append(f"{pick.player.name}(id={pick.player.id}, pick={pick.team_picked})")

                        elif pick.is_winner == True:
                            winners_count += 1
                            winning_players.append(f"{pick.player.name}(id={pick.player.id}, pick={pick.team_picked})")

                        elif pick.is_winner is None:
                            # This shouldn't happen if fixtures are complete and matching works
                            logger.warning(
                                f"  PICK NOT EVALUATED: player_id={pick.player_id} ({pick.player.name}) "
                                f"picked '{pick.team_picked}' still has is_winner=None after fixture completion"
                            )

                    # Log summary for this round
                    logger.info(
                        f"  ROUND {round_obj.round_number} SUMMARY: "
                        f"picks_evaluated={len(picks)}, winners={winners_count}, "
                        f"eliminated={eliminated_count}, unmatched={len(unmatched_picks)}"
                    )

                    if unmatched_picks:
                        logger.warning(f"  UNMATCHED PICKS DETAIL:")
                        for up in unmatched_picks:
                            logger.warning(
                                f"    - pick_id={up['pick_id']}, player={up['player_name']}, "
                                f"team_picked='{up['team_picked']}', canonical='{up['team_canonical']}'"
                            )

                    if eliminated_players:
                        logger.info(f"  Eliminated players: {', '.join(eliminated_players)}")

                    if winning_players:
                        logger.info(f"  Winning players: {', '.join(winning_players)}")

                    total_eliminations += eliminated_count
                    total_winners += winners_count
                    total_draws += len(draw_fixtures)

                    # Mark round as completed
                    round_obj.status = 'completed'
                    logger.info(f"  Round {round_obj.round_number}: Marked as COMPLETED")

                    # Check for game state changes
                    self._check_game_state(round_obj)
                    rounds_processed += 1

                db.session.commit()

                # Final summary
                active_count = Player.query.filter_by(status='active').count()
                eliminated_count_db = Player.query.filter_by(status='eliminated').count()
                winner_count_db = Player.query.filter_by(status='winner').count()

                logger.info("-" * 40)
                logger.info(
                    f"=== ELIMINATION PROCESSING COMPLETE: rounds_processed={rounds_processed}, "
                    f"total_eliminations={total_eliminations}, total_winners={total_winners}, "
                    f"total_draw_fixtures={total_draws} ==="
                )
                logger.info(
                    f"Player status counts: active={active_count}, eliminated={eliminated_count_db}, "
                    f"winner={winner_count_db}"
                )
                logger.info("=" * 60)

            except Exception as e:
                logger.error("=" * 60)
                logger.error("=== ELIMINATION PROCESSING JOB FAILED ===")
                logger.error(f"Error processing eliminations: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                logger.error("=" * 60)
                db.session.rollback()

    def _check_game_state(self, completed_round):
        """Check for winner, all eliminated, or end of cycle"""
        active_players = Player.query.filter_by(status='active').all()

        # Check for winner (exactly 1 active player)
        if len(active_players) == 1:
            winner = active_players[0]
            winner.status = 'winner'
            logger.info(f"WINNER DETECTED: {winner.name} has won the game!")

            # Send winner notification
            self._send_winner_notification(winner)

            # Optionally auto-reset for new game
            if os.environ.get('AUTO_RESET_ON_WIN', 'false').lower() == 'true':
                self._reset_game_for_new_cycle()

        # Check for all eliminated (rollover scenario)
        elif len(active_players) == 0:
            logger.info("ALL PLAYERS ELIMINATED! Triggering rollover...")

            # Reset all players to active for new cycle
            Player.query.update({'status': 'active'}, synchronize_session=False)

            # Update cycle number for next round
            next_cycle = (completed_round.cycle_number or 1) + 1
            logger.info(f"Starting Cycle {next_cycle} after all players eliminated")

            # Send rollover notification
            self._send_rollover_notification(next_cycle)

        # Check for end of cycle (Round 20 with 2+ survivors)
        elif completed_round.round_number == 20 and len(active_players) >= 2:
            logger.info(f"END OF CYCLE {completed_round.cycle_number}: {len(active_players)} survivors")

            # Survivors continue, eliminated players stay eliminated
            next_cycle = (completed_round.cycle_number or 1) + 1
            logger.info(f"Starting Cycle {next_cycle} with {len(active_players)} survivors")

            # Send cycle complete notification
            self._send_cycle_complete_notification(active_players, next_cycle)

    def send_due_reminders(self):
        """Send reminders that are due via Telegram only"""
        with self.app.app_context():
            try:
                logger.info("Checking for due reminders...")

                # Get reminders that are due and not sent
                now = datetime.now()
                due_reminders = ReminderSchedule.query.filter(
                    ReminderSchedule.scheduled_time <= now,
                    ReminderSchedule.is_sent == False
                ).all()

                sent_count = 0
                skipped_missing = 0

                for reminder in due_reminders:
                    player = reminder.player
                    round_obj = reminder.round

                    # Get or create pick token
                    pick_token = PickToken.query.filter_by(
                        player_id=player.id,
                        round_id=round_obj.id
                    ).first()

                    if not pick_token:
                        pick_token = PickToken.create_for_player_round(
                            player.id, round_obj.id, expires_hours=168
                        )
                        db.session.add(pick_token)
                        db.session.commit()

                    # Prepare message
                    pick_url = f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/pick/{pick_token.token}"

                    if reminder.reminder_type == '4_hour':
                        message = f"⚽ Round {round_obj.round_number} picks close in 4 hours!"
                        button_text = "⚽ Make Your Pick"
                    else:  # 2_hour
                        message = f"⏰ FINAL REMINDER: Round {round_obj.round_number} picks close in 2 hours!\n\nMake your pick NOW!"
                        button_text = "🚨 MAKE PICK NOW"

                    if hasattr(player, 'telegram_id') and player.telegram_id:
                        telegram_sent = self._send_telegram_message(
                            player.telegram_id,
                            message,
                            button_url=pick_url,
                            button_text=button_text
                        )
                        if telegram_sent:
                            sent_count += 1
                        else:
                            logger.warning(
                                "Failed to send Telegram reminder to %s (id=%s, phone=%s)",
                                player.name,
                                player.id,
                                player.whatsapp_number or "-"
                            )
                    else:
                        skipped_missing += 1
                        logger.warning(
                            "Skipping reminder for %s (id=%s, phone=%s) missing telegram_chat_id",
                            player.name,
                            player.id,
                            player.whatsapp_number or "-"
                        )

                    # Mark as sent
                    reminder.is_sent = True
                    reminder.sent_at = datetime.now()

                db.session.commit()

                if due_reminders:
                    logger.info(
                        "Reminder send summary: sent=%s skipped_missing_telegram=%s total_due=%s",
                        sent_count,
                        skipped_missing,
                        len(due_reminders)
                    )

            except Exception as e:
                logger.error(f"Error sending reminders: {e}")
                db.session.rollback()

    def send_new_round_announcements(self):
        """Send initial round announcement to all players when new round starts.

        Only sends to players who:
        - Are eligible (active, not eliminated in previous rounds of this cycle)
        - Have a telegram_chat_id
        - Have NOT already submitted a pick for this round

        This makes the job idempotent - running it multiple times will not
        re-notify players who have already picked.
        """
        with self.app.app_context():
            try:
                logger.info("=== ROUND ANNOUNCEMENT JOB START ===")

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) for announcements")

                if not active_rounds:
                    logger.info("No active rounds found")
                    return

                for round_obj in active_rounds:
                    # Use eligibility check to respect per-round eliminations
                    eligible_players = self._get_eligible_players_for_round(round_obj)

                    # Initialize counters for structured logging
                    eligible_total = len(eligible_players)
                    already_picked_skipped = 0
                    skipped_missing_telegram = 0
                    skipped_no_token = 0
                    skipped_already_announced = 0
                    sent = 0
                    failed = 0

                    for player in eligible_players:
                        # Check if player already has a pick for this round (idempotency check)
                        if self._player_has_pick_for_round(player.id, round_obj.id):
                            already_picked_skipped += 1
                            logger.debug(
                                "Skipping announcement for %s (player_id=%s): already has pick for round_id=%s",
                                player.name,
                                player.id,
                                round_obj.id
                            )
                            continue

                        # Get pick token
                        pick_token = PickToken.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()

                        if not pick_token:
                            skipped_no_token += 1
                            continue

                        # Check if we've already sent the initial announcement
                        # We track this by checking if any reminder has been scheduled
                        existing_reminder = ReminderSchedule.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()

                        if existing_reminder:
                            skipped_already_announced += 1
                            continue

                        # Check for telegram_id
                        if not (hasattr(player, 'telegram_id') and player.telegram_id):
                            skipped_missing_telegram += 1
                            logger.warning(
                                "Skipping round announcement for %s (player_id=%s, phone=%s): missing telegram_chat_id",
                                player.name,
                                player.id,
                                player.whatsapp_number or "-"
                            )
                            continue

                        # Send initial announcement via Telegram
                        pick_url = f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/pick/{pick_token.token}"

                        # Get deadline info
                        if round_obj.first_kickoff_at:
                            deadline_str = round_obj.first_kickoff_at.strftime('%A %d %B at %H:%M')
                        else:
                            deadline_str = "soon"

                        message = f"⚽ NEW ROUND {round_obj.round_number} IS LIVE!\n\n"
                        message += f"Make your pick before {deadline_str}"

                        success = self._send_telegram_message(
                            player.telegram_id,
                            message,
                            button_url=pick_url,
                            button_text="⚽ Make Your Pick"
                        )
                        if success:
                            sent += 1
                            logger.info(f"Sent new round announcement to {player.name}")

                            # Now schedule the reminders (4-hour and 2-hour)
                            if round_obj.first_kickoff_at:
                                # 4-hour reminder
                                reminder_4h = ReminderSchedule(
                                    player_id=player.id,
                                    round_id=round_obj.id,
                                    reminder_type='4_hour',
                                    scheduled_time=round_obj.first_kickoff_at - timedelta(hours=4),
                                    is_sent=False
                                )
                                db.session.add(reminder_4h)

                                # 2-hour reminder
                                reminder_2h = ReminderSchedule(
                                    player_id=player.id,
                                    round_id=round_obj.id,
                                    reminder_type='2_hour',
                                    scheduled_time=round_obj.first_kickoff_at - timedelta(hours=2),
                                    is_sent=False
                                )
                                db.session.add(reminder_2h)
                        else:
                            failed += 1
                            logger.warning(
                                "Failed to send round announcement to %s (player_id=%s, phone=%s)",
                                player.name,
                                player.id,
                                player.whatsapp_number or "-"
                            )

                    # Structured logging summary with all counts
                    logger.info(
                        "Round announcement summary: round_id=%s, round_number=%s, "
                        "eligible_total=%s, already_picked_skipped=%s, skipped_no_token=%s, "
                        "skipped_already_announced=%s, skipped_missing_telegram=%s, sent=%s, failed=%s",
                        round_obj.id,
                        round_obj.round_number,
                        eligible_total,
                        already_picked_skipped,
                        skipped_no_token,
                        skipped_already_announced,
                        skipped_missing_telegram,
                        sent,
                        failed
                    )

                db.session.commit()
                logger.info("=== ROUND ANNOUNCEMENT JOB COMPLETE ===")

            except Exception as e:
                logger.error(f"Error sending round announcements: {e}")
                db.session.rollback()

    def generate_round_tokens(self):
        """Generate pick tokens for pending/active rounds without tokens"""
        with self.app.app_context():
            try:
                logger.info("=== TOKEN GENERATION JOB START ===")

                # Get pending AND active rounds (tokens should be created before activation)
                rounds_needing_tokens = Round.query.filter(
                    Round.status.in_(['pending', 'active'])
                ).all()
                logger.info(
                    f"Found {len(rounds_needing_tokens)} round(s) (pending/active) for token generation check"
                )

                if not rounds_needing_tokens:
                    logger.info("No pending or active rounds found")
                    return

                tokens_created = 0

                for round_obj in rounds_needing_tokens:
                    # Use eligibility check to respect per-round eliminations
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    logger.info(
                        f"Round {round_obj.round_number}: Processing {len(eligible_players)} eligible player(s)"
                    )

                    round_tokens_created = 0

                    for player in eligible_players:
                        # Check if token exists
                        existing_token = PickToken.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()

                        if not existing_token:
                            # Create token
                            token = PickToken.create_for_player_round(
                                player.id, round_obj.id, expires_hours=168
                            )
                            db.session.add(token)
                            logger.info(
                                f"Created token for player '{player.name}' (id={player.id}) - Round {round_obj.round_number}"
                            )
                            round_tokens_created += 1

                    tokens_created += round_tokens_created
                    logger.info(
                        f"Round {round_obj.round_number}: Created {round_tokens_created} new token(s)"
                    )

                db.session.commit()
                logger.info(f"=== TOKEN GENERATION COMPLETE: {tokens_created} token(s) created ===")

            except Exception as e:
                logger.error(f"Error generating tokens: {e}")
                db.session.rollback()

    def apply_missed_picks(self):
        """Apply auto-picks for players who missed the deadline.

        This job checks all active rounds and creates auto-picks for any player
        who is eligible but has not submitted a pick after the deadline (1 hour
        before first kickoff).

        Important: This does NOT require telegram_chat_id or pick tokens.
        It only requires players.status='active' and no existing pick for the round.
        """
        with self.app.app_context():
            try:
                logger.info("=" * 60)
                logger.info("=== AUTO-PICK JOB START ===")
                logger.info("=" * 60)

                # Always use UTC for consistency
                now = datetime.utcnow()
                logger.info(f"now_utc = {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")

                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Active rounds found: {len(active_rounds)}")

                if not active_rounds:
                    logger.info("No active rounds found, nothing to do")
                    logger.info("=== AUTO-PICK JOB COMPLETE (no rounds) ===")
                    return

                total_auto_picks_applied = 0

                for round_obj in active_rounds:
                    logger.info("-" * 40)
                    logger.info(f"Checking Round {round_obj.round_number} (round_id={round_obj.id})")

                    # Determine first kickoff time
                    kickoff = round_obj.first_kickoff_at
                    kickoff_source = "first_kickoff_at field"

                    if not kickoff:
                        # Fallback: calculate from fixtures
                        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                        kickoff = None
                        for fixture in fixtures:
                            if fixture.date and fixture.time:
                                dt = datetime.combine(fixture.date, fixture.time)
                                if kickoff is None or dt < kickoff:
                                    kickoff = dt
                        kickoff_source = "calculated from fixtures"

                    if not kickoff:
                        logger.warning(
                            f"  Round {round_obj.round_number}: No kickoff time available, SKIPPING"
                        )
                        continue

                    # Ensure kickoff is naive UTC for comparison
                    if kickoff.tzinfo is not None:
                        kickoff = kickoff.replace(tzinfo=None)

                    deadline = kickoff - timedelta(hours=1)

                    logger.info(f"  first_kickoff_utc = {kickoff.strftime('%Y-%m-%d %H:%M:%S')} ({kickoff_source})")
                    logger.info(f"  deadline_utc      = {deadline.strftime('%Y-%m-%d %H:%M:%S')} (kickoff - 1 hour)")
                    logger.info(f"  now_utc           = {now.strftime('%Y-%m-%d %H:%M:%S')}")

                    deadline_passed = now >= deadline
                    logger.info(f"  deadline_passed   = {deadline_passed}")

                    if not deadline_passed:
                        time_until = deadline - now
                        logger.info(f"  Deadline not reached. Time remaining: {time_until}")
                        continue

                    # Get eligible players (status='active', not eliminated in prior rounds)
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    logger.info(f"  eligible_players_count = {len(eligible_players)}")

                    # Find players missing picks
                    missing_pick_players = []
                    for player in eligible_players:
                        existing_pick = Pick.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()
                        if not existing_pick:
                            missing_pick_players.append(player)

                    missing_pick_count = len(missing_pick_players)
                    logger.info(f"  missing_pick_count = {missing_pick_count}")

                    if missing_pick_count == 0:
                        logger.info(f"  All eligible players have picks. Nothing to auto-pick.")
                        continue

                    # Log details of players needing auto-picks
                    logger.info(f"  Players needing auto-pick:")
                    for p in missing_pick_players:
                        telegram_status = "has telegram_id" if p.telegram_id else "NO telegram_id"
                        logger.info(f"    - {p.name} (player_id={p.id}, {telegram_status})")

                    # Apply auto-picks
                    round_auto_picks = 0
                    for player in missing_pick_players:
                        auto_team = self._get_auto_pick_team(player, round_obj)

                        if auto_team:
                            pick = Pick(
                                player_id=player.id,
                                round_id=round_obj.id,
                                team_picked=auto_team,
                                auto_assigned=True,
                                auto_reason='missed_deadline',
                                timestamp=now
                            )
                            db.session.add(pick)
                            logger.info(
                                f"  AUTO-PICK CREATED: player='{player.name}' (id={player.id}) -> "
                                f"team='{auto_team}' for Round {round_obj.round_number}"
                            )
                            round_auto_picks += 1
                        else:
                            logger.warning(
                                f"  FAILED: No eligible team for player '{player.name}' (id={player.id}) - "
                                f"Round {round_obj.round_number} (all teams may be used)"
                            )

                    total_auto_picks_applied += round_auto_picks
                    logger.info(f"  Round {round_obj.round_number} summary: {round_auto_picks} auto-pick(s) created")

                db.session.commit()
                logger.info("-" * 40)
                logger.info(f"=== AUTO-PICK JOB COMPLETE ===")
                logger.info(f"autopicks_created_count = {total_auto_picks_applied}")
                logger.info("=" * 60)

            except Exception as e:
                logger.error("=" * 60)
                logger.error("=== AUTO-PICK JOB FAILED ===")
                logger.error(f"Error applying missed picks: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                logger.error("=" * 60)
                db.session.rollback()

    def round_progression_orchestrator(self):
        """
        Orchestrator job that ensures proper round progression.

        This job runs every 10 minutes and:
        1. Activates pending rounds that have tokens created
        2. Checks if active rounds are ready to be processed
        3. Ensures the automation pipeline doesn't stall
        """
        with self.app.app_context():
            try:
                logger.info("=== ROUND ORCHESTRATOR JOB START ===")

                # Step 1: Check for pending rounds with fixtures and tokens, and activate them
                pending_rounds = Round.query.filter_by(status='pending').all()
                logger.info(f"Found {len(pending_rounds)} pending round(s)")

                for round_obj in pending_rounds:
                    # Check if fixtures exist
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    if not fixtures:
                        logger.info(f"Round {round_obj.round_number}: No fixtures yet, staying pending")
                        continue

                    # Check if tokens exist for eligible players
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    if not eligible_players:
                        logger.warning(f"Round {round_obj.round_number}: No eligible players found")
                        continue

                    # Count how many tokens exist for this round
                    token_count = PickToken.query.filter_by(round_id=round_obj.id).count()

                    logger.info(
                        f"Round {round_obj.round_number}: {len(fixtures)} fixture(s), "
                        f"{len(eligible_players)} eligible player(s), {token_count} token(s)"
                    )

                    # If tokens exist for all eligible players, activate the round
                    if token_count >= len(eligible_players) and len(eligible_players) > 0:
                        round_obj.status = 'active'
                        logger.info(
                            f"Round {round_obj.round_number}: ACTIVATED (has {token_count} tokens for {len(eligible_players)} players)"
                        )
                    else:
                        logger.info(
                            f"Round {round_obj.round_number}: Not ready to activate "
                            f"(needs {len(eligible_players)} tokens, has {token_count})"
                        )

                # Step 2: Check active rounds for completion readiness
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s)")

                for round_obj in active_rounds:
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    completed_fixtures = [f for f in fixtures if f.status == 'completed']

                    logger.info(
                        f"Round {round_obj.round_number} (active): "
                        f"{len(completed_fixtures)}/{len(fixtures)} fixtures completed"
                    )

                    # If all fixtures are completed, the process_eliminations job will handle it
                    if fixtures and all(f.status == 'completed' for f in fixtures):
                        logger.info(
                            f"Round {round_obj.round_number}: All fixtures completed, "
                            f"waiting for elimination processing"
                        )

                db.session.commit()
                logger.info("=== ROUND ORCHESTRATOR JOB COMPLETE ===")

            except Exception as e:
                logger.error(f"Error in round orchestrator: {e}")
                db.session.rollback()

    def _get_auto_pick_team(self, player, round_obj):
        """
        Get auto-pick team for a player.

        Selection algorithm:
        1. Get all teams playing in this round (from fixtures)
        2. Get teams already used by this player in this cycle (normalized comparison)
        3. Available = teams in round that haven't been used
        4. Use deterministic but distributed selection: hash(player_id + round_id) to pick index

        This ensures:
        - Different players get different teams (distributed)
        - Same player always gets same team if re-run (deterministic/idempotent)
        - No fallback to a single team like "Bournemouth" for everyone
        """
        # Get teams in round (from fixtures)
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        teams_in_round_raw = set()
        for fixture in fixtures:
            teams_in_round_raw.add(fixture.home_team)
            teams_in_round_raw.add(fixture.away_team)

        # Normalize round teams to canonical names
        teams_in_round_canonical = {normalize_team_name(t) for t in teams_in_round_raw}

        # Get teams used by player in this cycle (normalized)
        used_teams_canonical = set()
        picks = Pick.query.filter_by(player_id=player.id).join(Round).filter(
            Round.cycle_number == (round_obj.cycle_number or 1)
        ).all()
        for pick in picks:
            used_teams_canonical.add(normalize_team_name(pick.team_picked))

        # Available teams (canonical names)
        available_canonical = teams_in_round_canonical - used_teams_canonical

        logger.info(
            f"  Auto-pick for player_id={player.id} ({player.name}): "
            f"teams_in_round={len(teams_in_round_canonical)}, "
            f"teams_used={len(used_teams_canonical)}, "
            f"available={len(available_canonical)}"
        )

        if not available_canonical:
            logger.warning(
                f"  No available teams for player_id={player.id} ({player.name}) - "
                f"all {len(teams_in_round_canonical)} teams in round have been used"
            )
            return None

        # Sort for deterministic ordering
        available_sorted = sorted(available_canonical)

        # Use hash to distribute picks across players
        # Hash of (player_id, round_id) gives a stable but distributed index
        hash_input = f"{player.id}_{round_obj.id}".encode()
        hash_value = int(hashlib.md5(hash_input).hexdigest(), 16)
        index = hash_value % len(available_sorted)

        selected_canonical = available_sorted[index]

        # Map back to the actual fixture team name for storage
        # (we want to store the same format as fixtures for consistency)
        selected_fixture_name = None
        for raw_name in teams_in_round_raw:
            if normalize_team_name(raw_name) == selected_canonical:
                selected_fixture_name = raw_name
                break

        if not selected_fixture_name:
            selected_fixture_name = selected_canonical

        logger.info(
            f"  Auto-pick selected: player_id={player.id} -> '{selected_fixture_name}' "
            f"(canonical: '{selected_canonical}', index={index}/{len(available_sorted)})"
        )

        return selected_fixture_name

    def _send_telegram_message(self, telegram_id, message, button_url=None, button_text="Make Your Pick"):
        """Send message via Telegram bot with optional inline button"""
        try:
            # Use Telegram API directly instead of bot proxy
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
            if not bot_token:
                logger.error("TELEGRAM_BOT_TOKEN not set")
                return False

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                'chat_id': telegram_id,
                'text': message
            }

            # Add inline keyboard button if URL provided
            if button_url:
                payload['reply_markup'] = {
                    'inline_keyboard': [[
                        {'text': button_text, 'url': button_url}
                    ]]
                }

            response = requests.post(url, json=payload)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    def _send_winner_notification(self, winner):
        """Send winner announcement to all players"""
        message = f"🏆 GAME OVER! {winner.name} has won the Last Man Standing competition! 🎉"

        # Send to all players
        players = Player.query.all()
        sent_count = 0
        skipped_missing = 0
        for player in players:
            if hasattr(player, 'telegram_id') and player.telegram_id:
                if self._send_telegram_message(player.telegram_id, message):
                    sent_count += 1
            else:
                skipped_missing += 1
                logger.warning(
                    "Skipping winner announcement for %s (id=%s, phone=%s) missing telegram_chat_id",
                    player.name,
                    player.id,
                    player.whatsapp_number or "-"
                )
        logger.info(
            "Winner announcement summary: sent=%s skipped_missing_telegram=%s",
            sent_count,
            skipped_missing
        )

    def _send_rollover_notification(self, new_cycle):
        """Send rollover notification to all players"""
        message = f"🔄 ALL PLAYERS ELIMINATED! Starting fresh with Cycle {new_cycle}. All players are back in the game!"

        # Send to all players
        players = Player.query.all()
        sent_count = 0
        skipped_missing = 0
        for player in players:
            if hasattr(player, 'telegram_id') and player.telegram_id:
                if self._send_telegram_message(player.telegram_id, message):
                    sent_count += 1
            else:
                skipped_missing += 1
                logger.warning(
                    "Skipping rollover announcement for %s (id=%s, phone=%s) missing telegram_chat_id",
                    player.name,
                    player.id,
                    player.whatsapp_number or "-"
                )
        logger.info(
            "Rollover announcement summary: sent=%s skipped_missing_telegram=%s",
            sent_count,
            skipped_missing
        )

    def _send_cycle_complete_notification(self, survivors, new_cycle):
        """Send cycle completion notification"""
        survivor_names = ', '.join([p.name for p in survivors])
        message = f"📊 Cycle complete! {len(survivors)} survivors advance to Cycle {new_cycle}: {survivor_names}"

        # Send to all players
        players = Player.query.all()
        sent_count = 0
        skipped_missing = 0
        for player in players:
            if hasattr(player, 'telegram_id') and player.telegram_id:
                if self._send_telegram_message(player.telegram_id, message):
                    sent_count += 1
            else:
                skipped_missing += 1
                logger.warning(
                    "Skipping cycle announcement for %s (id=%s, phone=%s) missing telegram_chat_id",
                    player.name,
                    player.id,
                    player.whatsapp_number or "-"
                )
        logger.info(
            "Cycle completion summary: sent=%s skipped_missing_telegram=%s",
            sent_count,
            skipped_missing
        )

    def _reset_game_for_new_cycle(self):
        """Reset game data for a new cycle (optional auto-reset)"""
        # This is optional - admin can manually trigger if preferred
        pass

    def _log_registered_jobs(self):
        """Log all registered scheduler jobs with their configuration"""
        logger.info("=" * 60)
        logger.info("REGISTERED SCHEDULER JOBS:")
        logger.info("=" * 60)
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            trigger_str = str(job.trigger)
            next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z') if job.next_run_time else 'None'
            logger.info(
                f"  Job ID: {job.id} | Name: {job.name} | "
                f"Trigger: {trigger_str} | Next Run: {next_run}"
            )
        logger.info("=" * 60)
        logger.info(f"Total jobs registered: {len(jobs)}")
        logger.info("=" * 60)

    def _get_eligible_players_for_round(self, round_obj):
        """
        Get eligible players for a round based on survival from previous rounds.

        A player is eligible if:
        1. Their global status is 'active' (not already globally eliminated/winner)
        2. They have NOT been eliminated in any previous round of this cycle

        This ensures that even if the elimination job hasn't updated player.status yet,
        we still respect per-round eliminations when generating tokens/sending messages.
        """
        # Start with globally active players
        all_active_players = Player.query.filter_by(status='active').all()

        if not round_obj:
            return all_active_players

        cycle_number = round_obj.cycle_number or 1

        # Get all picks from previous rounds in this cycle that resulted in elimination
        eliminated_player_ids = set()

        # Find all rounds in this cycle before the current round
        previous_rounds = Round.query.filter(
            Round.cycle_number == cycle_number,
            Round.round_number < round_obj.round_number
        ).all()

        for prev_round in previous_rounds:
            eliminated_picks = Pick.query.filter_by(
                round_id=prev_round.id,
                is_eliminated=True
            ).all()

            for pick in eliminated_picks:
                eliminated_player_ids.add(pick.player_id)

        # Filter out eliminated players
        eligible_players = [
            player for player in all_active_players
            if player.id not in eliminated_player_ids
        ]

        logger.info(
            f"Eligibility check for Round {round_obj.round_number}: "
            f"globally_active={len(all_active_players)}, "
            f"eliminated_in_previous_rounds={len(eliminated_player_ids)}, "
            f"eligible={len(eligible_players)}"
        )

        return eligible_players

    def _player_has_pick_for_round(self, player_id: int, round_id: int) -> bool:
        """
        Check if a player has already submitted a pick for a given round.

        Used to ensure announcements are only sent to players who haven't picked yet,
        making the announcement job idempotent.

        Args:
            player_id: The player's database ID
            round_id: The round's database ID

        Returns:
            True if a pick exists for this player-round combination, False otherwise
        """
        existing_pick = Pick.query.filter_by(
            player_id=player_id,
            round_id=round_id
        ).first()
        return existing_pick is not None


# Create global scheduler instance
scheduler = LMSScheduler()
