"""
LMS V2 - API Routes
"""
from datetime import datetime
from flask import Blueprint, jsonify, request, session
from lms_v2.models import db, Player, Round, Fixture, Pick, PickToken, Reminder

api_bp = Blueprint('api', __name__)


def api_admin_required(f):
    """Decorator for API routes requiring admin."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


@api_bp.route('/players')
@api_admin_required
def get_players():
    """Get all players."""
    players = Player.query.all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'telegram_id': p.telegram_id,
        'status': p.status
    } for p in players])


@api_bp.route('/rounds')
@api_admin_required
def get_rounds():
    """Get all rounds."""
    rounds = Round.query.order_by(Round.id.desc()).all()
    return jsonify([{
        'id': r.id,
        'round_number': r.round_number,
        'cycle_number': r.cycle_number,
        'pl_matchday': r.pl_matchday,
        'status': r.status,
        'first_kickoff': r.first_kickoff.isoformat() if r.first_kickoff else None
    } for r in rounds])


@api_bp.route('/picks-grid')
@api_admin_required
def get_picks_grid():
    """Get picks grid data."""
    cycle = request.args.get('cycle', type=int)

    players = Player.query.order_by(Player.name).all()

    if cycle:
        rounds = Round.query.filter_by(cycle_number=cycle).order_by(Round.round_number).all()
    else:
        rounds = Round.query.order_by(Round.round_number).all()

    # Build grid
    grid = []
    for player in players:
        row = {
            'player': {'id': player.id, 'name': player.name, 'status': player.status},
            'picks': {}
        }
        for pick in player.picks:
            if not cycle or pick.round.cycle_number == cycle:
                row['picks'][pick.round_id] = {
                    'team': pick.team,
                    'is_winner': pick.is_winner,
                    'is_eliminated': pick.is_eliminated,
                    'auto_assigned': pick.auto_assigned
                }
        grid.append(row)

    return jsonify({
        'players': grid,
        'rounds': [{'id': r.id, 'number': r.round_number, 'status': r.status} for r in rounds]
    })


@api_bp.route('/active-round')
def get_active_round():
    """Get current active round info."""
    round_obj = Round.query.filter_by(status='active').first()
    if not round_obj:
        return jsonify({'active_round': None})

    fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
    picks_count = Pick.query.filter_by(round_id=round_obj.id).count()
    active_players = Player.query.filter_by(status='active').count()

    return jsonify({
        'active_round': {
            'id': round_obj.id,
            'round_number': round_obj.round_number,
            'cycle_number': round_obj.cycle_number,
            'pl_matchday': round_obj.pl_matchday,
            'first_kickoff': round_obj.first_kickoff.isoformat() if round_obj.first_kickoff else None,
            'fixtures_count': len(fixtures),
            'picks_submitted': picks_count,
            'picks_pending': active_players - picks_count
        }
    })


@api_bp.route('/telegram/player/<telegram_id>')
def get_player_by_telegram(telegram_id):
    """Get player by Telegram ID (for bot integration)."""
    player = Player.query.filter_by(telegram_id=telegram_id).first()
    if not player:
        return jsonify({'error': 'Player not found'}), 404

    return jsonify({
        'id': player.id,
        'name': player.name,
        'status': player.status
    })


@api_bp.route('/telegram/token/<telegram_id>')
def get_pick_token_for_telegram(telegram_id):
    """Get pick token URL for a player's Telegram ID."""
    player = Player.query.filter_by(telegram_id=telegram_id).first()
    if not player:
        return jsonify({'error': 'Player not found'}), 404

    active_round = Round.query.filter_by(status='active').first()
    if not active_round:
        return jsonify({'error': 'No active round'}), 404

    token = PickToken.query.filter_by(
        player_id=player.id,
        round_id=active_round.id
    ).first()

    if not token:
        return jsonify({'error': 'No token available'}), 404

    return jsonify({
        'token': token.token,
        'round_number': active_round.round_number,
        'is_valid': token.is_valid
    })
