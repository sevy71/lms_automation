from datetime import datetime, timedelta
from lms_automation.extensions import db
import secrets
import string
import os



class Organiser(db.Model):
    """Tenant-level isolation for multi-organiser support (Phase 1 foundation).

    Each organiser owns their own set of players, rounds, and game data.
    The 'default' organiser is auto-created during migration and owns all
    pre-existing data so backward compatibility is preserved.
    """
    __tablename__ = 'organisers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(50), nullable=False, unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default='active')  # active, suspended
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Relationships — lazy='dynamic' so queries remain chainable for scoping
    players = db.relationship('Player', backref='organiser', lazy='dynamic',
                              foreign_keys='Player.organiser_id')
    rounds = db.relationship('Round', backref='organiser', lazy='dynamic',
                             foreign_keys='Round.organiser_id')

    def __repr__(self):
        return f'<Organiser {self.slug}>'


class AdminUser(db.Model):
    """Per-organiser admin accounts (Phase 2c).

    Roles:
    - organiser_admin: can manage their own organiser's data only
    - super_admin: can manage all organisers + create admin accounts

    The default super-admin is bootstrapped during migration from the legacy
    ADMIN_PASSWORD env var so existing deployments keep working without manual
    steps.
    """
    __tablename__ = 'admin_users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False,
                     default='organiser_admin')  # organiser_admin | super_admin
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    organiser = db.relationship('Organiser', backref='admin_users', lazy=True,
                                foreign_keys=[organiser_id])

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<AdminUser {self.username} role={self.role}>'


class Player(db.Model):
    __tablename__ = 'players'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    whatsapp_number = db.Column(db.String(20), nullable=True)
    telegram_id = db.Column(db.String(50), nullable=True)  # Telegram chat ID for notifications
    status = db.Column(db.String(20), default='active')  # active, eliminated, winner
    unreachable = db.Column(db.Boolean, default=False)

    # Multi-organiser support (Phase 1). Set NOT NULL by migration after backfill.
    # TODO Phase 2: enforce NOT NULL constraint on creation path once all organisers are wired.
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=True, index=True)

    picks = db.relationship('Pick', backref='player', lazy=True)

    def __repr__(self):
        return f'<Player {self.name}>'


class Round(db.Model):
    __tablename__ = 'rounds'

    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    pl_matchday = db.Column(db.Integer, nullable=True)  # Premier League matchday (1-38)
    start_date = db.Column(db.DateTime, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, active, completed
    first_kickoff_at = db.Column(db.DateTime, nullable=True)
    special_measure = db.Column(db.String(50), nullable=True)  # universal_bye, frozen, void, override
    special_note = db.Column(db.Text, nullable=True)
    cycle_number = db.Column(db.Integer, default=1)

    # Season tracking (season locked per game)
    season_id = db.Column(db.String(10), nullable=True)
    api_season_year = db.Column(db.Integer, nullable=True)

    # Multi-organiser support (Phase 1). Set NOT NULL by migration after backfill.
    # TODO Phase 2: enforce NOT NULL constraint on creation path once all organisers are wired.
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=True, index=True)

    fixtures = db.relationship('Fixture', backref='round', lazy=True)
    picks = db.relationship('Pick', backref='round', lazy=True)

    def __repr__(self):
        return f'<Round {self.round_number} (PL MD {self.pl_matchday})>'

    def get_season_id(self):
        """Compatibility helper: rounds table may contain season_id column."""
        return getattr(self, 'season_id', None)

    def get_api_season_year(self):
        """Compatibility helper: rounds table may contain api_season_year column."""
        return getattr(self, 'api_season_year', None)


class Fixture(db.Model):
    __tablename__ = 'fixtures'

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    event_id = db.Column(db.String(50), nullable=True)  # External API event ID
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=True)
    time = db.Column(db.Time, nullable=True)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='scheduled')  # scheduled, live, completed, postponed

    def __repr__(self):
        return f'<Fixture {self.home_team} vs {self.away_team}>'


