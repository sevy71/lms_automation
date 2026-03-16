"""
Background Scheduler for LMS Automation
Handles periodic tasks for automated game management

ROUND STATE MACHINE
===================

States:
-------
- pending      : Round created, awaiting fixtures/tokens/activation
- active       : Round accepting picks; players can submit/edit picks
- picks_locked : deadline_passed AND (all picks in OR autopick applied); no more edits
                 (tracked via special_note containing 'picks_locked')
- completed    : All RELEVANT fixtures finished AND eliminations processed
                 (relevant = involves a team that at least one player picked)

PHASE MARKERS (in special_note):
--------------------------------
- tokens_generated : All eligible players have tokens for this round
- announced        : Initial "round is live" announcement attempted for all eligible players
- picks_locked     : Deadline passed AND all picks are in (manual or auto); no more edits allowed
- admin_locked     : Admin triggered manual lock (bypasses deadline check)
- picks_published  : "Picks locked" notification sent
- results_sent     : Round results notification sent

Transitions:
------------
pending -> active:
  WHEN: fixtures exist AND tokens_generated marker set
  BY: round_progression_orchestrator job

active -> announced:
  WHEN: round is active AND send_new_round_announcements runs
  BY: send_new_round_announcements job (marks 'announced' after attempting all)

announced -> picks_locked:
  WHEN: (deadline_passed OR admin_locked) AND (picks_count >= eligible_count OR autopick_applied)
  BY: check_all_picks_submitted OR apply_missed_picks
  GUARD: MUST have 'announced' marker (or SKIP_ANNOUNCEMENT_GATE=true)
  GUARD: MUST have deadline_passed OR admin_locked (prevents early lock!)

picks_locked -> completed:
  WHEN: ALL of:
    a) all RELEVANT fixtures completed (fixtures involving picked teams)
       Non-relevant fixtures (no player picked either team) do NOT block completion.
    b) picks exist for every eligible player (manual or auto)
    c) eliminations have been processed (is_winner evaluated for all picks)
  BY: process_eliminations job

completed -> (next round pending):
  WHEN: round completed AND next round created
  BY: rollover logic OR admin creating new round

CRITICAL INVARIANTS:
--------------------
1. NEVER mark round completed if picks_count == 0 for eligible players
2. NEVER mark round completed if picks_count < eligible_count AND deadline not passed
3. NEVER mark round completed if any RELEVANT fixture is still scheduled/live/postponed
   (relevant = involves a team that a player picked)
4. is_winner=False MUST always mean is_eliminated=True (enforced immediately per-fixture)
   SAFETY NET: Also enforced by invariant job for completed rounds with fixture scores.
5. Auto-picks MUST be applied before eliminations if deadline has passed
6. NEVER allow picks_locked without tokens_generated (unless SKIP_ANNOUNCEMENT_GATE)
7. NEVER allow picks_locked without announced marker (unless SKIP_ANNOUNCEMENT_GATE)
8. NEVER allow picks_locked without deadline_passed (unless admin_locked)
9. NEVER eliminate players via invariant job if round not completed/finalized

IDEMPOTENCY RULES:
------------------
- generate_round_tokens: Only creates if token doesn't exist; marks 'tokens_generated' once
- send_new_round_announcements: Only sends if not announced; marks 'announced' once
- apply_missed_picks: Only insert if no pick exists for (player_id, round_id)
- _update_picks_for_fixture: Sets is_winner AND immediately sets is_eliminated/player.status for losers
- process_eliminations: Safety-net re-check; only set is_eliminated=True if is_winner=False AND not already eliminated
- check_all_picks_submitted: Only send notification once (check special_note)
- enforce_global_elimination_invariant: Only enforces for completed rounds with scores
- Notifications: All use special_note markers to prevent duplicate sends
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
from lms_automation.eligibility import get_eligible_players_for_round
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
        # MANUAL_MODE: When true, disables automation that completes rounds or processes eliminations
        # This allows the game to run at human pace for debugging
        self.manual_mode = os.environ.get('MANUAL_MODE', 'false').lower() == 'true'

    def init_app(self, app: Flask):
        """Initialize scheduler with Flask app context"""
        self.app = app

    def _get_api(self) -> Optional[FootballDataAPI]:
        try:
            return FootballDataAPI()
        except Exception as e:
            logger.error("Football API unavailable: %s", e)
            return None

    def _ensure_db_session(self):
        """
        Ensure db session is healthy before job execution.

        Called at the start of scheduler jobs to handle stale connections.
        The pool_pre_ping setting handles most cases, but this provides
        an extra safety net for long-running operations.
        """
        try:
            # Remove any stale session state from previous job runs
            db.session.remove()
        except Exception as e:
            logger.warning(f"Error cleaning up DB session: {e}")

    def start(self):
        """Start the scheduler with all jobs.

        FAST INTERVALS with GUARDS:
        - Most jobs run every 1-2 minutes for responsiveness
        - Internal guards prevent collisions and ensure correct sequencing
        - Jobs are idempotent: running twice won't cause issues

        MANUAL_MODE:
        - When MANUAL_MODE=true, critical automation jobs are disabled
        - Disabled jobs: process_eliminations, update_fixtures, apply_missed_picks, check_all_picks, round_orchestrator
        - Enabled jobs: sync_fixtures, generate_tokens, send_round_announcements, send_reminders, enforce_invariant
        - This allows the admin to manually control round completion via the UI
        """
        if not self.scheduler.running:
            # Log MANUAL_MODE status at startup
            logger.info("=" * 60)
            logger.info(f"SCHEDULER CONFIGURATION: MANUAL_MODE={self.manual_mode}")
            if self.manual_mode:
                logger.info("MANUAL MODE ENABLED: The following jobs are DISABLED:")
                logger.info("  - update_fixtures (fixture result polling)")
                logger.info("  - process_eliminations (round completion + eliminations)")
                logger.info("  - apply_missed_picks (auto-picks after deadline)")
                logger.info("  - check_all_picks (picks locked notification)")
                logger.info("  - round_orchestrator (auto-activate pending rounds)")
                logger.info("Admin must use 'Process Results' button to manually process rounds.")
            logger.info("=" * 60)

            # Schedule jobs

            # Check for fixture updates every 5 minutes (was 30)
            # Guard: Only updates fixtures that have changed
            # MANUAL_MODE: DISABLED - this job evaluates picks and could lead to auto-completion
            fixture_interval = int(os.environ.get('FIXTURE_UPDATE_INTERVAL_MINUTES', '5'))
            if not self.manual_mode:
                self.scheduler.add_job(
                    func=self.update_fixture_results,
                    trigger=IntervalTrigger(minutes=fixture_interval),
                    id='update_fixtures',
                    name='Update fixture results from Football API',
                    replace_existing=True
                )
                logger.info(f"Fixture updates job configured with {fixture_interval}-minute interval")
            else:
                logger.info(f"Fixture updates job DISABLED (MANUAL_MODE=true)")

            # Sync fixtures every 30 minutes (was 60)
            # Guard: Only syncs fixtures that are missing or changed
            sync_interval = int(os.environ.get('FIXTURE_SYNC_INTERVAL_MINUTES', '30'))
            self.scheduler.add_job(
                func=self.sync_fixtures,
                trigger=IntervalTrigger(minutes=sync_interval),
                id='sync_fixtures',
                name='Sync fixtures from Football API',
                replace_existing=True
            )
            logger.info(f"Fixture sync job configured with {sync_interval}-minute interval")

            # Process eliminations every 2 minutes (fast but guarded)
            # GUARDS: Won't complete round unless ALL conditions met:
            #   1. All fixtures completed
            #   2. Picks exist for eligible players (or deadline passed + autopick ran)
            # MANUAL_MODE: DISABLED - this is the main job that completes rounds and eliminates players
            elim_interval_minutes = int(os.environ.get('ELIMINATION_INTERVAL_MINUTES', '2'))
            if not self.manual_mode:
                self.scheduler.add_job(
                    func=self.process_eliminations,
                    trigger=IntervalTrigger(minutes=elim_interval_minutes),
                    id='process_eliminations',
                    name='Process eliminations and check for rollover',
                    next_run_time=datetime.now(),  # run immediately on boot
                    replace_existing=True
                )
                logger.info(f"Eliminations job configured with {elim_interval_minutes}-minute interval (guarded)")
            else:
                logger.info(f"Eliminations job DISABLED (MANUAL_MODE=true)")

            # Send reminders every 5 minutes (was 15)
            # Guard: Uses is_sent flag to prevent duplicates
            reminder_interval = int(os.environ.get('REMINDER_INTERVAL_MINUTES', '5'))
            self.scheduler.add_job(
                func=self.send_due_reminders,
                trigger=IntervalTrigger(minutes=reminder_interval),
                id='send_reminders',
                name='Send due reminders via Telegram',
                replace_existing=True
            )
            logger.info(f"Reminders job configured with {reminder_interval}-minute interval")

            # Generate tokens for new rounds every 10 minutes (was 1 hour)
            # Guard: Only creates tokens if they don't exist
            token_interval = int(os.environ.get('TOKEN_GENERATION_INTERVAL_MINUTES', '10'))
            self.scheduler.add_job(
                func=self.generate_round_tokens,
                trigger=IntervalTrigger(minutes=token_interval),
                id='generate_tokens',
                name='Generate pick tokens for active rounds',
                next_run_time=datetime.now(),
                replace_existing=True
            )
            logger.info(f"Token generation job configured with {token_interval}-minute interval")

            # Send initial round announcements every 10 minutes (was 30)
            # Guard: Uses special_note to prevent duplicate sends
            announcement_interval = int(os.environ.get('ANNOUNCEMENT_INTERVAL_MINUTES', '10'))
            self.scheduler.add_job(
                func=self.send_new_round_announcements,
                trigger=IntervalTrigger(minutes=announcement_interval),
                id='send_round_announcements',
                name='Send new round announcements to players via Telegram',
                replace_existing=True
            )
            logger.info(f"Announcements job configured with {announcement_interval}-minute interval")

            # Apply missed picks every 2 minutes (fast, catches deadlines promptly)
            # Guard: Only inserts if no existing pick for (player_id, round_id)
            # MANUAL_MODE: DISABLED - prevents automatic team assignment; admin can trigger manually
            autopick_interval_minutes = int(os.environ.get('AUTOPICK_INTERVAL_MINUTES', '2'))
            if not self.manual_mode:
                self.scheduler.add_job(
                    func=self.apply_missed_picks,
                    trigger=IntervalTrigger(minutes=autopick_interval_minutes),
                    id='apply_missed_picks',
                    name='Apply auto-picks for players who missed deadline',
                    next_run_time=datetime.now(),
                    replace_existing=True
                )
                logger.info(f"Auto-pick job configured with {autopick_interval_minutes}-minute interval")
            else:
                logger.info(f"Auto-pick job DISABLED (MANUAL_MODE=true)")

            # Orchestrator job - ensures proper round progression every 5 minutes (was 10)
            # Guard: Only activates rounds that meet all criteria
            # MANUAL_MODE: DISABLED - prevents automatic round activation; admin controls when rounds go active
            orchestrator_interval = int(os.environ.get('ORCHESTRATOR_INTERVAL_MINUTES', '5'))
            if not self.manual_mode:
                self.scheduler.add_job(
                    func=self.round_progression_orchestrator,
                    trigger=IntervalTrigger(minutes=orchestrator_interval),
                    id='round_orchestrator',
                    name='Orchestrate round progression (activate pending rounds with tokens)',
                    next_run_time=datetime.now(),
                    replace_existing=True
                )
                logger.info(f"Orchestrator job configured with {orchestrator_interval}-minute interval")
            else:
                logger.info(f"Orchestrator job DISABLED (MANUAL_MODE=true)")

            # Check if all picks are submitted every 2 minutes (was 5)
            # Guard: Uses special_note to prevent duplicate notifications
            # MANUAL_MODE: DISABLED - prevents auto-locking picks; admin controls the flow
            picks_check_interval = int(os.environ.get('PICKS_CHECK_INTERVAL_MINUTES', '2'))
            if not self.manual_mode:
                self.scheduler.add_job(
                    func=self.check_all_picks_submitted,
                    trigger=IntervalTrigger(minutes=picks_check_interval),
                    id='check_all_picks',
                    name='Check if all picks submitted and publish',
                    replace_existing=True
                )
                logger.info(f"Picks check job configured with {picks_check_interval}-minute interval")
            else:
                logger.info(f"Picks check job DISABLED (MANUAL_MODE=true)")

            # Global invariant enforcement every 30 minutes (was 1 hour)
            # Guard: Only corrects actual violations
            invariant_interval = int(os.environ.get('INVARIANT_CHECK_INTERVAL_MINUTES', '30'))
            self.scheduler.add_job(
                func=self.enforce_global_elimination_invariant,
                trigger=IntervalTrigger(minutes=invariant_interval),
                id='enforce_invariant',
                name='Enforce elimination invariant across all picks',
                replace_existing=True
            )
            logger.info(f"Invariant enforcement job configured with {invariant_interval}-minute interval")

            # Deliver pending Telegram notifications from the outbox every 60 seconds.
            # This is the async delivery half of the transactional outbox pattern:
            # game-state code writes rows to notification_outbox inside the state
            # transaction; this job delivers them with up to 3 attempts before
            # marking a row 'dead'.
            self.scheduler.add_job(
                func=self._deliver_notifications,
                trigger=IntervalTrigger(seconds=60),
                id='deliver_notifications',
                name='Deliver pending Telegram notifications from outbox',
                next_run_time=datetime.now(),
                replace_existing=True,
            )
            logger.info("Notification delivery job configured (60-second interval)")

            # Prune old timeline records once per day per retention policy.
            # Controlled via env vars: RUN_TIMELINE_CHECKPOINT_RETENTION_DAYS (default 30),
            # RUN_TIMELINE_AUDIT_RETENTION_DAYS (default 0=never), RUN_TIMELINE_RUN_RETENTION_DAYS (default 0=never)
            self.scheduler.add_job(
                func=self._run_timeline_retention,
                trigger=IntervalTrigger(hours=24),
                id='timeline_retention',
                name='Prune old timeline records per retention policy',
                replace_existing=True,
            )
            logger.info("Timeline retention job configured (24-hour interval)")

            self.scheduler.start()
            logger.info("Scheduler started with all jobs configured")

            # Log all registered jobs for verification
            self._log_registered_jobs()

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduler stopped")

    def _get_fixture_update_window(self, round_obj):
        """
        Calculate the time window during which we should poll for fixture results.

        Returns (window_start, window_end) tuple or (None, None) if no fixtures.

        Logic:
        - Games typically last ~2 hours
        - Start polling 2 hours after the earliest kick-off (when first games might finish)
        - Continue polling until 3 hours after the latest kick-off (buffer for delays)

        Example: If fixtures kick off at 15:00, 17:30, and 20:00:
        - Earliest kickoff: 15:00 → window_start = 17:00
        - Latest kickoff: 20:00 → window_end = 23:00
        - Update window: 17:00 to 23:00
        """
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        if not fixtures:
            return None, None

        kickoff_times = []
        for fixture in fixtures:
            if fixture.date and fixture.time:
                dt = datetime.combine(fixture.date, fixture.time)
                kickoff_times.append(dt)

        if not kickoff_times:
            return None, None

        earliest_kickoff = min(kickoff_times)
        latest_kickoff = max(kickoff_times)

        # Start polling 2 hours after earliest kick-off (when first games should be finishing)
        window_start = earliest_kickoff + timedelta(hours=2)
        # Stop polling 4 hours after latest kick-off (2h game + 2h API delay buffer)
        window_end = latest_kickoff + timedelta(hours=4)

        return window_start, window_end

    def _is_within_update_window(self, round_obj, now=None):
        """
        Check if current time is within the fixture update window for a round.

        Uses PER-FIXTURE windows: each fixture's window runs from its kickoff
        to kickoff + 4 hours (2h game time + 2h API delay buffer).

        Returns True if ANY incomplete fixture is currently within its window,
        or if any incomplete fixture is past its window (straggler catch-up).
        """
        if now is None:
            now = datetime.utcnow()

        # Ensure now is naive for comparison
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)

        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        if not fixtures:
            logger.debug(f"Round {round_obj.round_number}: No fixtures found, will poll")
            return True

        incomplete_fixtures = [f for f in fixtures if f.status != 'completed']

        if not incomplete_fixtures:
            logger.debug(f"Round {round_obj.round_number}: All fixtures completed, skipping poll")
            return False

        for fixture in incomplete_fixtures:
            if not fixture.date or not fixture.time:
                # No kickoff time known -- be safe and poll
                logger.debug(
                    f"Round {round_obj.round_number}: Fixture {fixture.home_team} vs {fixture.away_team} "
                    f"has no kickoff time, will poll"
                )
                return True

            kickoff = datetime.combine(fixture.date, fixture.time)
            # 4h window: ~2h for game to finish + 2h buffer for API delays
            fixture_window_end = kickoff + timedelta(hours=4)

            if kickoff <= now <= fixture_window_end:
                logger.debug(
                    f"Round {round_obj.round_number}: Fixture {fixture.home_team} vs {fixture.away_team} "
                    f"within per-fixture window (kickoff={kickoff.strftime('%Y-%m-%d %H:%M')}, "
                    f"window_end={fixture_window_end.strftime('%Y-%m-%d %H:%M')})"
                )
                return True

            if now > fixture_window_end:
                # Past the fixture's window but still not completed -- keep polling as straggler
                logger.debug(
                    f"Round {round_obj.round_number}: Fixture {fixture.home_team} vs {fixture.away_team} "
                    f"past window but still incomplete, continuing to poll"
                )
                return True

        # All incomplete fixtures are in the future (before their kickoff)
        logger.debug(
            f"Round {round_obj.round_number}: {len(incomplete_fixtures)} incomplete fixture(s) "
            f"but none have reached their kickoff time yet"
        )
        return False

    def update_fixture_results(self):
        """Poll Football API for fixture results and update database.

        Uses smart scheduling based on fixture kick-off times:
        - Only polls during the window when games are expected to finish
        - Window starts 2 hours after earliest kick-off (when first games finish)
        - Window ends 3 hours after latest kick-off (buffer for delays)
        - Continues polling if any fixtures remain incomplete
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=== FIXTURE UPDATE JOB START ===")
                api = self._get_api()
                if not api:
                    logger.warning("Football API unavailable, skipping fixture updates")
                    return

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) to check for fixture updates")

                if not active_rounds:
                    logger.info("No active rounds found, nothing to update")
                    return

                for round_obj in active_rounds:
                    if not round_obj.pl_matchday:
                        continue

                    # Check if we're within the update window for this round
                    if not self._is_within_update_window(round_obj):
                        window_start, window_end = self._get_fixture_update_window(round_obj)
                        if window_start:
                            logger.info(
                                f"Round {round_obj.round_number}: Skipping API poll - outside update window "
                                f"({window_start.strftime('%Y-%m-%d %H:%M')} to {window_end.strftime('%Y-%m-%d %H:%M')})"
                            )
                        continue

                    logger.info(f"Round {round_obj.round_number}: Within update window, polling API for results")

                    # Use round's api_season_year for the API call
                    season_param = str(round_obj.get_api_season_year()) if round_obj.get_api_season_year() else None
                    api_data = api.get_premier_league_fixtures(
                        matchday=round_obj.pl_matchday,
                        season=season_param
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
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("Syncing fixtures from Football API...")
                api = self._get_api()
                if not api:
                    return

                rounds = Round.query.filter(Round.status.in_(['pending', 'active'])).all()
                created = 0
                updated = 0
                skipped = 0

                for round_obj in rounds:
                    if not round_obj.pl_matchday:
                        skipped += 1
                        continue

                    # Use round's api_season_year for the API call
                    season_param = str(round_obj.get_api_season_year()) if round_obj.get_api_season_year() else None
                    fixtures_data = api.get_premier_league_fixtures(
                        matchday=round_obj.pl_matchday,
                        season=season_param
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
                    if not pick.is_eliminated:
                        pick.is_eliminated = True
                        if pick.player and pick.player.status != 'eliminated':
                            pick.player.status = 'eliminated'
                            logger.info(
                                f"IMMEDIATE ELIMINATION: player_id={pick.player_id} ({pick.player.name}) "
                                f"eliminated via {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}"
                            )
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
                    if not pick.is_eliminated:
                        pick.is_eliminated = True
                        if pick.player and pick.player.status != 'eliminated':
                            pick.player.status = 'eliminated'
                            logger.info(
                                f"IMMEDIATE ELIMINATION: player_id={pick.player_id} ({pick.player.name}) "
                                f"eliminated via {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}"
                            )
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
                    if not pick.is_eliminated:
                        pick.is_eliminated = True
                        if pick.player and pick.player.status != 'eliminated':
                            pick.player.status = 'eliminated'
                            logger.info(
                                f"IMMEDIATE ELIMINATION: player_id={pick.player_id} ({pick.player.name}) "
                                f"eliminated via draw {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}"
                            )
                    picks_updated += 1
                    logger.info(
                        f"PICK RESULT: player_id={pick.player_id} picked '{pick.team_picked}' "
                        f"-> ELIMINATED (draw {fixture.home_score}-{fixture.away_score}) - DRAW IS NOT A WIN"
                    )

            for pick in away_picks:
                if pick.is_winner is None:
                    pick.is_winner = False  # Draw is NOT a win
                    if not pick.is_eliminated:
                        pick.is_eliminated = True
                        if pick.player and pick.player.status != 'eliminated':
                            pick.player.status = 'eliminated'
                            logger.info(
                                f"IMMEDIATE ELIMINATION: player_id={pick.player_id} ({pick.player.name}) "
                                f"eliminated via draw {fixture.home_team} {fixture.home_score}-{fixture.away_score} {fixture.away_team}"
                            )
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

        STATE MACHINE GUARDS (NON-NEGOTIABLE):
        A round is ONLY marked completed when ALL of these are true:
          a) fixtures exist for that round
          b) all RELEVANT fixtures are completed — a fixture is "relevant" only if
             at least one player picked either of its teams.  Fixtures where no
             player made a selection do NOT block round completion.
          c) picks exist for every eligible player OR deadline passed AND autopick has run
          d) all picks have been evaluated (is_winner is not None)

        This job:
        1. Checks if all RELEVANT fixtures in active rounds are completed
        2. Validates picks exist for eligible players (or deadline passed)
        3. For each completed round, marks picks with is_winner=False as eliminated
        4. Updates player.status to 'eliminated' for eliminated picks
        5. Detects unmatched picks (picks that couldn't be matched to any fixture)
        6. Only then marks the round as completed
        7. Checks for winner/rollover conditions
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            from lms_automation.services.timeline_context import TimelineContext
            _tl = TimelineContext('process_eliminations', trigger_type='scheduler', mode='auto')
            _tl.__enter__()
            try:
                logger.info("=" * 60)
                logger.info("=== ELIMINATION PROCESSING JOB START ===")
                logger.info("=" * 60)

                now = datetime.utcnow()

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

                    # ======== GUARD 1: Check fixtures exist ========
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    if not fixtures:
                        logger.warning(f"  GUARD BLOCK: Round {round_obj.round_number} has no fixtures, skipping")
                        continue

                    # ======== GUARD 2: Check all RELEVANT fixtures completed ========
                    # A fixture is "relevant" if any player picked one of the teams in it.
                    # The round can complete as soon as all picked teams' games have finished,
                    # even if other fixtures in the matchday are still pending.
                    completed_fixtures = [f for f in fixtures if f.status == 'completed']
                    draw_fixtures = [f for f in completed_fixtures if f.home_score == f.away_score]
                    pending_fixtures = [f for f in fixtures if f.status != 'completed']

                    picks = Pick.query.filter_by(round_id=round_obj.id).all()
                    picked_teams = {normalize_team_name(p.team_picked) for p in picks}

                    # Find pending fixtures that involve a picked team
                    relevant_pending = []
                    for pf in pending_fixtures:
                        home_canonical = normalize_team_name(pf.home_team)
                        away_canonical = normalize_team_name(pf.away_team)
                        if home_canonical in picked_teams or away_canonical in picked_teams:
                            relevant_pending.append(pf)

                    logger.info(
                        f"  fixtures_total={len(fixtures)}, fixtures_completed={len(completed_fixtures)}, "
                        f"fixtures_pending={len(pending_fixtures)}, relevant_pending={len(relevant_pending)}, "
                        f"fixtures_draw={len(draw_fixtures)}"
                    )

                    if relevant_pending:
                        logger.info(
                            f"  GUARD BLOCK: Round {round_obj.round_number} has {len(relevant_pending)} "
                            f"pending fixture(s) with picked teams"
                        )
                        for pf in relevant_pending[:5]:
                            logger.info(f"    - {pf.home_team} vs {pf.away_team} (status={pf.status})")
                        continue

                    # Task-5 completion-check log — always emitted so future issues are easy to trace.
                    relevant_fixtures_total = len([
                        f for f in fixtures
                        if normalize_team_name(f.home_team) in picked_teams
                        or normalize_team_name(f.away_team) in picked_teams
                    ])
                    relevant_fixtures_completed = len([
                        f for f in completed_fixtures
                        if normalize_team_name(f.home_team) in picked_teams
                        or normalize_team_name(f.away_team) in picked_teams
                    ])
                    logger.info(
                        f"  Round {round_obj.round_number} completion check: "
                        f"{relevant_fixtures_completed}/{relevant_fixtures_total} relevant fixtures completed"
                        + (
                            f" ({len(pending_fixtures)} non-relevant fixture(s) still pending)"
                            if pending_fixtures else ""
                        )
                    )

                    # ======== GUARD 3: Check picks exist for eligible players ========
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    eligible_count = len(eligible_players)
                    picks_count = len(picks)

                    logger.info(f"  eligible_players={eligible_count}, picks_count={picks_count}")

                    # Determine deadline
                    kickoff = round_obj.first_kickoff_at
                    if not kickoff:
                        # Fallback: calculate from fixtures
                        for fixture in fixtures:
                            if fixture.date and fixture.time:
                                dt = datetime.combine(fixture.date, fixture.time)
                                if kickoff is None or dt < kickoff:
                                    kickoff = dt

                    deadline = kickoff - timedelta(hours=1) if kickoff else None
                    deadline_passed = deadline and now >= deadline

                    logger.info(f"  deadline={'passed' if deadline_passed else 'not_passed'} (kickoff={kickoff}, deadline={deadline})")

                    # CRITICAL GUARD: Don't complete round if picks are missing
                    if picks_count == 0:
                        if deadline_passed:
                            logger.warning(
                                f"  GUARD BLOCK: Round {round_obj.round_number} has 0 picks but deadline passed. "
                                f"Waiting for autopick job to run."
                            )
                        else:
                            logger.info(
                                f"  GUARD BLOCK: Round {round_obj.round_number} has 0 picks and deadline not passed. "
                                f"Cannot complete round yet."
                            )
                        continue

                    if picks_count < eligible_count:
                        if deadline_passed:
                            logger.warning(
                                f"  GUARD BLOCK: Round {round_obj.round_number} has {picks_count}/{eligible_count} picks "
                                f"but deadline passed. Waiting for autopick job to fill missing picks."
                            )
                        else:
                            logger.info(
                                f"  GUARD BLOCK: Round {round_obj.round_number} has {picks_count}/{eligible_count} picks "
                                f"and deadline not passed. Cannot complete round yet."
                            )
                        continue

                    # ======== ALL GUARDS PASSED - Proceed with elimination processing ========
                    logger.info(f"  ALL GUARDS PASSED: Proceeding with elimination processing")
                    _tl.checkpoint(f'before_eliminations_r{round_obj.round_number}')
                    _tl.decision(
                        title=f'Process eliminations for Round {round_obj.round_number}',
                        rule_matched='all_guards_passed',
                        reasoning=(
                            f'All {len(completed_fixtures)} relevant fixtures completed, '
                            f'{picks_count}/{eligible_count} picks present, deadline_passed={deadline_passed}'
                        ),
                        confidence='high',
                        intended_action='evaluate_picks_mark_eliminated',
                        expected_impact=f'Up to {picks_count} picks evaluated, round marked completed',
                    )

                    # Ensure all completed fixtures have had their picks evaluated.
                    # This handles the race condition where sync_fixtures marks fixtures as 'completed'
                    # (with scores from API) but doesn't evaluate picks.
                    for fixture in completed_fixtures:
                        if fixture.home_score is not None and fixture.away_score is not None:
                            self._update_picks_for_fixture(fixture)

                    # Build set of all teams in fixtures (normalized)
                    fixture_teams_canonical = set()
                    for fx in fixtures:
                        fixture_teams_canonical.add(normalize_team_name(fx.home_team))
                        fixture_teams_canonical.add(normalize_team_name(fx.away_team))

                    # Refresh picks after evaluation
                    picks = Pick.query.filter_by(round_id=round_obj.id).all()
                    logger.info(f"  picks_total={len(picks)}")

                    # ======== GUARD 4: Check all picks have been evaluated ========
                    unevaluated_picks = [p for p in picks if p.is_winner is None]
                    if unevaluated_picks:
                        # Try to evaluate unmatched picks
                        for pick in unevaluated_picks:
                            pick_canonical = normalize_team_name(pick.team_picked)
                            if pick_canonical not in fixture_teams_canonical:
                                # Unmatched pick = cannot win = eliminated
                                pick.is_winner = False
                                logger.warning(
                                    f"  UNMATCHED PICK: player_id={pick.player_id} ({pick.player.name}) "
                                    f"picked '{pick.team_picked}' (canonical: '{pick_canonical}') "
                                    f"which doesn't match any fixture team - marking as LOSS"
                                )

                    # Re-check for unevaluated picks
                    picks = Pick.query.filter_by(round_id=round_obj.id).all()
                    still_unevaluated = [p for p in picks if p.is_winner is None]
                    if still_unevaluated:
                        logger.warning(
                            f"  GUARD BLOCK: Round {round_obj.round_number} still has {len(still_unevaluated)} "
                            f"unevaluated picks after fixture completion. This indicates a bug."
                        )
                        for pick in still_unevaluated[:5]:
                            logger.warning(
                                f"    - pick_id={pick.id}, player={pick.player.name}, team={pick.team_picked}"
                            )
                        continue

                    # ======== Process eliminations ========
                    eliminated_count = 0
                    winners_count = 0
                    unmatched_picks = []
                    eliminated_players = []
                    winning_players = []

                    for pick in picks:
                        pick_canonical = normalize_team_name(pick.team_picked)

                        # Track unmatched picks for logging
                        if pick_canonical not in fixture_teams_canonical:
                            unmatched_picks.append({
                                'pick_id': pick.id,
                                'player_id': pick.player_id,
                                'player_name': pick.player.name,
                                'team_picked': pick.team_picked,
                                'team_canonical': pick_canonical,
                            })

                        # Process elimination based on is_winner status (idempotent)
                        if pick.is_winner == False and not pick.is_eliminated:
                            pick.is_eliminated = True
                            pick.player.status = 'eliminated'
                            eliminated_count += 1
                            eliminated_players.append(f"{pick.player.name}(id={pick.player.id}, pick={pick.team_picked})")

                        elif pick.is_winner == True:
                            # CRITICAL: Ensure winning player's status is 'active' (not 'eliminated' or stale)
                            # This prevents the bug where survivors=1 but active_count=0 triggers rollover
                            if pick.player.status != 'active':
                                logger.info(
                                    f"  WINNER STATUS FIX: {pick.player.name} was '{pick.player.status}', "
                                    f"setting to 'active'"
                                )
                                pick.player.status = 'active'
                            winners_count += 1
                            winning_players.append(f"{pick.player.name}(id={pick.player.id}, pick={pick.team_picked})")

                    # Log summary for this round
                    if eliminated_count == 0:
                        logger.info(
                            f"  All eliminations already processed per-fixture (immediate elimination active)"
                        )
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

                    # ======== Mark round as completed (all guards passed) ========
                    round_obj.status = 'completed'

                    # Log completion with phase summary for one-glance debugging
                    phase_status = self._get_phase_status(round_obj)
                    logger.info(
                        f"  Round {round_obj.round_number}: COMPLETED "
                        f"(picks_total={len(picks)}, winners={winners_count}, eliminated={eliminated_count}, "
                        f"fixtures_completed={len(completed_fixtures)}, phase={phase_status})"
                    )
                    _tl.action(
                        action_type='round_completed',
                        outcome='applied',
                        actual_impact=(
                            f'round_id={round_obj.id} r{round_obj.round_number}: '
                            f'winners={winners_count} eliminated={eliminated_count}'
                        ),
                        reversible=True,
                        players_delta=eliminated_count,
                        rounds_delta=1,
                    )

                    # SAFEGUARD: Ensure no picks have is_winner=False AND is_eliminated=False
                    # This is an invariant violation - if you lost, you MUST be eliminated
                    self._enforce_elimination_invariant(round_obj)

                    # LEAN & CLEAN: Send round results to all participants
                    self._send_round_results_notification(
                        round_obj,
                        winners_count=winners_count,
                        eliminated_count=eliminated_count,
                        draw_count=len(draw_fixtures)
                    )

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
                _tl.__exit__(None, None, None)

            except Exception as e:
                logger.error("=" * 60)
                logger.error("=== ELIMINATION PROCESSING JOB FAILED ===")
                logger.error(f"Error processing eliminations: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                logger.error("=" * 60)
                db.session.rollback()
                _tl.__exit__(type(e), e, e.__traceback__)

    def _evaluate_game_state_after_round(self, completed_round) -> str:
        """
        Delegates to services.round_lifecycle — the single source of truth.
        Passes self.announce_round_now as the automation callback so new rounds
        get tokens generated and announced automatically.
        """
        from lms_automation.services.round_lifecycle import evaluate_game_state_after_round
        return evaluate_game_state_after_round(
            completed_round,
            announce_callback=self.announce_round_now,
        )

    def _send_admin_rollover_notification(self, new_cycle, completed_round):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_admin_rollover_notification
        return send_admin_rollover_notification(new_cycle, completed_round)

    def _check_game_state(self, completed_round):
        """
        Legacy wrapper for backwards compatibility.
        Delegates to _evaluate_game_state_after_round.
        """
        return self._evaluate_game_state_after_round(completed_round)

    def _create_rollover_round(self, completed_round, next_cycle):
        """Delegates to services.round_lifecycle."""
        from lms_automation.services.round_lifecycle import create_rollover_round
        return create_rollover_round(completed_round, next_cycle)

    def _create_next_round(self, completed_round, next_round_number, cycle_number):
        """Delegates to services.round_lifecycle."""
        from lms_automation.services.round_lifecycle import create_next_round
        return create_next_round(completed_round, next_round_number, cycle_number)

    def _populate_fixtures_for_rollover_round(self, round_obj):
        """Delegates to services.round_lifecycle."""
        from lms_automation.services.round_lifecycle import populate_fixtures_for_round
        return populate_fixtures_for_round(round_obj)

    def _trigger_rollover_automation(self, round_obj):
        """
        Trigger the full automation pipeline for a rollover round.

        IMPORTANT: This is called AFTER db.session.commit() so the round+fixtures
        are already persisted. Telegram messages happen outside the DB transaction.

        Uses phase markers for proper sequencing:
        1. Token generation -> sets 'tokens_generated' marker
        2. Round activation (requires 'tokens_generated')
        3. Announcements -> sets 'announced' marker
        """
        logger.info(f"=== ROLLOVER AUTOMATION START (round {round_obj.round_number}) ===")

        # Step 1: Generate tokens (reuse logic from generate_round_tokens)
        eligible_players = self._get_eligible_players_for_round(round_obj)
        eligible_count = len(eligible_players)
        tokens_created = 0

        for player in eligible_players:
            existing_token = PickToken.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()

            if not existing_token:
                token = PickToken.create_for_player_round(
                    player.id, round_obj.id, expires_hours=168
                )
                db.session.add(token)
                tokens_created += 1

        # Set tokens_generated marker
        token_count = PickToken.query.filter_by(round_id=round_obj.id).count() + tokens_created
        if token_count >= eligible_count and eligible_count > 0:
            self._add_marker(round_obj, 'tokens_generated')
            logger.info(
                f"ROLLOVER: Generated {tokens_created} tokens, TOKENS_GENERATED marker set "
                f"({token_count}/{eligible_count})"
            )
        else:
            logger.info(f"ROLLOVER: Generated {tokens_created} tokens")

        db.session.commit()

        # Step 2: Activate round if ready (requires tokens_generated marker)
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()

        activated = False
        if fixtures and self._has_marker(round_obj, 'tokens_generated') and eligible_count > 0:
            round_obj.status = 'active'
            db.session.commit()
            activated = True
            logger.info(
                f"ROLLOVER: Round {round_obj.round_number} ACTIVATED "
                f"({len(fixtures)} fixtures, {token_count} tokens, tokens_generated=True)"
            )
        else:
            logger.warning(
                f"ROLLOVER: Round {round_obj.round_number} NOT activated "
                f"(fixtures={len(fixtures)}, tokens={token_count}, eligible={eligible_count}, "
                f"tokens_generated={self._has_marker(round_obj, 'tokens_generated')})"
            )

        # Step 3: Send announcements and set 'announced' marker
        # This function is idempotent (skips already-announced players via ReminderSchedule)
        # and handles all the telegram sending + reminder scheduling
        if activated:
            # Call the existing announcement job directly (it uses app context internally)
            # Since we're already in app context, call the inner logic
            self._send_rollover_announcements_for_round(round_obj)

        logger.info(
            f"ROLLOVER: Complete - tokens_created={tokens_created}, activated={activated}"
        )
        logger.info(f"=== ROLLOVER AUTOMATION END ===")

    def _send_rollover_announcements_for_round(self, round_obj):
        """
        Send announcements for a specific rollover round.

        Reuses the same logic as send_new_round_announcements but for a single round.
        This avoids duplicating the announcement/reminder logic.
        """
        eligible_players = self._get_eligible_players_for_round(round_obj)

        sent = 0
        skipped_no_token = 0
        skipped_no_telegram = 0
        skipped_already_announced = 0

        for player in eligible_players:
            if player.status != 'active':
                continue

            # Skip if already has a pick
            if self._player_has_pick_for_round(player.id, round_obj.id):
                continue

            pick_token = PickToken.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()

            if not pick_token:
                skipped_no_token += 1
                continue

            # Skip if already announced (reminder exists = already notified)
            existing_reminder = ReminderSchedule.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()
            if existing_reminder:
                skipped_already_announced += 1
                continue

            if not (hasattr(player, 'telegram_id') and player.telegram_id):
                skipped_no_telegram += 1
                continue

            # Build announcement message
            pick_url = f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/pick/{pick_token.token}"

            if round_obj.first_kickoff_at:
                deadline = round_obj.first_kickoff_at - timedelta(hours=1)
                deadline_str = deadline.strftime('%A %d %B at %H:%M')
            else:
                deadline_str = "soon"

            message = f"🔄 CYCLE {round_obj.cycle_number} - ROUND {round_obj.round_number} IS LIVE!\n\n"
            message += f"Everyone's back in! Make your pick before {deadline_str}"

            success = self._send_telegram_message(
                player.telegram_id,
                message,
                button_url=pick_url,
                button_text="⚽ Make Your Pick"
            )

            if success:
                sent += 1

                # Schedule reminders (same logic as send_new_round_announcements)
                if round_obj.first_kickoff_at:
                    _now = datetime.utcnow()

                    # 24-hour reminder — only schedule if still in the future
                    _reminder_24h_time = round_obj.first_kickoff_at - timedelta(hours=24)
                    if _reminder_24h_time > _now:
                        db.session.add(ReminderSchedule(
                            player_id=player.id,
                            round_id=round_obj.id,
                            reminder_type='24_hour',
                            scheduled_time=_reminder_24h_time,
                            is_sent=False
                        ))

                    reminder_4h = ReminderSchedule(
                        player_id=player.id,
                        round_id=round_obj.id,
                        reminder_type='4_hour',
                        scheduled_time=round_obj.first_kickoff_at - timedelta(hours=4),
                        is_sent=False
                    )
                    db.session.add(reminder_4h)

                    reminder_2h = ReminderSchedule(
                        player_id=player.id,
                        round_id=round_obj.id,
                        reminder_type='2_hour',
                        scheduled_time=round_obj.first_kickoff_at - timedelta(hours=2),
                        is_sent=False
                    )
                    db.session.add(reminder_2h)

        # Set 'announced' marker after processing all eligible players
        self._add_marker(round_obj, 'announced')

        db.session.commit()

        logger.info(
            f"ROLLOVER: Announcements sent={sent}, skipped_no_token={skipped_no_token}, "
            f"skipped_no_telegram={skipped_no_telegram}, skipped_already_announced={skipped_already_announced}, "
            f"ANNOUNCED marker set"
        )

    def _enforce_elimination_invariant(self, round_obj):
        """
        SAFEGUARD: Enforce the invariant that is_winner=False MUST mean is_eliminated=True.

        This function:
        1. Finds any picks where is_winner=False AND is_eliminated=False (invariant violation)
        2. Logs loudly if any are found
        3. Auto-corrects by setting is_eliminated=True and player.status='eliminated'

        This should never happen if the code is correct, but this safeguard ensures
        data integrity even if there's a bug elsewhere.
        """
        # Find picks that violate the invariant
        violating_picks = Pick.query.filter_by(
            round_id=round_obj.id,
            is_winner=False,
            is_eliminated=False
        ).all()

        if not violating_picks:
            logger.debug(f"  Round {round_obj.round_number}: Elimination invariant OK (0 violations)")
            return

        # LOUDLY log the violation
        logger.error("=" * 60)
        logger.error("!!! ELIMINATION INVARIANT VIOLATION DETECTED !!!")
        logger.error(f"Round {round_obj.round_number}: Found {len(violating_picks)} picks with is_winner=False AND is_eliminated=False")
        logger.error("This should NEVER happen. Auto-correcting now...")
        logger.error("=" * 60)

        # Auto-correct each violation
        corrected_count = 0
        for pick in violating_picks:
            player = pick.player
            logger.error(
                f"  AUTO-CORRECTING: pick_id={pick.id}, player='{player.name}' (id={player.id}), "
                f"team='{pick.team_picked}' -> setting is_eliminated=True, player.status='eliminated'"
            )

            # Fix the pick
            pick.is_eliminated = True

            # Fix the player status
            if player.status != 'eliminated':
                player.status = 'eliminated'

            corrected_count += 1

        logger.error(f"  Auto-corrected {corrected_count} invariant violations for Round {round_obj.round_number}")
        logger.error("=" * 60)

    def enforce_global_elimination_invariant(self):
        """
        GLOBAL SAFEGUARD: Periodically scan ALL picks for invariant violations.

        Invariant: is_winner=False MUST mean is_eliminated=True

        CRITICAL GUARDS (to prevent premature elimination):
        - Only enforce for rounds where status='completed' OR special_note contains 'results_sent'
        - Only enforce if fixtures_completed > 0 AND at least one fixture has scores
        - Unresolved picks (is_winner=False, is_eliminated=False) are ALLOWED while fixtures
          are still in progress - this is the normal state before results come in.

        This catches any historical violations that might have slipped through,
        ensuring data integrity across the entire database.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=" * 60)
                logger.info("=== GLOBAL ELIMINATION INVARIANT CHECK START ===")

                # Find ALL picks that violate the invariant (across all rounds)
                violating_picks = Pick.query.filter_by(
                    is_winner=False,
                    is_eliminated=False
                ).all()

                if not violating_picks:
                    logger.info("Global elimination invariant check: OK (0 potential violations)")
                    logger.info("=== GLOBAL ELIMINATION INVARIANT CHECK COMPLETE ===")
                    logger.info("=" * 60)
                    return

                # Group picks by round for evaluation
                picks_by_round = {}
                for pick in violating_picks:
                    round_id = pick.round_id
                    if round_id not in picks_by_round:
                        picks_by_round[round_id] = []
                    picks_by_round[round_id].append(pick)

                logger.info(f"Found {len(violating_picks)} picks with is_winner=False AND is_eliminated=False "
                           f"across {len(picks_by_round)} round(s)")

                corrected_count = 0
                skipped_count = 0

                for round_id, round_picks in picks_by_round.items():
                    round_obj = Round.query.get(round_id)
                    if not round_obj:
                        logger.warning(f"  Round {round_id}: not found, skipping {len(round_picks)} picks")
                        skipped_count += len(round_picks)
                        continue

                    # Get fixture status for this round
                    fixtures = Fixture.query.filter_by(round_id=round_id).all()
                    fixtures_total = len(fixtures)
                    fixtures_completed = len([f for f in fixtures if f.status == 'completed'])
                    scores_present = any(
                        f.home_score is not None and f.away_score is not None
                        for f in fixtures
                    )

                    # Check if round is finalized (safe to enforce invariant)
                    round_completed = round_obj.status == 'completed'
                    results_sent = self._has_marker(round_obj, 'results_sent')

                    # Log decision inputs for visibility
                    logger.info(
                        f"  Round {round_obj.round_number} (id={round_id}): "
                        f"round.status='{round_obj.status}', results_sent={results_sent}, "
                        f"fixtures_total={fixtures_total}, fixtures_completed={fixtures_completed}, "
                        f"scores_present={scores_present}, potential_violations={len(round_picks)}"
                    )

                    # GUARD 1: Only enforce if round is completed OR results have been sent
                    if not round_completed and not results_sent:
                        logger.info(
                            f"  GUARD SKIP: Round {round_obj.round_number} not finalized "
                            f"(status='{round_obj.status}', results_sent={results_sent}). "
                            f"Skipping {len(round_picks)} picks - they may still be pending results."
                        )
                        skipped_count += len(round_picks)
                        continue

                    # GUARD 2: Only enforce if fixtures have actually been played (scores exist)
                    if fixtures_completed == 0 or not scores_present:
                        logger.info(
                            f"  GUARD SKIP: Round {round_obj.round_number} has no completed fixtures with scores "
                            f"(fixtures_completed={fixtures_completed}, scores_present={scores_present}). "
                            f"Skipping {len(round_picks)} picks."
                        )
                        skipped_count += len(round_picks)
                        continue

                    # All guards passed - these are genuine violations in a completed round
                    logger.warning(
                        f"  VIOLATION CONFIRMED: Round {round_obj.round_number} is finalized but has "
                        f"{len(round_picks)} picks with is_winner=False AND is_eliminated=False"
                    )

                    for pick in round_picks:
                        player = pick.player

                        logger.warning(
                            f"    AUTO-CORRECTING: pick_id={pick.id}, round={round_obj.round_number}, "
                            f"player='{player.name}' (id={player.id}), team='{pick.team_picked}' "
                            f"-> setting is_eliminated=True, player.status='eliminated'"
                        )

                        # Fix the pick
                        pick.is_eliminated = True

                        # Fix the player status
                        if player.status != 'eliminated':
                            player.status = 'eliminated'

                        corrected_count += 1

                db.session.commit()

                logger.info("-" * 40)
                logger.info(
                    f"Global invariant enforcement summary: "
                    f"corrected={corrected_count}, skipped={skipped_count} (pending rounds)"
                )
                logger.info("=== GLOBAL ELIMINATION INVARIANT CHECK COMPLETE ===")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error in global invariant enforcement: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                db.session.rollback()

    def send_due_reminders(self):
        """Send reminders that are due via Telegram only.

        LEAN & CLEAN POLICY:
        - Only send to ACTIVE players
        - Only send to players who have NOT yet submitted a pick
        - Never remind eliminated players
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=== REMINDER JOB START ===")

                # Get reminders that are due and not sent
                # IMPORTANT: Use UTC for all internal time comparisons
                now = datetime.utcnow()
                due_reminders = ReminderSchedule.query.filter(
                    ReminderSchedule.scheduled_time <= now,
                    ReminderSchedule.is_sent == False
                ).all()

                sent_count = 0
                skipped_missing = 0
                skipped_already_picked = 0
                skipped_not_active = 0

                for reminder in due_reminders:
                    player = reminder.player
                    round_obj = reminder.round

                    # LEAN & CLEAN: Only send to ACTIVE players
                    # Eliminated players must never receive reminders
                    if player.status != 'active':
                        reminder.is_sent = True
                        reminder.sent_at = now
                        skipped_not_active += 1
                        logger.debug(
                            f"Skipping reminder for {player.name} (player_id={player.id}): "
                            f"status={player.status} (not active)"
                        )
                        continue

                    # CRITICAL: Check if player already has a pick for this round
                    # Players who already submitted a pick must NEVER be reminded
                    existing_pick = Pick.query.filter_by(
                        player_id=player.id,
                        round_id=round_obj.id
                    ).first()

                    if existing_pick:
                        # Player already picked - mark reminder as sent but don't actually send
                        reminder.is_sent = True
                        reminder.sent_at = now
                        skipped_already_picked += 1
                        logger.debug(
                            f"Skipping reminder for {player.name} (player_id={player.id}): "
                            f"already picked '{existing_pick.team_picked}' for round_id={round_obj.id}"
                        )
                        continue

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

                    if reminder.reminder_type == '24_hour':
                        message = f"📅 Round {round_obj.round_number} picks close in 24 hours! Don't forget to submit your pick."
                        button_text = "⚽ Make Your Pick"
                    elif reminder.reminder_type == '4_hour':
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
                            logger.info(
                                f"Sent {reminder.reminder_type} reminder to {player.name} "
                                f"(player_id={player.id}) for Round {round_obj.round_number}"
                            )
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
                    reminder.sent_at = now

                db.session.commit()

                logger.info(
                    f"=== REMINDER JOB COMPLETE: sent={sent_count}, "
                    f"skipped_not_active={skipped_not_active}, skipped_already_picked={skipped_already_picked}, "
                    f"skipped_missing_telegram={skipped_missing}, total_due={len(due_reminders)} ==="
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

        PHASE MARKER: Sets 'announced' marker when announcements have been attempted
        for all eligible players. This marker gates auto-pick from running.

        This makes the job idempotent - running it multiple times will not
        re-notify players who have already picked.

        Delegates to announce_round_now() for each active round to ensure
        consistent behavior between scheduled and immediate announcements.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=" * 60)
                logger.info("=== ROUND ANNOUNCEMENT JOB START ===")

                # Get active rounds
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s) for announcements")

                if not active_rounds:
                    logger.info("No active rounds found")
                    logger.info("=== ROUND ANNOUNCEMENT JOB COMPLETE (no rounds) ===")
                    return

                for round_obj in active_rounds:
                    # Log phase status for debugging
                    phase_status = self._get_phase_status(round_obj)
                    logger.info(
                        f"Round {round_obj.round_number} (id={round_obj.id}): phase={phase_status}"
                    )

                    # Delegate to announce_round_now for consistent logic
                    result = self.announce_round_now(round_obj.id)

                    if result.get('skipped_already_announced'):
                        logger.info(
                            f"Round {round_obj.round_number}: Already has 'announced' marker, skipped"
                        )

                logger.info("=== ROUND ANNOUNCEMENT JOB COMPLETE ===")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error sending round announcements: {e}")
                db.session.rollback()

    def generate_round_tokens(self):
        """Generate pick tokens for pending/active rounds without tokens.

        PHASE MARKER: Sets 'tokens_generated' when all eligible players have tokens.
        This marker is required before round activation and announcements.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=" * 60)
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
                    logger.info("=== TOKEN GENERATION JOB COMPLETE (no rounds) ===")
                    return

                total_tokens_created = 0

                for round_obj in rounds_needing_tokens:
                    # Use eligibility check to respect per-round eliminations
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    eligible_count = len(eligible_players)

                    # Count existing tokens
                    existing_token_count = PickToken.query.filter_by(round_id=round_obj.id).count()

                    # Log phase status for debugging
                    phase_status = self._get_phase_status(round_obj)
                    logger.info(
                        f"Round {round_obj.round_number} (id={round_obj.id}): "
                        f"eligible={eligible_count}, existing_tokens={existing_token_count}, "
                        f"phase={phase_status}"
                    )

                    if eligible_count == 0:
                        logger.warning(
                            f"Round {round_obj.round_number}: No eligible players, skipping token generation"
                        )
                        continue

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
                                f"  Created token for player '{player.name}' (id={player.id})"
                            )
                            round_tokens_created += 1

                    total_tokens_created += round_tokens_created

                    # Check if all eligible players now have tokens
                    final_token_count = PickToken.query.filter_by(round_id=round_obj.id).count()

                    # PHASE MARKER: Set 'tokens_generated' when all tokens exist
                    if final_token_count >= eligible_count and not self._has_marker(round_obj, 'tokens_generated'):
                        self._add_marker(round_obj, 'tokens_generated')
                        logger.info(
                            f"Round {round_obj.round_number}: TOKENS_GENERATED marker set "
                            f"({final_token_count}/{eligible_count} tokens)"
                        )

                    logger.info(
                        f"Round {round_obj.round_number}: tokens_created={round_tokens_created}, "
                        f"total_tokens={final_token_count}/{eligible_count}"
                    )

                db.session.commit()
                logger.info(f"=== TOKEN GENERATION COMPLETE: {total_tokens_created} token(s) created ===")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error generating tokens: {e}")
                db.session.rollback()

    def apply_missed_picks(self):
        """Apply auto-picks for players who missed the deadline.

        This job checks all active rounds and creates auto-picks for any player
        who is eligible but has not submitted a pick after the deadline (1 hour
        before first kickoff).

        PHASE GUARD: Requires 'announced' marker before auto-picks can run.
        This ensures players receive pick-link announcements before auto-pick.
        Set SKIP_ANNOUNCEMENT_GATE=true to bypass this check (emergency use).

        Important: This does NOT require telegram_chat_id or pick tokens.
        It only requires players.status='active' and no existing pick for the round.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            from lms_automation.services.timeline_context import TimelineContext
            _tl = TimelineContext('apply_missed_picks', trigger_type='scheduler', mode='auto')
            _tl.__enter__()
            try:
                logger.info("=" * 60)
                logger.info("=== AUTO-PICK JOB START ===")
                logger.info("=" * 60)

                # Check if announcement gate is disabled (emergency bypass)
                skip_announcement_gate = os.environ.get('SKIP_ANNOUNCEMENT_GATE', 'false').lower() == 'true'
                if skip_announcement_gate:
                    logger.warning("SKIP_ANNOUNCEMENT_GATE=true - bypassing announcement requirement")

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

                    # Log phase status for debugging
                    phase_status = self._get_phase_status(round_obj)
                    logger.info(f"  phase_status = {phase_status}")

                    # PHASE GUARD: Require 'announced' marker before auto-picks
                    # This ensures players received their pick-link announcements first
                    if not skip_announcement_gate and not self._has_marker(round_obj, 'announced'):
                        logger.warning(
                            f"  GUARD BLOCK: Round {round_obj.round_number} missing 'announced' marker. "
                            f"Waiting for announcement job to complete before auto-picks. "
                            f"(Set SKIP_ANNOUNCEMENT_GATE=true to bypass)"
                        )
                        continue

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

                    _tl.checkpoint(f'before_autopick_r{round_obj.round_number}')
                    _tl.decision(
                        title=f'Apply auto-picks for Round {round_obj.round_number}',
                        rule_matched='deadline_passed_picks_missing',
                        reasoning=(
                            f'Deadline passed for round {round_obj.round_number}, '
                            f'{missing_pick_count} player(s) have not submitted picks'
                        ),
                        confidence='high',
                        intended_action='insert_auto_pick_for_each_missing_player',
                        expected_impact=f'{missing_pick_count} auto-pick(s) to be created',
                    )

                    # Log details of players needing auto-picks
                    logger.info(f"  Players needing auto-pick:")
                    for p in missing_pick_players:
                        telegram_status = "has telegram_id" if p.telegram_id else "NO telegram_id"
                        logger.info(f"    - {p.name} (player_id={p.id}, {telegram_status})")

                    # Apply auto-picks
                    round_auto_picks = 0
                    for player in missing_pick_players:
                        auto_team, auto_reason = self._get_auto_pick_team(player, round_obj)

                        if auto_team:
                            pick = Pick(
                                player_id=player.id,
                                round_id=round_obj.id,
                                team_picked=auto_team,
                                auto_assigned=True,
                                auto_reason=auto_reason,  # 'rollback_opponent' or 'fallback_alpha_first'
                                timestamp=now
                            )
                            db.session.add(pick)
                            logger.info(
                                f"  AUTO-PICK CREATED: player='{player.name}' (id={player.id}) -> "
                                f"team='{auto_team}' reason='{auto_reason}' for Round {round_obj.round_number}"
                            )
                            round_auto_picks += 1
                        else:
                            logger.warning(
                                f"  FAILED: No eligible team for player '{player.name}' (id={player.id}) - "
                                f"Round {round_obj.round_number} (all teams may be used)"
                            )

                    total_auto_picks_applied += round_auto_picks
                    logger.info(f"  Round {round_obj.round_number} summary: {round_auto_picks} auto-pick(s) created")
                    if round_auto_picks > 0:
                        _tl.action(
                            action_type='auto_picks_applied',
                            outcome='applied',
                            actual_impact=(
                                f'round_id={round_obj.id} r{round_obj.round_number}: '
                                f'{round_auto_picks} auto-pick(s) created'
                            ),
                            reversible=True,
                            players_delta=round_auto_picks,
                        )

                    # After applying auto-picks, check if all picks are now in and mark as picks_locked
                    # This signals to process_eliminations that autopick has completed
                    eligible_count = len(eligible_players)
                    current_picks_count = Pick.query.filter_by(round_id=round_obj.id).count()

                    if current_picks_count >= eligible_count:
                        # All picks are in (manual + auto) - mark as locked
                        if not (round_obj.special_note and 'picks_locked' in round_obj.special_note):
                            if round_obj.special_note:
                                round_obj.special_note += "; picks_locked"
                            else:
                                round_obj.special_note = "picks_locked"
                            logger.info(
                                f"  Round {round_obj.round_number}: ALL PICKS IN ({current_picks_count}/{eligible_count}) "
                                f"- marked as picks_locked"
                            )

                db.session.commit()
                logger.info("-" * 40)
                logger.info(f"=== AUTO-PICK JOB COMPLETE ===")
                logger.info(f"autopicks_created_count = {total_auto_picks_applied}")
                logger.info("=" * 60)
                _tl.__exit__(None, None, None)

            except Exception as e:
                logger.error("=" * 60)
                logger.error("=== AUTO-PICK JOB FAILED ===")
                logger.error(f"Error applying missed picks: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                logger.error("=" * 60)
                db.session.rollback()
                _tl.__exit__(type(e), e, e.__traceback__)

    def round_progression_orchestrator(self):
        """
        Orchestrator job that ensures proper round progression.

        This job runs every 5 minutes and:
        1. Activates pending rounds that have 'tokens_generated' marker
        2. Checks if active rounds are ready to be processed
        3. Self-healing recovery: if no active/pending rounds exist for an
           organiser but active players remain, creates the missing next round.
           This unblocks the competition if the scheduler was offline when a
           round completed and evaluate_game_state_after_round was never called.

        PHASE REQUIREMENT: Round activation requires 'tokens_generated' marker,
        ensuring tokens exist for all eligible players before the round goes active.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            from lms_automation.services.timeline_context import TimelineContext
            _tl = TimelineContext('round_progression_orchestrator', trigger_type='scheduler', mode='auto')
            _tl.__enter__()
            try:
                logger.info("=" * 60)
                logger.info("=== ROUND ORCHESTRATOR JOB START ===")

                # Step 1: Check for pending rounds with fixtures and tokens_generated marker
                pending_rounds = Round.query.filter_by(status='pending').all()
                logger.info(f"Found {len(pending_rounds)} pending round(s)")

                for round_obj in pending_rounds:
                    # Log phase status for debugging
                    phase_status = self._get_phase_status(round_obj)
                    logger.info(
                        f"Round {round_obj.round_number} (id={round_obj.id}): phase={phase_status}"
                    )

                    # Check if fixtures exist
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    if not fixtures:
                        logger.info(f"Round {round_obj.round_number}: No fixtures yet, staying pending")
                        continue

                    # Check if tokens_generated marker exists
                    if not self._has_marker(round_obj, 'tokens_generated'):
                        logger.info(
                            f"Round {round_obj.round_number}: Missing 'tokens_generated' marker, "
                            f"waiting for token generation job"
                        )
                        continue

                    # Check if there are eligible players
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    if not eligible_players:
                        logger.warning(f"Round {round_obj.round_number}: No eligible players found")
                        continue

                    # ACTIVATION: Round has fixtures AND tokens_generated marker
                    _tl.checkpoint(f'before_activate_r{round_obj.round_number}')
                    _tl.decision(
                        title=f'Activate Round {round_obj.round_number}',
                        rule_matched='fixtures_exist_and_tokens_generated',
                        reasoning=(
                            f'{len(fixtures)} fixtures exist, tokens_generated marker present, '
                            f'{len(eligible_players)} eligible players'
                        ),
                        confidence='high',
                        intended_action='set_round_status_active',
                        expected_impact='Round becomes active; players can submit picks',
                    )
                    round_obj.status = 'active'
                    logger.info(
                        f"Round {round_obj.round_number}: ACTIVATED "
                        f"({len(fixtures)} fixtures, {len(eligible_players)} eligible players, "
                        f"tokens_generated=True)"
                    )
                    _tl.action(
                        action_type='round_activated',
                        outcome='applied',
                        actual_impact=f'round_id={round_obj.id} r{round_obj.round_number} -> active',
                        reversible=True,
                        rounds_delta=1,
                    )

                # Step 2: Check active rounds for completion readiness
                active_rounds = Round.query.filter_by(status='active').all()
                logger.info(f"Found {len(active_rounds)} active round(s)")

                for round_obj in active_rounds:
                    phase_status = self._get_phase_status(round_obj)
                    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                    completed_fixtures = [f for f in fixtures if f.status == 'completed']

                    logger.info(
                        f"Round {round_obj.round_number} (active): "
                        f"{len(completed_fixtures)}/{len(fixtures)} fixtures completed, "
                        f"phase={phase_status}"
                    )

                    # If all fixtures are completed, the process_eliminations job will handle it
                    if fixtures and all(f.status == 'completed' for f in fixtures):
                        logger.info(
                            f"Round {round_obj.round_number}: All fixtures completed, "
                            f"waiting for elimination processing"
                        )

                # ------------------------------------------------------------------
                # Step 3: Self-healing recovery.
                #
                # If the scheduler was offline when a round completed, the normal
                # path (process_eliminations → evaluate_game_state_after_round →
                # create_next_round) never ran.  The result is a competition stuck
                # with status='completed' on the last round and no active or pending
                # round to continue from.
                #
                # For each organiser that owns at least one round:
                #   • Skip if an active or pending round already exists.
                #   • Find the most recently completed round.
                #   • If there are still active players, create the next round.
                # ------------------------------------------------------------------
                from lms_automation.services.round_lifecycle import create_next_round

                organiser_ids_with_rounds = [
                    row[0]
                    for row in db.session.query(Round.organiser_id)
                    .filter(Round.organiser_id.isnot(None))
                    .distinct()
                    .all()
                ]

                for org_id in organiser_ids_with_rounds:
                    has_open_round = Round.query.filter(
                        Round.organiser_id == org_id,
                        Round.status.in_(['active', 'pending'])
                    ).first() is not None

                    if has_open_round:
                        continue

                    last_completed = Round.query.filter_by(
                        organiser_id=org_id,
                        status='completed'
                    ).order_by(Round.id.desc()).first()

                    if not last_completed:
                        continue

                    active_count = Player.query.filter_by(
                        organiser_id=org_id,
                        status='active'
                    ).count()

                    logger.warning(
                        "RECOVERY CHECK: organiser=%s has no active/pending rounds. "
                        "Last completed: Round %s (Cycle %s, id=%s). Active players: %s",
                        org_id,
                        last_completed.round_number,
                        last_completed.cycle_number,
                        last_completed.id,
                        active_count,
                    )

                    if active_count == 0:
                        logger.info(
                            "RECOVERY: organiser=%s — 0 active players, "
                            "competition concluded or awaiting rollover. Skipping.",
                            org_id,
                        )
                        continue

                    # Determine the next round number and cycle.
                    next_round_number = last_completed.round_number + 1
                    cycle_number = last_completed.cycle_number or 1
                    if last_completed.round_number == 20:
                        # End-of-cycle boundary — roll into the next cycle.
                        next_round_number = 1
                        cycle_number += 1

                    _tl.decision(
                        title=f'Recovery: create missing Round {next_round_number} for organiser {org_id}',
                        rule_matched='no_open_rounds_but_active_players',
                        reasoning=(
                            f'organiser={org_id}: no active/pending rounds after '
                            f'Round {last_completed.round_number} (id={last_completed.id}) '
                            f'completed; active_players={active_count}'
                        ),
                        confidence='high',
                        intended_action='create_next_round',
                        expected_impact=f'Round {next_round_number} (Cycle {cycle_number}) created as pending',
                    )

                    logger.warning(
                        "RECOVERY: Creating Round %s (Cycle %s) for organiser=%s to resume competition.",
                        next_round_number, cycle_number, org_id,
                    )

                    new_round = create_next_round(last_completed, next_round_number, cycle_number)
                    if new_round:
                        # create_next_round does not propagate organiser_id; fix that here.
                        # The outer db.session.commit() below will persist this update.
                        if new_round.organiser_id is None:
                            new_round.organiser_id = org_id
                        logger.warning(
                            "RECOVERY: Round %s (Cycle %s, id=%s) created for organiser=%s. "
                            "Competition resumes.",
                            next_round_number, cycle_number, new_round.id, org_id,
                        )
                        _tl.action(
                            action_type='recovery_round_created',
                            outcome='applied',
                            actual_impact=(
                                f'organiser={org_id}: round_id={new_round.id} '
                                f'r{next_round_number} cycle={cycle_number} -> pending'
                            ),
                            reversible=True,
                            rounds_delta=1,
                        )
                    else:
                        logger.warning(
                            "RECOVERY: Round %s (Cycle %s) already exists for organiser=%s "
                            "— no action needed.",
                            next_round_number, cycle_number, org_id,
                        )

                db.session.commit()
                logger.info("=== ROUND ORCHESTRATOR JOB COMPLETE ===")
                logger.info("=" * 60)
                _tl.__exit__(None, None, None)

            except Exception as e:
                logger.error(f"Error in round orchestrator: {e}")
                db.session.rollback()
                _tl.__exit__(type(e), e, e.__traceback__)

    def check_all_picks_submitted(self):
        """
        Check if all eligible players have submitted picks for active rounds.

        When all picks are submitted AND deadline has passed:
        1. Mark the round as "picks_locked" in special_note (prevents further edits)
        2. Publish picks so everyone can see who picked what

        CRITICAL GUARD: Picks should ONLY lock when:
        - deadline_passed == True (kickoff - 1 hour has passed), OR
        - Admin has triggered manual lock (special_note contains 'admin_locked')

        PHASE GUARD: Requires 'tokens_generated' marker before allowing picks_locked.
        This ensures tokens were created (prerequisite for announcements).

        IMPORTANT: This job does NOT complete rounds. It only marks them as picks_locked.
        Round completion is handled by process_eliminations after fixtures complete.
        """
        with self.app.app_context():
            self._ensure_db_session()  # Clean up stale connections
            try:
                logger.info("=" * 60)
                logger.info("=== ALL PICKS CHECK JOB START ===")

                # Check if announcement gate is disabled (emergency bypass)
                skip_announcement_gate = os.environ.get('SKIP_ANNOUNCEMENT_GATE', 'false').lower() == 'true'

                now = datetime.utcnow()

                active_rounds = Round.query.filter_by(status='active').all()

                for round_obj in active_rounds:
                    # Log phase status for debugging
                    phase_status = self._get_phase_status(round_obj)

                    # Get eligible players for this round
                    eligible_players = self._get_eligible_players_for_round(round_obj)
                    eligible_count = len(eligible_players)

                    # Count picks submitted for this round
                    picks_count = Pick.query.filter_by(round_id=round_obj.id).count()

                    # Determine deadline status
                    kickoff = round_obj.first_kickoff_at
                    kickoff_source = "first_kickoff_at field"

                    if not kickoff:
                        # Fallback: calculate from fixtures
                        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
                        for fixture in fixtures:
                            if fixture.date and fixture.time:
                                dt = datetime.combine(fixture.date, fixture.time)
                                if kickoff is None or dt < kickoff:
                                    kickoff = dt
                        kickoff_source = "calculated from fixtures"

                    # Ensure kickoff is naive UTC for comparison
                    if kickoff and kickoff.tzinfo is not None:
                        kickoff = kickoff.replace(tzinfo=None)

                    deadline = kickoff - timedelta(hours=1) if kickoff else None
                    deadline_passed = deadline and now >= deadline

                    # Check for admin manual lock
                    admin_locked = self._has_marker(round_obj, 'admin_locked')

                    # Already locked check
                    already_locked = self._has_marker(round_obj, 'picks_locked')

                    # Log comprehensive decision inputs (Task C logging)
                    logger.info(
                        f"Round {round_obj.round_number} (id={round_obj.id}): "
                        f"phase={phase_status}, picks={picks_count}/{eligible_count}, "
                        f"now_utc={now.strftime('%Y-%m-%d %H:%M')}, "
                        f"deadline_utc={deadline.strftime('%Y-%m-%d %H:%M') if deadline else 'None'}, "
                        f"deadline_passed={deadline_passed}, admin_locked={admin_locked}, "
                        f"already_locked={already_locked}"
                    )

                    if eligible_count == 0:
                        logger.info(f"  -> No eligible players, skipping")
                        continue

                    # Check if all eligible players have picks
                    if picks_count >= eligible_count:
                        # PHASE GUARD: Require tokens_generated before picks_locked
                        # This ensures the proper flow: tokens -> announcements -> picks
                        if not skip_announcement_gate and not self._has_marker(round_obj, 'tokens_generated'):
                            logger.warning(
                                f"  -> GUARD BLOCK: Cannot mark picks_locked "
                                f"without 'tokens_generated' marker. Waiting for token generation."
                            )
                            continue

                        # CRITICAL GUARD: Only lock picks if deadline has passed OR admin triggered lock
                        if not deadline_passed and not admin_locked:
                            logger.info(
                                f"  -> GUARD SKIP: All {picks_count} picks submitted but deadline NOT passed. "
                                f"Picks will lock when deadline passes (or admin triggers manual lock). "
                                f"Time until deadline: {deadline - now if deadline else 'N/A'}"
                            )
                            continue

                        # All conditions met - mark as locked and notify

                        # Mark as picks_locked (idempotent - check if already set)
                        if not already_locked:
                            self._add_marker(round_obj, 'picks_locked')
                            lock_reason = "deadline_passed" if deadline_passed else "admin_locked"
                            logger.info(
                                f"  -> PICKS LOCKED! ({picks_count}/{eligible_count}) "
                                f"reason={lock_reason}"
                            )
                        else:
                            logger.debug(
                                f"  -> Already marked as picks_locked"
                            )

                        # Send notification that picks are now visible (idempotent)
                        self._send_picks_published_notification(round_obj)
                    else:
                        logger.info(
                            f"  -> Waiting for more picks ({picks_count}/{eligible_count})"
                        )

                db.session.commit()
                logger.info("=== ALL PICKS CHECK JOB COMPLETE ===")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"Error checking all picks: {e}")
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                db.session.rollback()

    def _deliver_notifications(self):
        """APScheduler job: deliver all pending outbox rows via Telegram Bot API."""
        with self.app.app_context():
            try:
                from lms_automation.services.notifications import deliver_pending_notifications
                deliver_pending_notifications()
            except Exception as e:
                logger.error("deliver_notifications job failed: %s", e)

    def _run_timeline_retention(self):
        """APScheduler job: prune old run-timeline records per retention policy."""
        with self.app.app_context():
            try:
                from lms_automation.services.run_timeline import prune_old_data
                counts = prune_old_data()
                if any(counts.values()):
                    logger.info("Timeline retention pruned: %s", counts)
            except Exception as e:
                logger.error("timeline_retention job failed: %s", e)

    def _send_picks_published_notification(self, round_obj):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_picks_published_notification
        return send_picks_published_notification(round_obj)

    def _send_round_results_notification(self, round_obj, winners_count, eliminated_count, draw_count):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_round_results_notification
        return send_round_results_notification(round_obj, winners_count, eliminated_count, draw_count)

    # Fixed preference list for deterministic auto-picks (popular/strong teams first)
    TEAM_PREFERENCE_LIST = [
        'Arsenal', 'Man City', 'Liverpool', 'Chelsea', 'Man Utd', 'Tottenham',
        'Newcastle', 'Aston Villa', 'Brighton', 'West Ham', 'Bournemouth',
        'Fulham', 'Brentford', 'Crystal Palace', 'Wolves', 'Nottingham Forest',
        'Everton', 'Leicester', 'Ipswich', 'Southampton'
    ]

    def _get_auto_pick_team(self, player, round_obj):
        """
        Get auto-pick team for a player who missed the deadline.

        DETERMINISTIC Selection Algorithm:
        1. Round 1 of any cycle: Default to Arsenal if available
        2. Otherwise: First team from preference list (Arsenal, Man City, Liverpool, etc.)
        3. Fallback: Alphabetically first available team (stable sort)
        4. Last resort: Random (only if no other option - should never happen)

        IMPORTANT: All team names are CANONICAL (normalized).
        This ensures correct alphabetical ordering and consistent matching.

        Returns tuple: (canonical_team_name, auto_reason)
        - auto_reason: 'missed_deadline_default_arsenal', 'missed_deadline_preference',
                       'missed_deadline_fallback_first_available', 'missed_deadline_random_last_resort'
        """
        import random

        current_cycle = round_obj.cycle_number or 1
        current_round_number = round_obj.round_number or 1

        # Get teams in round - normalize to canonical names
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        teams_in_round_canonical = set()

        for fixture in fixtures:
            teams_in_round_canonical.add(normalize_team_name(fixture.home_team))
            teams_in_round_canonical.add(normalize_team_name(fixture.away_team))

        # Get teams used by player in this cycle (normalized)
        used_teams_canonical = set()
        cycle_picks = Pick.query.filter_by(player_id=player.id).join(Round).filter(
            Round.cycle_number == current_cycle
        ).order_by(Round.round_number.desc()).all()

        for pick in cycle_picks:
            used_teams_canonical.add(normalize_team_name(pick.team_picked))

        # Available teams (canonical names)
        available_canonical = teams_in_round_canonical - used_teams_canonical

        logger.info(
            f"  Auto-pick for player_id={player.id} ({player.name}): "
            f"Round {current_round_number}, Cycle {current_cycle}, "
            f"teams_in_round={len(teams_in_round_canonical)}, "
            f"teams_used={len(used_teams_canonical)}, "
            f"available={len(available_canonical)}"
        )

        if not available_canonical:
            logger.warning(
                f"  No available teams for player_id={player.id} ({player.name}) - "
                f"all {len(teams_in_round_canonical)} teams in round have been used"
            )
            return None, None

        # RULE 1: Walk back through player's prior WINNING picks (most recent → oldest)
        # and take the opposing team (the loser in that past match) if it's eligible this round.
        # (Eligibility already encoded by available_canonical.)

        # RULE 2 (UPDATED): "previous round that lost" with step-back
        # Target = opponent team from the player's most recent WINNING pick.
        # If that team is not available (already used in cycle / not in this round's fixtures),
        # step back to the previous round, etc.
        if current_round_number >= 2:
            try:
                # Iterate backwards through rounds in this cycle
                for prev_rn in range(current_round_number - 1, 0, -1):
                    prev_pick = Pick.query.filter_by(player_id=player.id).join(Round).filter(
                        Round.cycle_number == current_cycle,
                        Round.round_number == prev_rn
                    ).first()

                    if not prev_pick or prev_pick.is_winner is not True:
                        continue

                    prev_round = prev_pick.round
                    opponent = None
                    for fx in (prev_round.fixtures or []):
                        if normalize_team_name(fx.home_team) == normalize_team_name(prev_pick.team_picked):
                            opponent = normalize_team_name(fx.away_team)
                            break
                        if normalize_team_name(fx.away_team) == normalize_team_name(prev_pick.team_picked):
                            opponent = normalize_team_name(fx.home_team)
                            break

                    if not opponent:
                        continue

                    # Candidate must be available this round (and not previously used in the cycle)
                    if opponent in available_canonical:
                        logger.info(
                            f"  AUTO-PICK PREV-LOSER (step-back): player_id={player.id} ({player.name}) -> '{opponent}' "
                            f"(from prev_round={prev_rn}, last winning pick {normalize_team_name(prev_pick.team_picked)})"
                        )
                        return opponent, 'missed_deadline_prev_round_loser'
            except Exception as e:
                logger.warning(f"  Prev-loser auto-pick (step-back) failed for player_id={player.id}: {e}")

        # RULE 2: Fallback — first eligible team alphabetically among teams not yet used this cycle
        available_sorted = sorted(available_canonical, key=str.lower)
        if available_sorted:
            selected_canonical = available_sorted[0]
            logger.info(
                f"  AUTO-PICK FALLBACK ALPHA: player_id={player.id} ({player.name}) -> "
                f"'{selected_canonical}' (first of {len(available_sorted)} alphabetically)"
            )
            return selected_canonical, 'missed_deadline_fallback_alpha'

        # RULE 4: Last resort - random (should never happen if available_canonical is not empty)
        available_list = list(available_canonical)
        selected = random.choice(available_list)
        logger.warning(
            f"  AUTO-PICK RANDOM LAST RESORT: player_id={player.id} ({player.name}) -> "
            f"'{selected}' (random from {len(available_list)} teams)"
        )
        return selected, 'missed_deadline_random_last_resort'

    def _send_telegram_message(self, telegram_id, message, button_url=None, button_text="Make Your Pick"):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_telegram_message
        return send_telegram_message(telegram_id, message, button_url, button_text)

    def _send_winner_notification(self, winner, completed_round):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_winner_notification
        return send_winner_notification(winner, completed_round)

    def _send_rollover_notification(self, new_cycle):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_rollover_notification
        return send_rollover_notification(new_cycle)

    def _send_cycle_complete_notification(self, survivors, new_cycle):
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import send_cycle_complete_notification
        return send_cycle_complete_notification(survivors, new_cycle)

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
        """Delegate to canonical eligibility function in eligibility.py."""
        return get_eligible_players_for_round(round_obj)

    # ==================== PHASE MARKER HELPERS ====================

    def _has_marker(self, round_obj, marker: str) -> bool:
        """Delegates to services.markers."""
        from lms_automation.services.markers import has_marker
        return has_marker(round_obj, marker)

    def _add_marker(self, round_obj, marker: str) -> bool:
        """Delegates to services.markers."""
        from lms_automation.services.markers import add_marker
        return add_marker(round_obj, marker)

    def _ensure_tokens_for_round(self, round_obj) -> dict:
        """
        Ensure all eligible players have tokens for a round (idempotent).

        Called inline from send_new_round_announcements when tokens_generated
        marker is missing, ensuring tokens exist before sending pick links.

        Returns:
            dict with 'created' and 'total' counts
        """
        eligible_players = self._get_eligible_players_for_round(round_obj)
        eligible_count = len(eligible_players)
        tokens_created = 0

        if eligible_count == 0:
            logger.warning(
                f"Round {round_obj.round_number}: No eligible players for token generation"
            )
            return {'created': 0, 'total': 0}

        for player in eligible_players:
            # Check if token already exists (idempotent)
            existing_token = PickToken.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()

            if not existing_token:
                token = PickToken.create_for_player_round(
                    player.id, round_obj.id, expires_hours=168
                )
                db.session.add(token)
                logger.debug(
                    f"  Created token for player '{player.name}' (id={player.id})"
                )
                tokens_created += 1

        # Count final tokens
        final_token_count = PickToken.query.filter_by(round_id=round_obj.id).count()

        # Set marker if all tokens exist
        if final_token_count >= eligible_count and not self._has_marker(round_obj, 'tokens_generated'):
            self._add_marker(round_obj, 'tokens_generated')

        logger.info(
            f"Tokens: created {tokens_created}/{eligible_count}; marker set"
        )

        return {'created': tokens_created, 'total': final_token_count}

    def _get_phase_status(self, round_obj) -> dict:
        """Delegates to services.markers."""
        from lms_automation.services.markers import get_phase_status
        return get_phase_status(round_obj)

    def announce_round_now(self, round_id: int) -> dict:
        """
        Immediately announce a round to all eligible players (public API).

        This is the canonical announcement function that:
        1. Ensures tokens exist for all eligible players
        2. Sends pick-link messages to players with telegram_id
        3. Schedules reminders (4h and 2h before deadline)
        4. Sets the 'announced' marker

        Idempotency:
        - If 'announced' marker exists, returns immediately with skipped_already_announced=1
        - Token generation is idempotent (no duplicates)
        - Players who already have ReminderSchedule are skipped (already notified)

        Works in MANUAL_MODE (announcements are allowed regardless of manual_mode setting).

        Args:
            round_id: The round ID to announce

        Returns:
            dict: {
                success: bool,
                round_id: int,
                sent: int,
                skipped_missing_telegram: int,
                skipped_already_announced: int,
                errors: int,
                error: str (only if success=False)
            }
        """
        # Load round
        round_obj = Round.query.get(round_id)
        if not round_obj:
            logger.warning(f"announce_round_now: Round {round_id} not found")
            return {
                'success': False,
                'round_id': round_id,
                'sent': 0,
                'skipped_missing_telegram': 0,
                'skipped_already_announced': 0,
                'errors': 0,
                'error': f'Round {round_id} not found'
            }

        # Idempotency: skip if already announced
        if self._has_marker(round_obj, 'announced'):
            logger.info(
                f"Announce round now: sent=0 skipped_missing_telegram=0 "
                f"skipped_already_announced=1 errors=0"
            )
            return {
                'success': True,
                'round_id': round_id,
                'sent': 0,
                'skipped_missing_telegram': 0,
                'skipped_already_announced': 1,
                'errors': 0
            }

        # Ensure tokens exist (idempotent)
        if not self._has_marker(round_obj, 'tokens_generated'):
            self._ensure_tokens_for_round(round_obj)
            db.session.flush()

        # Get eligible players
        eligible_players = self._get_eligible_players_for_round(round_obj)

        sent = 0
        skipped_missing_telegram = 0
        skipped_already_notified = 0
        skipped_not_active = 0
        skipped_already_picked = 0
        skipped_no_token = 0
        errors = 0

        for player in eligible_players:
            # Skip non-active players
            if player.status != 'active':
                skipped_not_active += 1
                continue

            # Skip if already has a pick
            if self._player_has_pick_for_round(player.id, round_obj.id):
                skipped_already_picked += 1
                continue

            # Get pick token
            pick_token = PickToken.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()

            if not pick_token:
                skipped_no_token += 1
                logger.warning(
                    f"  Player {player.name} (id={player.id}): No token, cannot send announcement"
                )
                continue

            # Skip if already notified (reminder exists = already sent)
            existing_reminder = ReminderSchedule.query.filter_by(
                player_id=player.id,
                round_id=round_obj.id
            ).first()
            if existing_reminder:
                skipped_already_notified += 1
                continue

            # Check for telegram_id
            if not (hasattr(player, 'telegram_id') and player.telegram_id):
                skipped_missing_telegram += 1
                logger.warning(
                    f"  Player {player.name} (id={player.id}): missing telegram_id"
                )
                continue

            # Build announcement message and enqueue for async delivery
            pick_url = f"{os.environ.get('BASE_URL', 'http://localhost:5000')}/pick/{pick_token.token}"

            if round_obj.first_kickoff_at:
                deadline = round_obj.first_kickoff_at - timedelta(hours=1)
                deadline_str = deadline.strftime('%A %d %B at %H:%M')
            else:
                deadline_str = "soon"

            message = f"⚽ NEW ROUND {round_obj.round_number} IS LIVE!\n\n"
            message += f"Make your pick before {deadline_str}"

            from lms_automation.services.notifications import enqueue_notification
            enqueue_notification(
                player.telegram_id,
                message,
                button_url=pick_url,
                button_text="⚽ Make Your Pick",
                round_id=round_obj.id,
                idempotency_key=f"round_announcement:{player.telegram_id}:{round_obj.id}",
            )
            sent += 1
            logger.info(f"  Queued announcement for {player.name} (id={player.id})")

            # Schedule reminders unconditionally — delivery outcome is async
            if round_obj.first_kickoff_at:
                _now = datetime.utcnow()

                # 24-hour reminder — only schedule if still in the future
                _reminder_24h_time = round_obj.first_kickoff_at - timedelta(hours=24)
                if _reminder_24h_time > _now:
                    db.session.add(ReminderSchedule(
                        player_id=player.id,
                        round_id=round_obj.id,
                        reminder_type='24_hour',
                        scheduled_time=_reminder_24h_time,
                        is_sent=False,
                    ))

                reminder_4h = ReminderSchedule(
                    player_id=player.id,
                    round_id=round_obj.id,
                    reminder_type='4_hour',
                    scheduled_time=round_obj.first_kickoff_at - timedelta(hours=4),
                    is_sent=False,
                )
                db.session.add(reminder_4h)

                reminder_2h = ReminderSchedule(
                    player_id=player.id,
                    round_id=round_obj.id,
                    reminder_type='2_hour',
                    scheduled_time=round_obj.first_kickoff_at - timedelta(hours=2),
                    is_sent=False,
                )
                db.session.add(reminder_2h)

        # Set 'announced' marker after processing (even if some players missing telegram)
        self._add_marker(round_obj, 'announced')
        db.session.commit()

        logger.info(
            f"Announce round now: sent={sent} skipped_missing_telegram={skipped_missing_telegram} "
            f"skipped_already_announced=0 errors={errors}"
        )

        return {
            'success': True,
            'round_id': round_id,
            'sent': sent,
            'skipped_missing_telegram': skipped_missing_telegram,
            'skipped_already_announced': 0,
            'errors': errors
        }

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

    # ==================== LEAN & CLEAN MESSAGING HELPERS ====================

    def _is_player_active(self, player_id: int) -> bool:
        """
        Check if a player has status='active'.

        Args:
            player_id: The player's database ID

        Returns:
            True if player.status == 'active', False otherwise
        """
        player = Player.query.get(player_id)
        return player is not None and player.status == 'active'

    def _player_participated_in_round(self, player_id: int, round_id: int) -> bool:
        """
        Check if a player participated in a round (i.e., has a pick for that round).

        Args:
            player_id: The player's database ID
            round_id: The round's database ID

        Returns:
            True if the player has a pick for this round, False otherwise
        """
        return self._player_has_pick_for_round(player_id, round_id)

    def _get_picks_grid_url(self) -> str:
        """Delegates to services.telegram_dispatch."""
        from lms_automation.services.telegram_dispatch import get_picks_grid_url
        return get_picks_grid_url()


def check_db_integrity():
    """
    DB Integrity Check Helper.

    Detects common data integrity issues:
    1. Rounds with 0 picks but status='completed'
    2. Fixtures with 'fallback' in event_id (invalid fallback fixtures)
    3. Duplicate fixtures (same teams in same round)
    4. Picks referencing teams not in fixtures for that round
    5. Picks with is_winner=False but is_eliminated=False (invariant violation)

    Returns:
        dict with keys:
            - 'issues': list of issue descriptions
            - 'counts': dict of issue counts by type
            - 'ok': True if no issues found, False otherwise

    Usage:
        from lms_automation.scheduler import check_db_integrity
        result = check_db_integrity()
        if not result['ok']:
            for issue in result['issues']:
                print(issue)
    """
    issues = []
    counts = {
        'completed_rounds_no_picks': 0,
        'fallback_fixtures': 0,
        'duplicate_fixtures': 0,
        'picks_invalid_teams': 0,
        'elimination_invariant_violations': 0,
    }

    # Import here to avoid circular dependency
    from lms_automation.models import Round, Fixture, Pick
    from lms_automation.team_utils import normalize_team_name

    # 1. Check for rounds with 0 picks but status='completed'
    completed_rounds = Round.query.filter_by(status='completed').all()
    for round_obj in completed_rounds:
        picks_count = Pick.query.filter_by(round_id=round_obj.id).count()
        if picks_count == 0:
            issues.append(
                f"INTEGRITY ERROR: Round {round_obj.round_number} (id={round_obj.id}) is 'completed' "
                f"but has 0 picks. This should never happen."
            )
            counts['completed_rounds_no_picks'] += 1

    # 2. Check for fallback fixtures (invalid team data)
    fallback_fixtures = Fixture.query.filter(
        Fixture.event_id.like('fallback_%')
    ).all()
    for fixture in fallback_fixtures:
        issues.append(
            f"INTEGRITY WARNING: Fallback fixture detected - Round {fixture.round_id}: "
            f"{fixture.home_team} vs {fixture.away_team} (event_id={fixture.event_id}). "
            f"This may contain invalid/outdated team data."
        )
        counts['fallback_fixtures'] += 1

    # 3. Check for duplicate fixtures (same teams in same round)
    all_rounds = Round.query.all()
    for round_obj in all_rounds:
        fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
        seen_matchups = set()
        for fixture in fixtures:
            # Normalize and create a canonical matchup key
            home = normalize_team_name(fixture.home_team)
            away = normalize_team_name(fixture.away_team)
            matchup = tuple(sorted([home, away]))  # Sorted to catch A vs B and B vs A

            if matchup in seen_matchups:
                issues.append(
                    f"INTEGRITY ERROR: Duplicate fixture in Round {round_obj.round_number} - "
                    f"{fixture.home_team} vs {fixture.away_team}"
                )
                counts['duplicate_fixtures'] += 1
            else:
                seen_matchups.add(matchup)

    # 4. Check for picks referencing teams not in fixtures for that round
    all_picks = Pick.query.all()
    for pick in all_picks:
        fixtures = Fixture.query.filter_by(round_id=pick.round_id).all()
        fixture_teams = set()
        for fx in fixtures:
            fixture_teams.add(normalize_team_name(fx.home_team))
            fixture_teams.add(normalize_team_name(fx.away_team))

        pick_team = normalize_team_name(pick.team_picked)
        if pick_team not in fixture_teams and fixtures:  # Only flag if fixtures exist
            issues.append(
                f"INTEGRITY WARNING: Pick {pick.id} by {pick.player.name} for Round "
                f"{pick.round.round_number} references team '{pick.team_picked}' "
                f"(normalized: '{pick_team}') which is not in any fixture for this round."
            )
            counts['picks_invalid_teams'] += 1

    # 5. Check for elimination invariant violations (is_winner=False but is_eliminated=False)
    violating_picks = Pick.query.filter_by(
        is_winner=False,
        is_eliminated=False
    ).all()
    for pick in violating_picks:
        issues.append(
            f"INTEGRITY ERROR: Elimination invariant violation - Pick {pick.id} by "
            f"{pick.player.name} has is_winner=False but is_eliminated=False. "
            f"This violates the rule: losing = eliminated."
        )
        counts['elimination_invariant_violations'] += 1

    # Summary
    total_issues = sum(counts.values())

    return {
        'issues': issues,
        'counts': counts,
        'ok': total_issues == 0,
        'total_issues': total_issues,
    }


def run_integrity_check_with_logging():
    """
    Run DB integrity check and log results.

    Convenience wrapper that logs all issues found.
    """
    result = check_db_integrity()

    if result['ok']:
        logger.info("=" * 60)
        logger.info("DB INTEGRITY CHECK: ALL OK (no issues found)")
        logger.info("=" * 60)
    else:
        logger.error("=" * 60)
        logger.error(f"DB INTEGRITY CHECK: FOUND {result['total_issues']} ISSUE(S)")
        logger.error("=" * 60)
        logger.error(f"Issue counts: {result['counts']}")
        for issue in result['issues']:
            logger.error(f"  {issue}")
        logger.error("=" * 60)

    return result


# Create global scheduler instance
scheduler = LMSScheduler()
