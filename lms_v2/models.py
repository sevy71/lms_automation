"""
LMS V2 - Database Models
Last Man Standing Premier League Prediction Game
"""
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
import secrets
import string

db = SQLAlchemy()


class Player(db.Model):
    """A player in the LMS game."""
    __tablename__ = 'players'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    telegram_id = db.Column(db.String(50), unique=True, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, eliminated, winner
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    picks = db.relationship('Pick', backref='player', lazy=True)
    tokens = db.relationship('PickToken', backref='player', lazy=True)

    def __repr__(self):
        return f'<Player {self.name}>'


class Round(db.Model):
    """A game round tied to a Premier League matchday."""
    __tablename__ = 'rounds'

    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    cycle_number = db.Column(db.Integer, default=1)
    pl_matchday = db.Column(db.Integer, nullable=True)  # PL matchday 1-38
    status = db.Column(db.String(20), default='pending')  # pending, active, completed
    first_kickoff = db.Column(db.DateTime, nullable=True)
    deadline = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    fixtures = db.relationship('Fixture', backref='round', lazy=True)
    picks = db.relationship('Pick', backref='round', lazy=True)
    tokens = db.relationship('PickToken', backref='round', lazy=True)

    def __repr__(self):
        return f'<Round {self.round_number} (Cycle {self.cycle_number})>'


class Fixture(db.Model):
    """A Premier League match within a round."""
    __tablename__ = 'fixtures'

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    api_id = db.Column(db.Integer, unique=True, nullable=True)  # football-data.org ID
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    kickoff = db.Column(db.DateTime, nullable=True)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='scheduled')  # scheduled, live, finished, postponed

    def __repr__(self):
        return f'<Fixture {self.home_team} vs {self.away_team}>'

    @property
    def winner(self):
        """Return winning team name, or None if draw/not finished."""
        if self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return self.home_team
        elif self.away_score > self.home_score:
            return self.away_team
        return None  # Draw


class Pick(db.Model):
    """A player's team pick for a round."""
    __tablename__ = 'picks'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    team = db.Column(db.String(100), nullable=False)
    is_winner = db.Column(db.Boolean, default=False)
    is_eliminated = db.Column(db.Boolean, default=False)
    auto_assigned = db.Column(db.Boolean, default=False)  # True if system auto-picked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('player_id', 'round_id', name='unique_player_round_pick'),
    )

    def __repr__(self):
        return f'<Pick {self.player.name}: {self.team}>'


class PickToken(db.Model):
    """Secure token for player pick submission links."""
    __tablename__ = 'pick_tokens'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('player_id', 'round_id', name='unique_player_round_token'),
    )

    @classmethod
    def generate(cls, player_id, round_id, expires_hours=72):
        """Generate a new pick token for a player/round."""
        token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        return cls(
            player_id=player_id,
            round_id=round_id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(hours=expires_hours)
        )

    @property
    def is_valid(self):
        """Check if token is still valid."""
        return not self.is_used and datetime.utcnow() < self.expires_at

    def __repr__(self):
        return f'<PickToken {self.token[:8]}...>'


class Reminder(db.Model):
    """Scheduled reminder for a player."""
    __tablename__ = 'reminders'

    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.id'), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # '4_hour', '2_hour', 'final'
    scheduled_for = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship('Player', backref='reminders')
    round = db.relationship('Round', backref='reminders')

    @property
    def is_sent(self):
        return self.sent_at is not None

    def __repr__(self):
        return f'<Reminder {self.reminder_type} for {self.player.name}>'