class Pick(db.Model):
    __tablename__ = 'picks'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    team_picked = db.Column(db.String(100), nullable=False)
    is_winner = db.Column(db.Boolean, nullable=True)  # None=pending, True=won, False=lost
    is_eliminated = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    last_edited_at = db.Column(db.DateTime, nullable=True)

    # Audit fields for auto-pick/postponement policy
    auto_assigned = db.Column(db.Boolean, default=False)
    auto_reason = db.Column(db.String(50), nullable=True)  # missed_deadline, postponement_early, postponement_late, etc.
    postponed_event_id = db.Column(db.String(50), nullable=True)
    announcement_time = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<Pick {self.player.name} - {self.team_picked}>'


class PickToken(db.Model):
    __tablename__ = 'pick_tokens'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    is_used = db.Column(db.Boolean, default=False)
    edit_count = db.Column(db.Integer, default=0)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    player = db.relationship('Player', backref='pick_tokens', lazy=True)
    round = db.relationship('Round', backref='pick_tokens', lazy=True)

    def __repr__(self):
        return f'<PickToken {self.token[:8]}... for {self.player.name if self.player else "Unknown"}>'

    @staticmethod
    def generate_token():
        """Generate a secure random token"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(32))

    @staticmethod
    def create_for_player_round(player_id, round_id, expires_hours=None, force_new=False):
        """Create or fetch a pick token for a player and round.

        Expiry is determined dynamically so tokens survive long gaps between rounds:
          1. Preferred: round.first_kickoff_at minus PICK_TOKEN_LOCK_BUFFER_MINUTES (default 15)
          2. Fallback:  round.end_date if first_kickoff_at is absent/past
          3. Last resort: now + PICK_TOKEN_DEFAULT_HOURS (env, default 168 h)
          4. Hard cap:  min(computed_expiry, now + PICK_TOKEN_MAX_HOURS) (env, default 8760 h = 1 yr)

        Tokens must still expire before (or at) kickoff so no post-kickoff picks slip through.
        Expired tokens are regenerated automatically (not forced, just not re-used).
        """
        # Read env config (falls back to sensible defaults)
        default_hours = int(os.environ.get('PICK_TOKEN_DEFAULT_HOURS', 168))
        max_hours = int(os.environ.get('PICK_TOKEN_MAX_HOURS', 8760))
        lock_buffer_minutes = int(os.environ.get('PICK_TOKEN_LOCK_BUFFER_MINUTES', 15))

        # Use caller-supplied expires_hours only when explicitly provided
        if expires_hours is None:
            expires_hours = default_hours

        # Try to reuse an existing valid token unless forced
        if not force_new:
            existing_token = PickToken.query.filter_by(
                player_id=player_id,
                round_id=round_id
            ).first()
            if existing_token and existing_token.is_valid():
                return existing_token

        # Determine expiry anchored to round lifecycle
        round_obj = Round.query.get(round_id)
        now = datetime.utcnow()
        expires_at = None

        if round_obj:
            # Primary anchor: first kickoff minus safety buffer
            if round_obj.first_kickoff_at and round_obj.first_kickoff_at > now:
                expires_at = round_obj.first_kickoff_at - timedelta(minutes=lock_buffer_minutes)
            # Secondary anchor: end_date
            elif round_obj.end_date and round_obj.end_date > now:
                expires_at = round_obj.end_date

        # Last resort: fixed TTL from env/caller
        if expires_at is None or expires_at <= now:
            expires_at = now + timedelta(hours=expires_hours)

        # Apply hard cap so tokens cannot outlive PICK_TOKEN_MAX_HOURS
        max_expiry = now + timedelta(hours=max_hours)
        if expires_at > max_expiry:
            expires_at = max_expiry

        # Create new token
        token = PickToken(
            player_id=player_id,
            round_id=round_id,
            token=PickToken.generate_token(),
            expires_at=expires_at
        )

        db.session.add(token)
        return token

    def is_valid(self):
        """Check if token is valid (not exceeded edit limit and not expired)"""
        if self.edit_count >= 2:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def mark_used(self):
        """Increment edit count and update used_at timestamp"""
        self.edit_count += 1
        self.used_at = datetime.utcnow()
        if self.edit_count >= 2:
            self.is_used = True

    def get_pick_url(self, base_url='https://localhost:5000'):
        """Get the full pick URL for this token"""
        # Ensure base_url is clean and properly formatted
        base_url = base_url.rstrip('/')

        # Ensure base_url has protocol (critical for mobile WhatsApp)
        if not base_url.startswith(('http://', 'https://')):
            base_url = f"https://{base_url}"

        return f"{base_url}/pick/{self.token}"


class ReminderSchedule(db.Model):
    __tablename__ = 'reminder_schedules'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # '24_hour', '4_hour', or '2_hour'
    scheduled_time = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)
    is_sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    player = db.relationship('Player', backref='reminder_schedules')
    round = db.relationship('Round', backref='reminder_schedules')

    def __repr__(self):
        return f'<ReminderSchedule {self.reminder_type} for {self.player.name} R{self.round.round_number}>'

    @staticmethod
    def create_reminders_for_round(round_id):
        """Create reminder schedules for all active players in a round.
        Anchors reminders to first_kickoff_at when available, otherwise end_date.
        Generates 24-hour, 4-hour, and 2-hour reminders before the anchor time.
        The 24-hour reminder is skipped if the anchor time is already within 24 hours
        (i.e. the round was created too late for a 24-hour heads-up).
        """
        round_obj = Round.query.get(round_id)
        if not round_obj:
            return False

        # Determine anchor time (prefer first_kickoff_at; fallback to end_date)
        anchor_time = round_obj.first_kickoff_at or round_obj.end_date
        if not anchor_time:
            # Try derive from fixtures
            try:
                fixtures = round_obj.fixtures or []
                earliest = None
                for fx in fixtures:
                    if getattr(fx, 'date', None) and getattr(fx, 'time', None):
                        dt = datetime.combine(fx.date, fx.time)
                        if earliest is None or dt < earliest:
                            earliest = dt
                anchor_time = earliest
            except Exception:
                anchor_time = None
        if not anchor_time:
            return False

        now = datetime.utcnow()
        active_players = Player.query.filter_by(status='active').all()

        # Calculate reminder times relative to anchor
        twenty_four_hour_reminder = anchor_time - timedelta(hours=24)
        four_hour_reminder = anchor_time - timedelta(hours=4)
        two_hour_reminder = anchor_time - timedelta(hours=2)

        # Only schedule 24h reminder if it's still in the future
        schedule_24h = twenty_four_hour_reminder > now

        reminders_created = 0

        for player in active_players:
            # Check if reminders already exist
            existing_24h = ReminderSchedule.query.filter_by(
                player_id=player.id,
                round_id=round_id,
                reminder_type='24_hour'
            ).first()

            existing_4h = ReminderSchedule.query.filter_by(
                player_id=player.id,
                round_id=round_id,
                reminder_type='4_hour'
            ).first()

            existing_2h = ReminderSchedule.query.filter_by(
                player_id=player.id,
                round_id=round_id,
                reminder_type='2_hour'
            ).first()

            # Create 24-hour reminder only if it's still in the future (skip for late-created rounds)
            if not existing_24h and schedule_24h:
                reminder_24h = ReminderSchedule(
                    player_id=player.id,
                    round_id=round_id,
                    reminder_type='24_hour',
                    scheduled_time=twenty_four_hour_reminder
                )
                db.session.add(reminder_24h)
                reminders_created += 1

            # Create 4-hour reminder (create even if scheduled time is in the past so it shows as due)
            if not existing_4h:
                reminder_4h = ReminderSchedule(
                    player_id=player.id,
                    round_id=round_id,
                    reminder_type='4_hour',
                    scheduled_time=four_hour_reminder
                )
                db.session.add(reminder_4h)
                reminders_created += 1

            # Create 2-hour reminder (create even if scheduled time is in the past so it shows as due)
            if not existing_2h:
                reminder_2h = ReminderSchedule(
                    player_id=player.id,
                    round_id=round_id,
                    reminder_type='2_hour',
                    scheduled_time=two_hour_reminder
                )
                db.session.add(reminder_2h)
                reminders_created += 1

        db.session.commit()
        return reminders_created

    @staticmethod
    def get_pending_reminders():
        """Get all reminders that are due and haven't been sent"""
        return ReminderSchedule.query.filter(
            ReminderSchedule.is_sent == False,
            ReminderSchedule.scheduled_time <= datetime.utcnow()
        ).all()

    def mark_as_sent(self):
        """Mark reminder as sent"""
        self.is_sent = True
        self.sent_at = datetime.utcnow()
        db.session.commit()


