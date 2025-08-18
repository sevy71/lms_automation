# lms_automation/models.py
try:
    from .database import db  # when imported as a package
except ImportError:
    from database import db   # when loaded as a top-level module
from datetime import datetime

class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    whatsapp_number = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), default='active') # 'active', 'eliminated'
    unreachable = db.Column(db.Boolean, default=False)

    picks = db.relationship('Pick', backref='player', lazy=True)

    def __repr__(self):
        return f'<Player {self.name}>'


class Round(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='open') # 'open', 'closed', 'completed'

    fixtures = db.relationship('Fixture', backref='round', lazy=True)
    picks = db.relationship('Pick', backref='round', lazy=True)

    def __repr__(self):
        return f'<Round {self.round_number}>'

class Fixture(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=True)
    round_number = db.Column(db.Integer, nullable=True)
    event_id = db.Column(db.String(50), unique=True, nullable=False) # From API-Football or manually generated
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    time = db.Column(db.String(10), nullable=False)
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default='scheduled') # 'scheduled', 'completed'

    def __repr__(self):
        return f'<Fixture {self.home_team} vs {self.away_team}>'

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    round_id = db.Column(db.Integer, db.ForeignKey('round.id'), nullable=False)
    team_picked = db.Column(db.String(100), nullable=False)
    is_winner = db.Column(db.Boolean, nullable=True) # True if picked team won, False if lost/drew
    is_eliminated = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow) # Add timestamp

class SendQueue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)
    number = db.Column(db.Text, nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending') # pending|in_progress|sent|failed
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now())

    player = db.relationship('Player')

    def __repr__(self):
        return f'<SendQueue {self.id} to {self.number} - {self.status}>'

class WhatsAppSend(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)
    ok = db.Column(db.Boolean, nullable=False)
    error_text = db.Column(db.Text, nullable=True)
    payload = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    player = db.relationship('Player', backref='whatsapp_sends', lazy=True)

    def __repr__(self):
        return f'<WhatsAppSend {self.id} (Player: {self.player_id}) - {self.ok}>'