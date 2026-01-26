"""
LMS V2 - Player Routes
"""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from lms_v2.models import db, Player, Round, Fixture, Pick, PickToken

player_bp = Blueprint('player', __name__)


# List of all Premier League teams
PL_TEAMS = [
    'Arsenal', 'Aston Villa', 'Bournemouth', 'Brentford', 'Brighton',
    'Chelsea', 'Crystal Palace', 'Everton', 'Fulham', 'Ipswich Town',
    'Leicester City', 'Liverpool', 'Manchester City', 'Manchester United',
    'Newcastle United', 'Nottingham Forest', 'Southampton', 'Tottenham Hotspur',
    'West Ham United', 'Wolverhampton Wanderers'
]


@player_bp.route('/pick/<token>')
def pick_form(token):
    """Display pick form for a player."""
    pick_token = PickToken.query.filter_by(token=token).first()

    if not pick_token:
        return render_template('player/error.html', message='Invalid pick link.')

    if not pick_token.is_valid:
        if pick_token.is_used:
            return render_template('player/error.html', message='This link has already been used.')
        return render_template('player/error.html', message='This link has expired.')

    player = pick_token.player
    round_obj = pick_token.round

    # Check if round is still active
    if round_obj.status != 'active':
        return render_template('player/error.html', message='This round is no longer active.')

    # Get teams player has already used in this cycle
    used_teams = db.session.query(Pick.team).filter(
        Pick.player_id == player.id,
        Pick.round_id.in_(
            db.session.query(Round.id).filter(Round.cycle_number == round_obj.cycle_number)
        )
    ).all()
    used_teams = {t[0] for t in used_teams}

    # Available teams
    available_teams = [t for t in PL_TEAMS if t not in used_teams]

    # Get fixtures for this round
    fixtures = Fixture.query.filter_by(round_id=round_obj.id).order_by(Fixture.kickoff).all()

    return render_template('player/pick_form.html',
                         player=player,
                         round=round_obj,
                         token=token,
                         available_teams=available_teams,
                         used_teams=used_teams,
                         fixtures=fixtures)


@player_bp.route('/pick/<token>/submit', methods=['POST'])
def submit_pick(token):
    """Submit a pick."""
    pick_token = PickToken.query.filter_by(token=token).first()

    if not pick_token or not pick_token.is_valid:
        return render_template('player/error.html', message='Invalid or expired link.')

    player = pick_token.player
    round_obj = pick_token.round

    if round_obj.status != 'active':
        return render_template('player/error.html', message='This round is no longer active.')

    team = request.form.get('team')
    if not team:
        flash('Please select a team', 'error')
        return redirect(url_for('player.pick_form', token=token))

    # Validate team not used in this cycle
    used_teams = db.session.query(Pick.team).filter(
        Pick.player_id == player.id,
        Pick.round_id.in_(
            db.session.query(Round.id).filter(Round.cycle_number == round_obj.cycle_number)
        )
    ).all()
    used_teams = {t[0] for t in used_teams}

    if team in used_teams:
        flash('You have already used this team in this cycle', 'error')
        return redirect(url_for('player.pick_form', token=token))

    # Check if pick already exists (edit case)
    existing_pick = Pick.query.filter_by(player_id=player.id, round_id=round_obj.id).first()
    if existing_pick:
        existing_pick.team = team
        existing_pick.created_at = datetime.utcnow()
    else:
        pick = Pick(player_id=player.id, round_id=round_obj.id, team=team)
        db.session.add(pick)

    # Mark token as used
    pick_token.is_used = True
    db.session.commit()

    return render_template('player/success.html', player=player, team=team, round=round_obj)


@player_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Player self-registration."""
    if request.method == 'POST':
        name = request.form.get('name')
        telegram_id = request.form.get('telegram_id') or None

        if not name:
            flash('Name is required', 'error')
            return redirect(url_for('player.register'))

        # Check for duplicate telegram ID
        if telegram_id:
            existing = Player.query.filter_by(telegram_id=telegram_id).first()
            if existing:
                flash('This Telegram ID is already registered', 'error')
                return redirect(url_for('player.register'))

        player = Player(name=name, telegram_id=telegram_id)
        db.session.add(player)
        db.session.commit()

        flash(f'Welcome {name}! You are now registered.', 'success')
        return redirect(url_for('index'))

    return render_template('player/register.html')