# ---------------------------------------------------------------------------
# Notification outbox (transactional outbox pattern)
# ---------------------------------------------------------------------------

class NotificationOutbox(db.Model):
    """
    Pending, sent, and dead Telegram notifications.

    Game-state code writes rows to this table inside the same DB transaction as
    the state changes.  A separate scheduler job reads pending rows and delivers
    them via the Telegram Bot API with configurable retries.

    Status transitions:
      pending  ->  sent   (delivered on any attempt)
      pending  ->  dead   (all attempts exhausted; max_attempts reached)
    """
    __tablename__ = 'notification_outbox'

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=True, index=True)
    telegram_id = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    button_url = db.Column(db.String(500), nullable=True)
    button_text = db.Column(db.String(200), nullable=True)
    # Deterministic idempotency key: '{event}:{telegram_id}:{scope}'.
    # Nullable — rows without a key are always inserted.
    # enqueue_notification() skips insert when an active (pending/sent) row with
    # the same key already exists, preventing duplicate notifications on retry.
    idempotency_key = db.Column(db.String(255), nullable=True, index=True)
    # pending / sent / dead
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return (
            f'<NotificationOutbox id={self.id} status={self.status} '
            f'attempts={self.attempts}/{self.max_attempts} '
            f'telegram_id={self.telegram_id}>'
        )


