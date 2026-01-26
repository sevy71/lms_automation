"""
LMS V2 - Background Scheduler
Handles all automated game processes
"""
import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from lms_v2.models import db, Player, Round, Fixture, Pick, PickToken, Reminder
from lms_v2.football_api import FootballAPI
from lms_v2.telegram import TelegramBot

logger = logging.getLogger(__name__)


class LMSScheduler:
    """Background scheduler for LMS automation."""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.app = None
        self.football_api = None
        self.telegram = None

    def init_app(self, app: Flask):
        """Initialize scheduler with Flask app."""
        self.app = app
        self.football_api = FootballAPI()
        self.telegram = TelegramBot()

    def _ensure_session(self):
        """Clean up stale DB sessions before job execution."""
        try:
            db.session.remove()
        except Exception as e:
            logger.warning(f"Session cleanup error: {e}")

    def start(self):
        """Start the scheduler with all jobs."""
        if not self.app:
            raise RuntimeError("Scheduler not initialized with app")

        # Get intervals from config
        fixture_sync = self.app.config.get('FIXTURE_SYNC_INTERVAL', 30)
        fixture_update = self.app.config.get('FIXTURE_UPDATE_INTERVAL', 5)
        elimination = self.app.config.get('ELIMINATION_INTERVAL', 2)
        reminder = self.app.config.get('REMINDER_INTERVAL', 5)

        # Add jobs
        self.scheduler.add_job(self.sync_fixtures, 'interval', minutes=fixture_sync,
                              id='sync_fixtures', replace_existing=True)

        self.scheduler.add_job(self.update_scores, 'interval', minutes=fixture_update,
                              id='update_scores', replace_existing=True)

        self.scheduler.add_job(self.process_eliminations, 'interval', minutes=elimination,
                              id='process_eliminations', replace_existing=True)

        self.scheduler.add_job(self.send_reminders, 'interval', minutes=reminder,
                              id='send_reminders', replace_existing=True)

        self.scheduler.add_job(self.auto_pick_missing, 'interval', minutes=elimination,
                              id='auto_pick_missing', replace_existing=True)

        self.scheduler.add_job(self.complete_rounds, 'interval', minutes=elimination,
                              id='complete_rounds', replace_existing=True)

        self.scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # =========================================================================
    # JOB: Sync Fixtures from Football API
    # =========================================================================
    def sync_fixtures(self):
        """Sync fixtures from Football Data API for active/pending rounds."""
        with self.app.app_context():
            self._ensure_session()
            try:
                rounds = Round.query.filter(Round.status.in_(['pending', 'active'])).all()
                if not rounds:
                    return

                for round_obj in rounds:
                    if not round_obj.pl_matchday:
                        continue

                    fixtures_data = self.football_api.get_matchday_fixtures(round_obj.pl_matchday)
                    if not fixtures_data:
                        continue

                    first_kickoff = None
                    for f in fixtures_data:
                        # Upsert fixture
                        fixture = Fixture.query.filter_by(api_id=f['id']).first()
                        if not fixture:
                            fixture = Fixture(api_id=f['id'], round_id=round_obj.id)
                            db.session.add(fixture)

                        fixture.home_team = f['home_team']
                        fixture.away_team = f['away_team']
                        fixture.kickoff = f['kickoff']
                        fixture.status = f['status']

                        if f['kickoff'] and (not first_kickoff or f['kickoff'] < first_kickoff):
                            first_kickoff = f['kickoff']

                    # Update round's first kickoff
                    if first_kickoff:
                        round_obj.first_kickoff = first_kickoff
                        round_obj.deadline = first_kickoff - timedelta(hours=1)

                    db.session.commit()
                    logger.info(f"Synced fixtures for Round {round_obj.round_number}")

            except Exception as e:
                logger.error(f"Error syncing fixtures: {e}")
                db.session.rollback()

    # =========================================================================
    # JOB: Update Live Scores
    # =========================================================================
    def update_scores(self):
        """Update scores for active round fixtures."""
        with self.app.app_context():
            self._ensure_session()
            try:
                active_round = Round.query.filter_by(status='active').first()
                if not active_round or not active_round.pl_matchday:
                    return

                fixtures_data = self.football_api.get_matchday_fixtures(active_round.pl_matchday)
                if not fixtures_data:
                    return

                for f in fixtures_data:
                    fixture = Fixture.query.filter_by(api_id=f['id']).first()
                    if fixture:
                        fixture.home_score = f.get('home_score')
                        fixture.away_score = f.get('away_score')
                        fixture.status = f['status']

                db.session.commit()
                logger.info(f"Updated scores for Round {active_round.round_number}")

            except Exception as e:
                logger.error(f"Error updating scores: {e}")
                db.session.rollback()

    # =========================================================================
    # JOB: Process Eliminations
    # =========================================================================
    def process_eliminations(self):
        """Check picks against results and mark eliminations."""
        with self.app.app_context():
            self._ensure_session()
            try:
                active_round = Round.query.filter_by(status='active').first()
                if not active_round:
                    return

                # Get finished fixtures
                finished_fixtures = Fixture.query.filter_by(
                    round_id=active_round.id,
                    status='finished'
                ).all()

                if not finished_fixtures:
                    return

                # Build winners set
                winning_teams = set()
                for f in finished_fixtures:
                    if f.winner:
                        winning_teams.add(f.winner)

                # Process picks
                picks = Pick.query.filter_by(round_id=active_round.id).all()
                for pick in picks:
                    # Find if pick's team played in a finished fixture
                    team_fixture = None
                    for f in finished_fixtures:
                        if pick.team in (f.home_team, f.away_team):
                            team_fixture = f
                            break

                    if not team_fixture:
                        continue  # Match not finished yet

                    if pick.team in winning_teams:
                        pick.is_winner = True
                    else:
                        pick.is_eliminated = True
                        # Eliminate the player
                        player = Player.query.get(pick.player_id)
                        if player:
                            player.status = 'eliminated'
                            logger.info(f"Player {player.name} eliminated (picked {pick.team})")

                db.session.commit()

            except Exception as e:
                logger.error(f"Error processing eliminations: {e}")
                db.session.rollback()

    # =========================================================================
    # JOB: Auto-Pick for Missing Players
    # =========================================================================
    def auto_pick_missing(self):
        """Auto-assign picks for players who missed the deadline."""
        with self.app.app_context():
            self._ensure_session()
            try:
                active_round = Round.query.filter_by(status='active').first()
                if not active_round or not active_round.deadline:
                    return

                # Check if deadline passed
                if datetime.utcnow() < active_round.deadline:
                    return

                # Get active players without picks
                active_players = Player.query.filter_by(status='active').all()
                for player in active_players:
                    existing = Pick.query.filter_by(
                        player_id=player.id,
                        round_id=active_round.id
                    ).first()

                    if existing:
                        continue

                    # Get used teams in this cycle
                    used = db.session.query(Pick.team).filter(
                        Pick.player_id == player.id,
                        Pick.round_id.in_(
                            db.session.query(Round.id).filter(
                                Round.cycle_number == active_round.cycle_number
                            )
                        )
                    ).all()
                    used_teams = {t[0] for t in used}

                    # Pick first available team alphabetically
                    from lms_v2.routes.player import PL_TEAMS
                    for team in sorted(PL_TEAMS):
                        if team not in used_teams:
                            pick = Pick(
                                player_id=player.id,
                                round_id=active_round.id,
                                team=team,
                                auto_assigned=True
                            )
                            db.session.add(pick)
                            logger.info(f"Auto-assigned {team} to {player.name}")
                            break

                db.session.commit()

            except Exception as e:
                logger.error(f"Error auto-picking: {e}")
                db.session.rollback()

    # =========================================================================
    # JOB: Send Reminders
    # =========================================================================
    def send_reminders(self):
        """Send due reminders via Telegram."""
        with self.app.app_context():
            self._ensure_session()
            try:
                # Get due reminders
                due = Reminder.query.filter(
                    Reminder.sent_at.is_(None),
                    Reminder.scheduled_for <= datetime.utcnow()
                ).all()

                for reminder in due:
                    player = reminder.player
                    if not player.telegram_id:
                        reminder.sent_at = datetime.utcnow()  # Mark as sent even if no telegram
                        continue

                    # Check if player already picked
                    existing = Pick.query.filter_by(
                        player_id=player.id,
                        round_id=reminder.round_id
                    ).first()

                    if existing:
                        reminder.sent_at = datetime.utcnow()
                        continue

                    # Get pick token URL
                    token = PickToken.query.filter_by(
                        player_id=player.id,
                        round_id=reminder.round_id
                    ).first()

                    if token and token.is_valid:
                        # Send reminder
                        base_url = os.environ.get('APP_URL', 'http://localhost:5000')
                        pick_url = f"{base_url}/pick/{token.token}"
                        message = (
                            f"⏰ Reminder: Round {reminder.round.round_number} pick needed!\n\n"
                            f"Submit your pick here:\n{pick_url}"
                        )
                        self.telegram.send_message(player.telegram_id, message)

                    reminder.sent_at = datetime.utcnow()

                db.session.commit()

            except Exception as e:
                logger.error(f"Error sending reminders: {e}")
                db.session.rollback()

    # =========================================================================
    # JOB: Complete Rounds
    # =========================================================================
    def complete_rounds(self):
        """Complete round when all fixtures finished and picks resolved."""
        with self.app.app_context():
            self._ensure_session()
            try:
                active_round = Round.query.filter_by(status='active').first()
                if not active_round:
                    return

                # Check all fixtures finished
                fixtures = Fixture.query.filter_by(round_id=active_round.id).all()
                if not fixtures:
                    return

                all_finished = all(f.status == 'finished' for f in fixtures)
                if not all_finished:
                    return

                # Check all picks resolved
                picks = Pick.query.filter_by(round_id=active_round.id).all()
                all_resolved = all(p.is_winner or p.is_eliminated for p in picks)
                if not all_resolved:
                    return

                # Complete the round
                active_round.status = 'completed'
                db.session.commit()
                logger.info(f"Round {active_round.round_number} completed")

                # Check for winner (only 1 active player left)
                active_count = Player.query.filter_by(status='active').count()
                if active_count == 1:
                    winner = Player.query.filter_by(status='active').first()
                    winner.status = 'winner'
                    db.session.commit()
                    logger.info(f"🏆 {winner.name} wins the cycle!")

                    # Notify winner
                    if winner.telegram_id:
                        self.telegram.send_message(
                            winner.telegram_id,
                            f"🏆 Congratulations {winner.name}! You won the Last Man Standing cycle!"
                        )

            except Exception as e:
                logger.error(f"Error completing round: {e}")
                db.session.rollback()


# Global scheduler instance
scheduler = LMSScheduler()
