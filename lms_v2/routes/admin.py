"""
LMS V2 - Admin Routes
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from lms_v2.models import db, Player, Round, Fixture, Pick, PickToken, Reminder

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    """Decorator to require admin login."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page."""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == current_app.config['ADMIN_PASSWORD']:
            session['is_admin'] = True
            flash('Logged in successfully', 'success')
            return redirect(url_for('admin.dashboard'))
        flash('Invalid password', 'error')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    """Admin logout."""
    session.pop('is_admin', None)
    flash('Logged out', 'info')
    return redirect(url_for('index'))


@admin_bp.route('/')
@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Main admin dashboard."""
    # Get stats
    total_players = Player.query.count()
    active_players = Player.query.filter_by(status='active').count()
    eliminated_players = Player.query.filter_by(status='eliminated').count()

    # Get current/recent rounds
    active_round = Round.query.filter_by(status='active').first()
    recent_rounds = Round.query.order_by(Round.id.desc()).limit(5).all()

    # Get picks for active round
    picks_submitted = 0
    picks_pending = 0
    if active_round:
        picks_submitted = Pick.query.filter_by(round_id=active_round.id).count()
        picks_pending = active_players - picks_submitted

    return render_template('admin/dashboard.html',
                         total_players=total_players,
                         active_players=active_players,
                         eliminated_players=eliminated_players,
                         active_round=active_round,
                         recent_rounds=recent_rounds,
                         picks_submitted=picks_submitted,
                         picks_pending=picks_pending)


@admin_bp.route('/players')
@admin_required
def players():
    """View all players."""
    players = Player.query.order_by(Player.name).all()
    return render_template('admin/players.html', players=players)


@admin_bp.route('/players/add', methods=['POST'])
@admin_required
def add_player():
    """Add a new player."""
    name = request.form.get('name')
    telegram_id = request.form.get('telegram_id') or None

    if not name:
        flash('Name is required', 'error')
        return redirect(url_for('admin.players'))

    player = Player(name=name, telegram_id=telegram_id)
    db.session.add(player)
    db.session.commit()
    flash(f'Player {name} added', 'success')
    return redirect(url_for('admin.players'))


@admin_bp.route('/players/<int:player_id>/delete', methods=['POST'])
@admin_required
def delete_player(player_id):
    """Delete a player."""
    player = Player.query.get_or_404(player_id)
    db.session.delete(player)
    db.session.commit()
    flash(f'Player {player.name} deleted', 'success')
    return redirect(url_for('admin.players'))


@admin_bp.route('/rounds')
@admin_required
def rounds():
    """View all rounds."""
    rounds = Round.query.order_by(Round.id.desc()).all()
    return render_template('admin/rounds.html', rounds=rounds)


@admin_bp.route('/rounds/create', methods=['POST'])
@admin_required
def create_round():
    """Create a new round."""
    # Get the next round number
    last_round = Round.query.order_by(Round.round_number.desc()).first()
    next_number = (last_round.round_number + 1) if last_round else 1

    # Get cycle number
    cycle = 1
    if last_round:
        if last_round.round_number >= current_app.config['MAX_ROUNDS_PER_CYCLE']:
            cycle = last_round.cycle_number + 1
        else:
            cycle = last_round.cycle_number

    pl_matchday = request.form.get('pl_matchday', type=int)

    round_obj = Round(
        round_number=next_number,
        cycle_number=cycle,
        pl_matchday=pl_matchday,
        status='pending'
    )
    db.session.add(round_obj)
    db.session.commit()

    flash(f'Round {next_number} created', 'success')
    return redirect(url_for('admin.rounds'))


@admin_bp.route('/rounds/<int:round_id>/activate', methods=['POST'])
@admin_required
def activate_round(round_id):
    """Activate a round - starts the automation."""
    round_obj = Round.query.get_or_404(round_id)

    if round_obj.status != 'pending':
        flash('Round is not pending', 'error')
        return redirect(url_for('admin.rounds'))

    # Generate tokens for all active players
    active_players = Player.query.filter_by(status='active').all()
    for player in active_players:
        existing = PickToken.query.filter_by(player_id=player.id, round_id=round_id).first()
        if not existing:
            token = PickToken.generate(player.id, round_id)
            db.session.add(token)

    round_obj.status = 'active'
    db.session.commit()

    flash(f'Round {round_obj.round_number} activated! Tokens generated for {len(active_players)} players.', 'success')
    return redirect(url_for('admin.rounds'))


@admin_bp.route('/picks-grid')
@admin_required
def picks_grid():
    """View picks grid - all players and their picks per round."""
    players = Player.query.order_by(Player.name).all()
    rounds = Round.query.order_by(Round.round_number).all()

    # Build picks matrix
    picks_matrix = {}
    for player in players:
        picks_matrix[player.id] = {}
        for pick in player.picks:
            picks_matrix[player.id][pick.round_id] = pick

    return render_template('admin/picks_grid.html',
                         players=players,
                         rounds=rounds,
                         picks_matrix=picks_matrix)


@admin_bp.route('/reminders')
@admin_required
def reminders():
    """View reminders dashboard."""
    pending = Reminder.query.filter(Reminder.sent_at.is_(None)).order_by(Reminder.scheduled_for).all()
    sent = Reminder.query.filter(Reminder.sent_at.isnot(None)).order_by(Reminder.sent_at.desc()).limit(50).all()
    return render_template('admin/reminders.html', pending=pending, sent=sent)