# ---------------------------------------------------------------------------
# Run Timeline — Automation runs, decisions, actions, checkpoints, audit
# ---------------------------------------------------------------------------

class AutomationRun(db.Model):
    """
    One execution of the automation engine (scheduler job or manual trigger).

    Every significant automated or manual run should create a record here so
    admins can audit what happened and, when needed, roll back or replay.
    """
    __tablename__ = 'automation_runs'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.String(36), nullable=False, unique=True, index=True)  # UUID
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=True, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    trigger_type = db.Column(db.String(20), nullable=False, default='scheduler')  # scheduler/manual/api
    mode = db.Column(db.String(10), nullable=False, default='auto')  # auto/manual
    # running / success / warn / failed / rolled-back
    status = db.Column(db.String(20), nullable=False, default='running', index=True)
    total_actions = db.Column(db.Integer, nullable=False, default=0)
    affected_players = db.Column(db.Integer, nullable=False, default=0)
    affected_rounds = db.Column(db.Integer, nullable=False, default=0)
    affected_reminders = db.Column(db.Integer, nullable=False, default=0)
    error_info = db.Column(db.Text, nullable=True)

    decisions = db.relationship('RunDecision', backref='run', lazy='dynamic',
                                cascade='all, delete-orphan')
    actions = db.relationship('RunAction', backref='run', lazy='dynamic',
                              cascade='all, delete-orphan')
    checkpoints = db.relationship('RunCheckpoint', backref='run', lazy='dynamic')

    def __repr__(self):
        return f'<AutomationRun {self.run_id} status={self.status}>'


class RunDecision(db.Model):
    """
    A single decision recorded before a critical action is applied.

    Captures the rule that matched, reasoning, confidence, and expected impact
    so admins understand *why* the automation did what it did.
    """
    __tablename__ = 'run_decisions'

    id = db.Column(db.Integer, primary_key=True)
    run_id_fk = db.Column(db.Integer, db.ForeignKey('automation_runs.id'),
                          nullable=False, index=True)
    step_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    rule_matched = db.Column(db.String(200), nullable=True)
    reasoning = db.Column(db.Text, nullable=True)
    confidence = db.Column(db.String(20), nullable=True)  # high / medium / low
    intended_action = db.Column(db.String(200), nullable=True)
    expected_impact = db.Column(db.Text, nullable=True)
    state = db.Column(db.String(20), nullable=False, default='applied')  # applied / skipped
    decided_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    actions = db.relationship('RunAction', backref='decision', lazy='dynamic')

    def __repr__(self):
        return f'<RunDecision step={self.step_number} state={self.state}>'


