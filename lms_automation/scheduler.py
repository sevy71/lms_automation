"""
Background Scheduler for LMS Automation
Handles periodic tasks for automated game management
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flask import Flask
from lms_automation.models import db, Round, Fixture, Pick, Player, ReminderSchedule, PickToken
from lms_automation.football_api import FootballDataAPI
from lms_automation.notifications import NotificationService
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
            self.scheduler.add_job(
                func=self.apply_missed_picks,
                trigger=IntervalTrigger(hours=1),
                id='apply_missed_picks',
                name='Apply auto-picks for players who missed deadline',
                next_run_time=datetime.now(),
                replace_existing=True
            )

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
        """Update pick results based on fixture outcome"""
        if fixture.home_score is None or fixture.away_score is None:
            return

        # Determine winning team
        if fixture.home_score > fixture.away_score:
            winning_team = fixture.home_team
        elif fixture.away_score > fixture.home_score:
            winning_team = fixture.away_team
        else:
            # Draw - both teams are considered winners for LMS
            drawn_teams = [fixture.home_team, fixture.away_team]

            # Update picks for draws (draws eliminate picks)
            picks = Pick.query.filter_by(round_id=fixture.round_id).filter(
                Pick.team_picked.in_(drawn_teams)
            ).all()

            for pick in picks:
                if pick.is_winner is None:
                    pick.is_winner = False
            return

        # Update picks for wins/losses
        picks = Pick.query.filter_by(round_id=fixture.round_id).all()

        for pick in picks:
            if pick.team_picked == winning_team:
                pick.is_winner = True
            elif pick.team_picked in [fixture.home_team, fixture.away_team]:
                pick.is_winner = False

    def process_eliminations(self):
        """Process eliminations for completed rounds and check for rollover"""
        with self.app.app_context():
            try:
                logger.info("=== ELIMINATION PROCESSING JOB START ===")

                # Get active rounds with all fixtures completed
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) to check for elimination processing")

                if not active_rounds:
                    logger.info("No active rounds found")
                    return

                rounds_processed = 0
                total_eliminations = 0

                for round_obj in active_rounds:
                    # Check if all fixtures are completed
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    completed_fixtures = [f for f in fixtures if f.status == 'completed']

                    logger.info(
                        f"Round {round_obj.round_number}: {len(completed_fixtures)}/{len(fixtures)} fixtures completed"
                    )

                    if not fixtures or not all(f.status == 'completed' for f in fixtures):
                        logger.info(f"Round {round_obj.round_number}: Not all fixtures completed yet, skipping")
                        continue

                    # Process eliminations
                    eliminated_count = 0
                    eliminated_player_names = []

                    for pick in round_obj.picks:
                        if pick.is_winner == False and not pick.is_eliminated:
                            pick.is_eliminated = True
                            pick.player.status = 'eliminated'
                            eliminated_count += 1
                            eliminated_player_names.append(f"{pick.player.name}(id={pick.player.id})")
                            logger.debug(
                                f"Marked player '{pick.player.name}' (id={pick.player.id}) as eliminated "
                                f"(Round {round_obj.round_number}, pick={pick.team_picked})"
                            )

                    if eliminated_count > 0:
                        logger.info(
                            f"Round {round_obj.round_number}: Eliminated {eliminated_count} player(s): "
                            f"{', '.join(eliminated_player_names)}"
                        )
                        total_eliminations += eliminated_count
                    else:
                        logger.info(f"Round {round_obj.round_number}: No new eliminations")

                    # Mark round as completed
                    round_obj.status = 'completed'
                    logger.info(f"Round {round_obj.round_number}: Marked as COMPLETED")

                    # Check for game state changes
                    self._check_game_state(round_obj)
                    rounds_processed += 1

                db.session.commit()

                # Verify player statuses were updated
                active_count = Player.query.filter_by(status='active').count()
                eliminated_count_db = Player.query.filter_by(status='eliminated').count()
                logger.info(
                    f"=== ELIMINATION PROCESSING COMPLETE: {rounds_processed} round(s) processed, "
                    f"{total_eliminations} player(s) eliminated ==="
                )
                logger.info(
                    f"Current player status counts: active={active_count}, eliminated={eliminated_count_db}"
                )

            except Exception as e:
                logger.error(f"Error processing eliminations: {e}")
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
        """Apply auto-picks for players who missed the deadline"""
        with self.app.app_context():
            try:
                logger.info("=== AUTO-PICK JOB START ===")

                # Always use UTC for consistency
                now = datetime.utcnow()
                logger.info(f"Current time (UTC): {now}")

                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) to check for missed picks")

                if not active_rounds:
                    logger.info("No active rounds found")
                    return

                auto_picks_applied = 0

                for round_obj in active_rounds:
                    kickoff = round_obj.first_kickoff_at
                    if not kickoff:
                        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                        kickoff = None
                        for fixture in fixtures:
                            if fixture.date and fixture.time:
                                dt = datetime.combine(fixture.date, fixture.time)
                                if kickoff is None or dt < kickoff:
                                    kickoff = dt

                    if not kickoff:
                        logger.info(
                            f"Round {round_obj.round_number}: No kickoff time available, skipping auto-picks"
                        )
                        continue

                    # Ensure kickoff is timezone-aware UTC (or convert naive to UTC)
                    if kickoff.tzinfo is None:
                        kickoff = kickoff.replace(tzinfo=timezone.utc)
                    else:
                        kickoff = kickoff.astimezone(timezone.utc).replace(tzinfo=None)

                    deadline = kickoff - timedelta(hours=1)

                    logger.info(
                        f"Round {round_obj.round_number}: Kickoff={kickoff} (UTC), Deadline={deadline} (UTC), Now={now} (UTC)"
                    )

                    if now >= deadline:
                        # Get eligible players without picks
                        eligible_players = self._get_eligible_players_for_round(round_obj)

                        for player in eligible_players:
                            existing_pick = Pick.query.filter_by(
                                player_id=player.id,
                                round_id=round_obj.id
                            ).first()

                            if not existing_pick:
                                # Apply auto-pick logic (from existing code)
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
                                        f"Auto-picked '{auto_team}' for player '{player.name}' (id={player.id}) - Round {round_obj.round_number}"
                                    )
                                    auto_picks_applied += 1
                                else:
                                    logger.warning(
                                        f"Could not find eligible team for auto-pick: player '{player.name}' (id={player.id}) - Round {round_obj.round_number}"
                                    )
                    else:
                        logger.info(
                            f"Round {round_obj.round_number}: Deadline not reached yet (deadline={deadline}, now={now})"
                        )

                db.session.commit()
                logger.info(f"=== AUTO-PICK JOB COMPLETE: {auto_picks_applied} auto-pick(s) applied ===")

            except Exception as e:
                logger.error(f"Error applying missed picks: {e}")
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
        """Get auto-pick team for a player (simplified version)"""
        # Get teams in round and teams already used
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        teams_in_round = set()
        for fixture in fixtures:
            teams_in_round.add(fixture.home_team)
            teams_in_round.add(fixture.away_team)

        # Get teams used by player in this cycle
        used_teams = set()
        picks = Pick.query.filter_by(player_id=player.id).join(Round).filter(
            Round.cycle_number == (round_obj.cycle_number or 1)
        ).all()
        for pick in picks:
            used_teams.add(pick.team_picked)

        # Available teams
        available = teams_in_round - used_teams

        if available:
            # Return first available team alphabetically
            return sorted(available)[0]
        return None

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
