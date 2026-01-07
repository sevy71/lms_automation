"""
Background Scheduler for LMS Automation
Handles periodic tasks for automated game management
"""

import os
import logging
from datetime import datetime, timedelta
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
                logger.info("Checking for fixture updates...")
                api = self._get_api()
                if not api:
                    return
                season = os.environ.get('FOOTBALL_SEASON') or os.environ.get('SEASON')

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()

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
                                "Updated fixture: %s %s - %s %s",
                                fixture.home_team,
                                fixture.home_score,
                                fixture.away_score,
                                fixture.away_team
                            )

                db.session.commit()
                logger.info("Fixture updates completed")

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
                logger.info("Processing eliminations...")

                # Get active rounds with all fixtures completed
                active_rounds = Round.query.filter_by(status='active').all()

                for round_obj in active_rounds:
                    # Check if all fixtures are completed
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    if not fixtures or not all(f.status == 'completed' for f in fixtures):
                        continue

                    # Process eliminations
                    eliminated_count = 0
                    for pick in round_obj.picks:
                        if pick.is_winner == False and not pick.is_eliminated:
                            pick.is_eliminated = True
                            pick.player.status = 'eliminated'
                            eliminated_count += 1

                    if eliminated_count > 0:
                        logger.info(f"Eliminated {eliminated_count} players in Round {round_obj.round_number}")

                    # Mark round as completed
                    round_obj.status = 'completed'

                    # Check for game state changes
                    self._check_game_state(round_obj)

                db.session.commit()
                logger.info("Elimination processing completed")

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
        """Send initial round announcement to all players when new round starts"""
        with self.app.app_context():
            try:
                logger.info("Checking for new rounds to announce...")

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()

                for round_obj in active_rounds:
                    active_players = Player.query.filter_by(status='active').all()
                    sent_count = 0
                    skipped_missing = 0

                    for player in active_players:
                        # Get pick token
                        pick_token = PickToken.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()

                        if not pick_token:
                            continue

                        # Check if we've already sent the initial announcement
                        # We track this by checking if any reminder has been scheduled
                        existing_reminder = ReminderSchedule.query.filter_by(
                            player_id=player.id,
                            round_id=round_obj.id
                        ).first()

                        # Only send if no reminders exist (meaning this is a brand new round for this player)
                        if not existing_reminder:
                            if not (hasattr(player, 'telegram_id') and player.telegram_id):
                                skipped_missing += 1
                                logger.warning(
                                    "Skipping round announcement for %s (id=%s, phone=%s) missing telegram_chat_id",
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

                            sent = self._send_telegram_message(
                                player.telegram_id,
                                message,
                                button_url=pick_url,
                                button_text="⚽ Make Your Pick"
                            )
                            if sent:
                                sent_count += 1
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
                                logger.warning(
                                    "Failed to send round announcement to %s (id=%s, phone=%s)",
                                    player.name,
                                    player.id,
                                    player.whatsapp_number or "-"
                                )

                    if sent_count or skipped_missing:
                        logger.info(
                            "Round %s announcement summary: sent=%s skipped_missing_telegram=%s",
                            round_obj.round_number,
                            sent_count,
                            skipped_missing
                        )

                db.session.commit()
                logger.info("Round announcements completed")

            except Exception as e:
                logger.error(f"Error sending round announcements: {e}")
                db.session.rollback()

    def generate_round_tokens(self):
        """Generate pick tokens for active rounds without tokens"""
        with self.app.app_context():
            try:
                logger.info("Checking for rounds needing tokens...")

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()

                for round_obj in active_rounds:
                    active_players = Player.query.filter_by(status='active').all()

                    for player in active_players:
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
                            logger.info(f"Created token for {player.name} - Round {round_obj.round_number}")

                db.session.commit()
                logger.info("Token generation completed")

            except Exception as e:
                logger.error(f"Error generating tokens: {e}")
                db.session.rollback()

    def apply_missed_picks(self):
        """Apply auto-picks for players who missed the deadline"""
        with self.app.app_context():
            try:
                logger.info("Checking for missed picks...")

                now = datetime.now()
                active_rounds = Round.query.filter_by(status='active').all()

                for round_obj in active_rounds:
                    # Check if deadline has passed
                    if round_obj.end_date and round_obj.end_date < now:
                        # Get active players without picks
                        active_players = Player.query.filter_by(status='active').all()

                        for player in active_players:
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
                                        timestamp=datetime.now()
                                    )
                                    db.session.add(pick)
                                    logger.info(f"Auto-picked {auto_team} for {player.name}")

                db.session.commit()
                logger.info("Auto-pick processing completed")

            except Exception as e:
                logger.error(f"Error applying missed picks: {e}")
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


# Create global scheduler instance
scheduler = LMSScheduler()