class RunAction(db.Model):
    """
    The persisted outcome of applying (or skipping) a decision.

    Written after the operation completes so it reflects actual impact.
    """
    __tablename__ = 'run_actions'

    id = db.Column(db.Integer, primary_key=True)
    run_id_fk = db.Column(db.Integer, db.ForeignKey('automation_runs.id'),
                          nullable=False, index=True)
    decision_id = db.Column(db.Integer, db.ForeignKey('run_decisions.id'),
                            nullable=True, index=True)
    action_type = db.Column(db.String(100), nullable=False)
    # applied / skipped / failed / reverted
    outcome = db.Column(db.String(20), nullable=False, default='applied')
    actual_impact = db.Column(db.Text, nullable=True)
    actor = db.Column(db.String(100), nullable=False, default='system')
    reversible = db.Column(db.Boolean, nullable=False, default=True)
    reversal_metadata = db.Column(db.Text, nullable=True)  # JSON blob
    executed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<RunAction type={self.action_type} outcome={self.outcome}>'


class RunCheckpoint(db.Model):
    """
    Point-in-time snapshot of critical game state.

    Created before each critical transition (round completion, eliminations,
    bulk reminders, auto-pick application) and at run start/end.

    The snapshot JSON stores enough state to restore the game safely:
      - round statuses and special_note markers
      - player statuses (active/eliminated/winner)
      - picks for current rounds (is_winner, is_eliminated, team_picked)
      - pick token validity flags
      - relevant config flags
    """
    __tablename__ = 'run_checkpoints'

    id = db.Column(db.Integer, primary_key=True)
    checkpoint_id = db.Column(db.String(36), nullable=False, unique=True, index=True)
    run_id_fk = db.Column(db.Integer, db.ForeignKey('automation_runs.id'),
                          nullable=True, index=True)
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=True, index=True)
    label = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    snapshot = db.Column(db.Text, nullable=False)  # JSON state snapshot

    def __repr__(self):
        return f'<RunCheckpoint {self.checkpoint_id} label={self.label!r}>'


class RunAuditEvent(db.Model):
    """
    Immutable append-only audit log for run timeline events.

    Rollbacks and replays are written here as new events — history is never
    mutated.  Indexed by run_id (string UUID) so events survive if their
    parent AutomationRun row is ever archived.
    """
    __tablename__ = 'run_audit_events'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.String(36), nullable=True, index=True)  # UUID string ref
    organiser_id = db.Column(db.Integer, db.ForeignKey('organisers.id'),
                             nullable=True, index=True)
    event_type = db.Column(db.String(100), nullable=False)
    actor = db.Column(db.String(100), nullable=False, default='system')
    details = db.Column(db.Text, nullable=True)  # JSON
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<RunAuditEvent type={self.event_type} run={self.run_id}>'


# ---------------------------------------------------------------------------
# Season helpers (required by lms_automation.app)
# ---------------------------------------------------------------------------

def get_default_api_season_year() -> int:
    """Return default season year for Football API.

    Uses env vars (FOOTBALL_SEASON/SEASON) if provided.
    Fallback: season year is the year the season starts (Jul+ => current year).
    """
    v = os.environ.get('FOOTBALL_SEASON') or os.environ.get('SEASON')
    if v:
        try:
            return int(v)
        except ValueError:
            pass

    now = datetime.utcnow()
    return now.year if now.month >= 7 else now.year - 1


def get_default_season_id() -> str:
    """Return default internal season_id.

    Uses SEASON_ID if provided, else derives from api season year.
    Example: 2025 -> "2025-26".
    """
    season_id = os.environ.get('SEASON_ID')
    if season_id:
        return season_id

    y = get_default_api_season_year()
    return f"{y}-{str(y + 1)[-2:]}"
