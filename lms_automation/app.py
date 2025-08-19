# lms_automation/app.py
from flask import Flask, render_template, render_template_string, request, redirect, url_for, flash, Response
import os
from datetime import datetime, date, timedelta  # Import date, timedelta for deadlines
from itsdangerous import URLSafeSerializer, BadSignature
import csv
import requests
import pytz
import io
from dotenv import load_dotenv
from sqlalchemy import text
import json
import subprocess

# Load environment variables
load_dotenv()

# Import database and models
try:
    from .database import db  # when imported as a package
except ImportError:
    from database import db   # when loaded as a top-level module
try:
    from .models import Player, Round, Fixture, Pick, SendQueue, WhatsAppSend  # package import
except ImportError:
    from models import Player, Round, Fixture, Pick, SendQueue, WhatsAppSend    # top-level import

# Import our API module
try:
    from .football_data_api import get_upcoming_premier_league_fixtures, get_premier_league_fixtures_by_season, get_fixture_by_id, get_fixtures_by_ids
except ImportError:
    from football_data_api import get_upcoming_premier_league_fixtures, get_premier_league_fixtures_by_season, get_fixture_by_id, get_fixtures_by_ids
from flask_migrate import Migrate

# --- App Initialization ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(basedir, 'lms.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'please_change_me')

# Connect database to the app
db.init_app(app)
migrate = Migrate(app, db)


# --- Token generator for per-round pick links ---
pick_link_serializer = URLSafeSerializer(app.config['SECRET_KEY'], salt='pick-link')
my_picks_link_serializer = URLSafeSerializer(app.config['SECRET_KEY'], salt='my-picks-link')

def make_pick_token(player_id: int, round_id: int) -> str:
    return pick_link_serializer.dumps({'p': int(player_id), 'r': int(round_id)})

def parse_pick_token(token: str):
    try:
        data = pick_link_serializer.loads(token)
        return int(data.get('p')), int(data.get('r'))
    except BadSignature:
        return None, None

def make_my_picks_token(player_id: int) -> str:
    return my_picks_link_serializer.dumps({'p': int(player_id)})

def parse_my_picks_token(token: str):
    try:
        data = my_picks_link_serializer.loads(token)
        return int(data.get('p'))
    except BadSignature:
        return None

def compute_round_deadline(round_obj):
    """
    Return a human-readable deadline string for a round:
    1 hour before earliest kick-off in Europe/London.
    Falls back to a generic text if fixtures/times unavailable.
    """
    try:
        # Try relationship first
        fixtures = list(getattr(round_obj, 'fixtures', []) or [])
        if not fixtures:
            try:
                fixtures = Fixture.query.filter_by(round_id=round_obj.id).all()
            except Exception:
                fixtures = []

        if fixtures:
            london = pytz.timezone('Europe/London')
            times = []
            for fx in fixtures:
                dt = None
                # If your model stores datetime in `date`
                if hasattr(fx, 'date') and isinstance(fx.date, datetime):
                    dt = fx.date
                # Or if stored as date + time string
                elif hasattr(fx, 'date') and hasattr(fx, 'time') and fx.date and fx.time:
                    try:
                        if isinstance(fx.date, date):
                            hh, mm = str(fx.time).split(':')[0:2]
                            dt = datetime.combine(fx.date, datetime.min.time()).replace(hour=int(hh), minute=int(mm))
                    except Exception:
                        dt = None
                if dt:
                    if dt.tzinfo is None:
                        dt = london.localize(dt)
                    else:
                        dt = dt.astimezone(london)
                    times.append(dt)
            if times:
                deadline = min(times) - timedelta(hours=1)
                return deadline.strftime('%a %d %b %H:%M')
    except Exception as e:
        app.logger.warning("Could not compute round deadline: %s", e)
    return "1 hour before first kick-off"

# --- Queue-based WhatsApp Configuration ---
WORKER_API_TOKEN = os.environ.get('WORKER_API_TOKEN')  # Token for worker authentication

def _digits_only(s: str) -> str:
    return ''.join(ch for ch in (s or '') if ch.isdigit())

def to_e164_digits(whatsapp_number: str) -> str:
    """
    Returns a properly formatted phone number with '+' prefix for WhatsApp Web.
    Converts UK local numbers (starting with 0) to international format.
    """
    d = _digits_only(whatsapp_number)
    
    # Convert UK local numbers (07... -> +447...)
    if d.startswith('0') and len(d) == 11:
        d = '44' + d[1:]  # Remove 0 and add 44 country code
    
    # Ensure it starts with + for WhatsApp Web
    return '+' + d

def is_valid_phone_number(phone_str: str) -> bool:
    """
    Validate if a phone number string is valid for WhatsApp.
    Accepts numbers with or without + prefix.
    """
    if not phone_str:
        return False
    
    # Extract digits only
    digits = _digits_only(phone_str)
    
    # Must have digits
    if not digits:
        return False
    
    # Check length - international numbers are typically 7-15 digits
    if len(digits) < 7 or len(digits) > 15:
        return False
    
    # UK numbers should be 11 digits if starting with 0, or 12-13 if starting with 44
    if digits.startswith('0') and len(digits) != 11:
        return False
    if digits.startswith('44') and not (12 <= len(digits) <= 13):
        return False
    
    return True

def queue_whatsapp_message(to_digits: str, body_text: str, player_id: int = None):
    """
    Queue a WhatsApp message for sending via the local worker.
    Returns (ok: bool, error_msg: str|None)
    """
    try:
        # Add message to the send queue
        send_item = SendQueue(
            player_id=player_id,
            number=to_digits,
            message=body_text,
            status='pending'
        )
        db.session.add(send_item)
        db.session.commit()
        return True, None
    except Exception as e:
        app.logger.error(f"Failed to queue WhatsApp message: {e}")
        return False, str(e)

def _consecutive_whatsapp_failures(player_id: int, window: int = 10) -> int:
    """Return number of consecutive failed sends for the given player, looking back up to `window` attempts."""
    sends = SendQueue.query.filter_by(player_id=player_id, status='failed').order_by(SendQueue.updated_at.desc()).limit(window).all()
    return len(sends)

def build_pick_message(player_name: str, round_number: int, pick_link: str) -> str:
    return f"Hello {player_name}! It’s time to make your pick for LMS Round {round_number}.\n{pick_link}\n(Deadline: 1 hour before first kick-off)"

# ----------------- Helpers for results & pick outcomes -----------------

def normalize_team(name: str):
    return (name or '').strip().lower()

def fixture_decision(fix: Fixture):
    """Return 'HOME'|'AWAY'|'DRAW' if fixture completed, else None."""
    if not fix:
        return None
    # Accept both our internal status 'completed' and API short codes like 'FT', 'AET', 'PEN'
    completed = (fix.status in ('completed', 'FT', 'AET', 'PEN'))
    if not completed:
        return None
    if fix.home_score is None or fix.away_score is None:
        return None
    if fix.home_score > fix.away_score:
        return 'HOME'
    if fix.away_score > fix.home_score:
        return 'AWAY'
    return 'DRAW'

def pick_outcome_for_fixture(pick: 'Pick', fix: 'Fixture'):
    """Classic LMS: must WIN. Draw or loss = elimination. Returns 'WIN'|'LOSE'|'PENDING'."""
    result = fixture_decision(fix)
    if result is None:
        return 'PENDING'
    team = normalize_team(pick.team_picked)
    if result == 'HOME' and team == normalize_team(fix.home_team):
        return 'WIN'
    if result == 'AWAY' and team == normalize_team(fix.away_team):
        return 'WIN'
    if result == 'DRAW':
        return 'LOSE'
    return 'LOSE'


# Basic Route
@app.route('/')
def index():
    return render_template('index.html')

# Simple test route for app.py
@app.route('/test')
def test_page():
    return render_template('test.html')

# Route to fetch and display upcoming fixtures
@app.route('/admin/fetch_fixtures')
def admin_fetch_fixtures():
    upcoming_fixtures = get_upcoming_premier_league_fixtures(next_n_fixtures=20) # Fetch more for testing
    if upcoming_fixtures:
        output = "<h2>Upcoming Premier League Fixtures:</h2><ul>"
        for fixture in upcoming_fixtures:
            home_team = fixture['home_team_name'] # Use cleaned data
            away_team = fixture['away_team_name'] # Use cleaned data
            fixture_date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00')) # Convert ISO format to datetime
            output += f"<li>{home_team} vs {away_team} on {fixture_date.strftime('%Y-%m-%d %H:%M')}</li>"
        output += "</ul>"
    else:
        output = "<p>No upcoming Premier League fixtures found.</p>"
    return output


# Player Registration Route
@app.route('/register_player', methods=['GET', 'POST'])
def register_player():
    if request.method == 'POST':
        player_name = request.form['player_name']
        whatsapp_number = request.form.get('whatsapp_number') # Optional

        existing_player = Player.query.filter_by(name=player_name).first()
        if existing_player:
            flash('Player name already exists. Please choose a different name.', 'error')
        else:
            new_player = Player(name=player_name, whatsapp_number=whatsapp_number)
            db.session.add(new_player)
            db.session.commit()
            flash(f'Player {player_name} registered successfully!', 'success')
            return redirect(url_for('index')) # Redirect to home or a success page
    return render_template('register_player.html')


# ----------------- Edit Player -----------------
@app.route('/admin/edit_player/<int:player_id>', methods=['GET', 'POST'])
def edit_player(player_id):
    player = Player.query.get_or_404(player_id)
    if request.method == 'POST':
        player.name = request.form.get('name', player.name)
        player.whatsapp_number = request.form.get('whatsapp_number') or None
        db.session.commit()
        flash(f"Player {player.name} updated successfully.", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template('edit_player.html', player=player)

# Admin Route to Create a New Round
@app.route('/admin/create_round', methods=['GET', 'POST'])
def admin_create_round():
    if request.method == 'POST':
        round_number = request.form['round_number']
        start_date_str = request.form['start_date']
        end_date_str = request.form['end_date']

        try:
            round_number = int(round_number)
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid input for round number or dates.', 'error')
            return redirect(url_for('admin_create_round'))

        existing_round = Round.query.filter_by(round_number=round_number).first()
        if existing_round:
            flash(f'Round {round_number} already exists.', 'error')
        else:
            new_round = Round(
                round_number=round_number,
                start_date=start_date,
                end_date=end_date,
                status='open' # New rounds are open for picks by default
            )
            db.session.add(new_round)
            db.session.commit()
            flash(f'Round {round_number} created successfully!', 'success')
            return redirect(url_for('index')) # Redirect to home or a success page
    return render_template('create_round.html')

# Admin Dashboard (formerly player_dashboard)
@app.route('/admin_dashboard')
def admin_dashboard():
    try:
        players = Player.query.all()
        current_round = Round.query.filter_by(status='open').first()
        fixtures = []
        if current_round:
            fixtures = Fixture.query.filter_by(round_id=current_round.id).order_by(Fixture.date).all()

        # Generate unique, tokenised pick links for each active player for the current round
        player_pick_links = {}
        cleaned_whatsapp_numbers = {}
        if current_round:
            for player in players:
                if player.status == 'active':
                    token = make_pick_token(player.id, current_round.id)
                    player_pick_links[player.id] = url_for('pick_with_token', token=token, _external=True)
                    if player.whatsapp_number:
                        # Clean the number by removing all non-digit characters
                        cleaned_number = ''.join(filter(str.isdigit, player.whatsapp_number))
                        cleaned_whatsapp_numbers[player.id] = cleaned_number
        has_wa_config = True  # Always true for queue-based system

        # Get queue statistics - with error handling
        try:
            queue_stats = {
                'pending': SendQueue.query.filter_by(status='pending').count(),
                'in_progress': SendQueue.query.filter_by(status='in_progress').count(),
                'sent': SendQueue.query.filter_by(status='sent').count(),
                'failed': SendQueue.query.filter_by(status='failed').count(),
            }
            # Get recent queue items for monitoring
            recent_queue_items = SendQueue.query.order_by(SendQueue.updated_at.desc()).limit(10).all()
        except Exception as e:
            app.logger.warning(f"Could not load queue stats: {e}")
            queue_stats = {'pending': 0, 'in_progress': 0, 'sent': 0, 'failed': 0}
            recent_queue_items = []

        return render_template('admin_dashboard.html', 
                             players=players, 
                             current_round=current_round, 
                             fixtures=fixtures, 
                             player_pick_links=player_pick_links, 
                             cleaned_whatsapp_numbers=cleaned_whatsapp_numbers, 
                             has_wa_config=has_wa_config,
                             queue_stats=queue_stats,
                             recent_queue_items=recent_queue_items,
                             season_year=2025)
    except Exception as e:
        app.logger.error(f"Error in admin_dashboard: {e}")
        # If database isn't initialized, try to initialize it
        try:
            db.create_all()
            flash('Database initialized. Please refresh the page.', 'info')
        except Exception as init_error:
            app.logger.error(f"Could not initialize database: {init_error}")
            flash('Database error. Please contact administrator.', 'error')
        
        # Return a basic error page or redirect
        return render_template_string('''
        <!DOCTYPE html>
        <html><head><title>Admin Dashboard Error</title></head>
        <body>
        <h1>Admin Dashboard Error</h1>
        <p>There was an error loading the dashboard. The database may not be initialized.</p>
        <a href="{{ url_for("admin_dashboard") }}">Try Again</a>
        </body></html>
        '''), 500


# Tokenised pick submission route: /l/<token>
@app.route('/l/<token>', methods=['GET', 'POST'])
def pick_with_token(token):
    player_id, round_id = parse_pick_token(token)
    if not player_id or not round_id:
        flash('Invalid or expired link.', 'error')
        return redirect(url_for('index'))

    player = Player.query.get_or_404(player_id)
    this_round = Round.query.get_or_404(round_id)
    if this_round.status != 'open':
        flash(f'Round {this_round.round_number} is not open for picks.', 'error')
        return redirect(url_for('index'))

    fixtures = Fixture.query.filter_by(round_id=this_round.id).order_by(Fixture.date).all()

    if request.method == 'POST':
        team_picked = request.form.get('team_picked')
        if not team_picked:
            flash('Please select a team.', 'error')
            return redirect(url_for('pick_with_token', token=token))

        if player.status == 'eliminated':
            flash(f'{player.name}, you have been eliminated and cannot make a pick.', 'error')
            return redirect(url_for('pick_with_token', token=token))

        existing_pick = Pick.query.filter_by(player_id=player.id, round_id=this_round.id).first()
        if existing_pick:
            flash(f'{player.name}, you have already made a pick for Round {this_round.round_number}. Your current pick is {existing_pick.team_picked}.', 'error')
        else:
            # Enforce global no-repeat rule: player cannot pick any team they have picked in any previous round
            prior_picks = Pick.query.filter(Pick.player_id == player.id, Pick.round_id != this_round.id).all()
            prior_teams = [p.team_picked for p in prior_picks]
            if team_picked in prior_teams:
                flash(f'{player.name}, you cannot pick {team_picked} because you have picked it before.', 'error')
                return redirect(url_for('pick_with_token', token=token))
            db.session.add(Pick(player_id=player.id, round_id=this_round.id, team_picked=team_picked, timestamp=datetime.utcnow()))
            db.session.commit()
            flash(f'{player.name}, your pick of {team_picked} for Round {this_round.round_number} has been submitted!', 'success')
        return redirect(url_for('pick_with_token', token=token))

    # Get all teams the player has picked in previous rounds (strict: no repeats ever)
    # This query gets all picks for the player that are NOT in the current round
    previous_picks = Pick.query.filter(
        Pick.player_id == player.id,
        Pick.round_id != this_round.id,
    ).all()
    # Normalise team names for comparison in the UI and validation
    previously_picked_teams = [p.team_picked for p in previous_picks]

    return render_template('submit_pick.html',
                           player=player,
                           current_round=this_round,
                           fixtures=fixtures,
                           previously_picked_teams=previously_picked_teams)

# Player-specific Pick Submission
@app.route('/submit_pick/<int:player_id>', methods=['GET', 'POST'])
def submit_pick(player_id):
    player = Player.query.get_or_404(player_id)
    current_round = Round.query.filter_by(status='open').first()
    fixtures = []
    if current_round:
        fixtures = Fixture.query.filter_by(round_id=current_round.id).order_by(Fixture.date).all()

    if request.method == 'POST':
        team_picked = request.form.get('team_picked')

        if not team_picked:
            flash('Please select a team.', 'error')
            return redirect(url_for('submit_pick', player_id=player.id)) # Redirect to GET route

        if player.status == 'eliminated':
            flash(f'{player.name}, you have been eliminated and cannot make a pick.', 'error')
            return redirect(url_for('submit_pick', player_id=player.id)) # Redirect to GET route

        if not current_round:
            flash('No open round available for picks.', 'error')
            return redirect(url_for('submit_pick', player_id=player.id)) # Redirect to GET route

        # Check if player has already made a pick for this round
        existing_pick = Pick.query.filter_by(player_id=player.id, round_id=current_round.id).first()
        if existing_pick:
            flash(f'{player.name}, you have already made a pick for Round {current_round.round_number}. Your current pick is {existing_pick.team_picked}.', 'error')
        else:
            new_pick = Pick(
                player_id=player.id,
                round_id=current_round.id,
                team_picked=team_picked,
                timestamp=datetime.utcnow() # Record pick time
            )
            db.session.add(new_pick)
            db.session.commit()
            flash(f'{player.name}, your pick of {team_picked} for Round {current_round.round_number} has been submitted!', 'success')
        return redirect(url_for('submit_pick', player_id=player.id)) # Redirect to GET route

    return render_template('submit_pick.html', player=player, current_round=current_round, fixtures=fixtures)


@app.route('/admin/bulk_register_players', methods=['GET', 'POST'])
def admin_bulk_register_players():
    if request.method == 'POST':
        player_names_raw = request.form['player_names']
        player_names = [name.strip() for name in player_names_raw.split('\n') if name.strip()]
        
        registered_count = 0
        skipped_count = 0
        for player_name in player_names:
            existing_player = Player.query.filter_by(name=player_name).first()
            if existing_player:
                skipped_count += 1
            else:
                new_player = Player(name=player_name, whatsapp_number=None) # WhatsApp number can be added later
                db.session.add(new_player)
                registered_count += 1
        db.session.commit()
        flash(f'Successfully registered {registered_count} new players. Skipped {skipped_count} existing players.', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('bulk_register_players.html')


# Admin Route to Manually Add a Fixture
@app.route('/admin/manual_add_fixture', methods=['GET', 'POST'])
def admin_manual_add_fixture():
    rounds = Round.query.order_by(Round.round_number).all() # Get all rounds for dropdown
    if request.method == 'POST':
        round_id = request.form['round_id']
        home_team = request.form['home_team']
        away_team = request.form['away_team']
        fixture_date_str = request.form['fixture_date']
        fixture_time_str = request.form['fixture_time']

        try:
            round_obj = Round.query.get(round_id)
            if not round_obj:
                flash('Invalid Round selected.', 'error')
                return redirect(url_for('admin_manual_add_fixture'))

            fixture_date = datetime.strptime(fixture_date_str, '%Y-%m-%d').date()
            # Combine date and time for the datetime object
            full_datetime_str = f"{fixture_date_str} {fixture_time_str}"
            full_datetime = datetime.strptime(full_datetime_str, '%Y-%m-%d %H:%M')

            # Generate a unique event_id for manual fixtures (e.g., 'MANUAL_ROUND_FIXTURE_TIMESTAMP')
            event_id = f"MANUAL_{round_obj.round_number}_{home_team.replace(' ', '')}_{away_team.replace(' ', '')}_{full_datetime.strftime('%Y%m%d%H%M')}"

            existing_fixture = Fixture.query.filter_by(event_id=event_id).first()
            if existing_fixture:
                flash('Fixture with these details already exists (manual entry).', 'error')
            else:
                new_fixture = Fixture(
                    round_id=round_obj.id,
                    event_id=event_id,
                    home_team=home_team,
                    away_team=away_team,
                    date=full_datetime, # Store as datetime
                    time=fixture_time_str, # Store time string
                    status='scheduled'
                )
                db.session.add(new_fixture)
                db.session.commit()
                flash(f'Fixture {home_team} vs {away_team} added to Round {round_obj.round_number}!', 'success')
                return redirect(url_for('admin_manual_add_fixture'))
        except ValueError:
            flash('Invalid date or time format.', 'error')
        except Exception as e:
            flash(f'An error occurred: {e}', 'error')
        return redirect(url_for('admin_manual_add_fixture'))
    return render_template('manual_add_fixture.html', rounds=rounds)


# ----------------- Bulk Assign Fixtures to Round -----------------
@app.route('/admin/bulk_assign_fixtures/<int:round_id>', methods=['GET', 'POST'])
def bulk_assign_fixtures(round_id):
    round_obj = Round.query.get_or_404(round_id)
    # Show only fixtures not yet assigned to any round
    fixtures = Fixture.query.filter(Fixture.round_id.is_(None)).order_by(Fixture.date.asc()).all()
    if request.method == 'POST':
        selected_ids = request.form.getlist('fixture_ids')
        count = 0
        for fid in selected_ids:
            fx = Fixture.query.get(int(fid))
            if fx and fx.round_id is None:
                fx.round_id = round_id
                count += 1
        db.session.commit()
        flash(f"Assigned {count} fixtures to Round {round_obj.round_number}.", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template('bulk_assign_fixtures.html', round=round_obj, fixtures=fixtures)


#
# ----------------- Admin: Update results (manual form) -----------------
@app.route('/admin/update_results/<int:round_id>', methods=['GET', 'POST'])
def admin_update_results(round_id):
    rnd = Round.query.get_or_404(round_id)
    fixtures = Fixture.query.filter_by(round_id=rnd.id).order_by(Fixture.date.asc()).all()

    if request.method == 'POST':
        ids = request.form.getlist('fixture_id')
        home_scores = request.form.getlist('home_score')
        away_scores = request.form.getlist('away_score')
        statuses = request.form.getlist('status')
        for i, fid in enumerate(ids):
            f = Fixture.query.get(int(fid))
            hs = home_scores[i].strip()
            as_ = away_scores[i].strip()
            st = statuses[i].strip() or 'scheduled'
            f.home_score = int(hs) if hs != '' else None
            f.away_score = int(as_) if as_ != '' else None
            f.status = st
        db.session.commit()
        flash('Results saved.', 'success')
        return redirect(url_for('admin_update_results', round_id=round_id))

    return render_template('admin_update_results.html', round=rnd, fixtures=fixtures)


# ----------------- Admin: Auto update results from API -----------------
@app.route('/admin/auto_update_results/<int:round_id>', methods=['POST'])
def admin_auto_update_results(round_id):
    rnd = Round.query.get_or_404(round_id)
    fixtures = Fixture.query.filter_by(round_id=rnd.id).all()
    event_ids = [f.event_id for f in fixtures if f.event_id]
    if not event_ids:
        flash('No event IDs available to auto-update.', 'warning')
        return redirect(url_for('admin_update_results', round_id=round_id))

    results_by_id = get_fixtures_by_ids(event_ids)
    updated = 0
    for f in fixtures:
        # Only look up from API results if the event_id is numeric (API-Football IDs).
        if str(f.event_id).isdigit():
            data = results_by_id.get(str(f.event_id)) or (results_by_id.get(int(f.event_id)) if results_by_id else None)
        else:
            data = None  # Skip manual fixtures that have non-numeric event_id (e.g., 'MANUAL_...')
        if not data:
            continue
        goals = data.get('goals') or {}
        status_short = ((data.get('fixture') or {}).get('status') or {}).get('short')
        hs = goals.get('home')
        as_ = goals.get('away')
        if hs is not None:
            f.home_score = hs
        if as_ is not None:
            f.away_score = as_
        if status_short:
            f.status = status_short
        updated += 1

    db.session.commit()
    flash(f'Auto-updated {updated} fixtures from API.', 'success')
    return redirect(url_for('admin_update_results', round_id=round_id))


# ----------------- Admin: Process round (determine eliminations) -----------------
@app.route('/admin/process_round/<int:round_id>', methods=['GET'])
def admin_process_round(round_id):
    rnd = Round.query.get_or_404(round_id)
    fixtures_by_team = {normalize_team(f.home_team): f for f in rnd.fixtures}
    fixtures_by_team.update({normalize_team(f.away_team): f for f in rnd.fixtures})

    undecided = []
    for f in rnd.fixtures:
        if fixture_decision(f) is None and f.status not in ('PST', 'P', 'postponed', 'cancelled'):
            undecided.append(f)
    if undecided:
        flash(f'There are {len(undecided)} undecided fixtures. Enter scores or auto-update before processing.', 'warning')
        return redirect(url_for('admin_update_results', round_id=round_id))

    eliminated = 0
    survived = 0
    for pick in rnd.picks:
        # if already judged, skip
        if pick.is_winner is not None:
            continue
        fix = fixtures_by_team.get(normalize_team(pick.team_picked))
        if not fix:
            # no matching fixture -> leave pending
            continue
        outcome = pick_outcome_for_fixture(pick, fix)
        if outcome == 'WIN':
            pick.is_winner = True
            survived += 1
        elif outcome == 'LOSE':
            pick.is_winner = False
            pick.is_eliminated = True
            if pick.player.status != 'eliminated':
                pick.player.status = 'eliminated'
            eliminated += 1
        else:
            # PENDING: leave as None
            pass

    rnd.status = 'completed'
    db.session.commit()

    flash(f'Processed Round {rnd.round_number}: {survived} win, {eliminated} eliminated.', 'success')
    return redirect(url_for('admin_round_summary', round_id=round_id))


# ----------------- Admin: Round summary & public standings -----------------
@app.route('/admin/round_summary/<int:round_id>')
def admin_round_summary(round_id):
    rnd = Round.query.get_or_404(round_id)
    picks = Pick.query.filter_by(round_id=round_id).all()

    def outcome_label(p):
        if p.is_winner is True:
            return 'WIN'
        if p.is_winner is False:
            return 'LOSE'
        return 'PENDING'

    rows = [{
        'player': p.player.name,
        'team': p.team_picked,
        'outcome': outcome_label(p),
        'active': (p.player.status == 'active')
    } for p in picks]

    return render_template('admin_round_summary.html', round=rnd, rows=rows)


@app.route('/standings')
def standings():
    active = Player.query.filter_by(status='active').order_by(Player.name.asc()).all()
    eliminated = Player.query.filter_by(status='eliminated').order_by(Player.name.asc()).all()
    return render_template('standings.html', active=active, eliminated=eliminated)

@app.route('/admin/generate_round_summary_for_whatsapp/<int:round_id>')
def generate_round_summary_for_whatsapp(round_id):
    rnd = Round.query.get_or_404(round_id)
    picks = Pick.query.filter_by(round_id=round_id).all()

    summary_lines = [f"*LMS Round {rnd.round_number} Picks:*"]
    for pick in picks:
        summary_lines.append(f"{pick.player.name}: {pick.team_picked}")
    
    whatsapp_message = "\n".join(summary_lines)

    return render_template('generate_round_summary_for_whatsapp.html', round=rnd, whatsapp_message=whatsapp_message)

@app.route('/admin/download_fixtures')
def download_fixtures():
    si = io.StringIO()
    cw = csv.writer(si)

    headers = ["Round Number", "Home Team", "Away Team", "Date", "Time", "Status", "Home Score", "Away Score"]
    cw.writerow(headers)

    # Get all fixtures, including unassigned ones
    fixtures = Fixture.query.outerjoin(Round).order_by(
        Round.round_number.asc().nullsfirst(), 
        Fixture.date.asc()
    ).all()

    for fixture in fixtures:
        row = [
            fixture.round.round_number if fixture.round else "N/A",
            fixture.home_team,
            fixture.away_team,
            fixture.date.strftime('%Y-%m-%d') if fixture.date else "N/A",
            fixture.time,
            fixture.status,
            fixture.home_score if fixture.home_score is not None else "",
            fixture.away_score if fixture.away_score is not None else ""
        ]
        cw.writerow(row)

    output = si.getvalue()
    response = Response(output, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=lms_fixtures.csv"
    return response


@app.route('/delete_player/<int:player_id>', methods=['GET'])
def delete_player(player_id):
    player = Player.query.get_or_404(player_id)
    try:
        db.session.delete(player)
        db.session.commit()
        flash(f'Player {player.name} has been deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while deleting the player: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/eliminate_player/<int:player_id>', methods=['GET'])
def eliminate_player(player_id):
    player = Player.query.get_or_404(player_id)
    try:
        player.status = 'eliminated'
        db.session.commit()
        flash(f'Player {player.name} has been eliminated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while eliminating the player: {e}', 'error')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/unassigned_fixtures')
def admin_unassigned_fixtures():
    fixtures = Fixture.query.filter(Fixture.round_id.is_(None)).order_by(Fixture.date.asc()).all()
    return render_template('unassigned_fixtures.html', fixtures=fixtures, show_round_id=True)


@app.route('/admin/reset_game', methods=['POST'])
def admin_reset_game():
    try:
        # Delete all records from the tables except for players
        db.session.query(Pick).delete()
        db.session.query(Fixture).delete()
        db.session.query(Round).delete()
        # Reset all players to active status
        Player.query.update({Player.status: 'active'})
        db.session.commit()
        flash('Game has been reset successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while resetting the game: {e}', 'error')
    return redirect(url_for('admin_dashboard'))

# ----------------- Admin: Queue WhatsApp links for sending -----------------
@app.route('/admin/send_whatsapp_links', methods=['POST'])
def admin_send_whatsapp_links():
    try:
        app.logger.info("📱 WhatsApp links route called - starting processing")
        
        current_round = Round.query.filter_by(status='open').first()
        if not current_round:
            app.logger.warning("No open round available for WhatsApp sending")
            flash('No open round available.', 'error')
            return redirect(url_for('admin_dashboard'))

        players = Player.query.filter_by(status='active').all()
        app.logger.info(f"Found {len(players)} active players")
        
        if not players:
            app.logger.warning("No active players to message")
            flash('No active players to message.', 'warning')
            return redirect(url_for('admin_dashboard'))
            
    except Exception as e:
        app.logger.error(f"Early error in WhatsApp route: {e}")
        flash(f'Error processing WhatsApp request: {e}', 'error')
        return redirect(url_for('admin_dashboard'))

    queued = 0
    failed = 0
    details = []
    
    for p in players:
        # Skip players marked unreachable
        if getattr(p, 'unreachable', False):
            details.append(f"{p.name}: unreachable")
            failed += 1
            continue
        if not p.whatsapp_number:
            details.append(f"{p.name}: no number")
            failed += 1
            continue
        
        if not is_valid_phone_number(p.whatsapp_number):
            details.append(f"{p.name}: invalid number")
            failed += 1
            continue
        
        to_digits = to_e164_digits(p.whatsapp_number)

        token = make_pick_token(p.id, current_round.id)
        pick_link = url_for('pick_with_token', token=token, _external=True)
        body = build_pick_message(p.name, current_round.round_number, pick_link)
        
        # Queue the message
        ok, err = queue_whatsapp_message(to_digits, body, p.id)
        
        if ok:
            queued += 1
            details.append(f"{p.name}: queued")
        else:
            failed += 1
            details.append(f"{p.name}: failed ({err})")
            app.logger.error(f"Failed to queue WhatsApp to {p.name}: {err}")

    flash(f"WhatsApp messages queued: {queued} queued, {failed} failed.", 'success' if queued and not failed else 'warning')
    # Also surface a few detail lines for quick debug
    preview = "; ".join(details[:5])
    if preview:
        flash(f"Examples: {preview}", 'info')
    
    # The local worker should be started manually to process the queue.
    if queued > 0:
        flash('Messages have been queued. Please start your local worker to send them.', 'info')

    return redirect(url_for('admin_dashboard'))

# ----------------- Admin: Queue WhatsApp link to a single player -----------------
@app.route('/admin/send_whatsapp_link/<int:player_id>', methods=['POST'])
def admin_send_whatsapp_link(player_id):
    player = Player.query.get_or_404(player_id)
    if not player.whatsapp_number:
        flash(f"{player.name} has no WhatsApp number.", "error")
        return redirect(url_for('admin_dashboard'))

    current_round = Round.query.filter_by(status='open').first()
    if not current_round:
        flash('No open round available.', 'error')
        return redirect(url_for('admin_dashboard'))

    # Skip if player marked unreachable
    if getattr(player, 'unreachable', False):
        flash(f"{player.name} is marked unreachable; not sending.", "warning")
        return redirect(url_for('admin_dashboard'))

    to_digits = to_e164_digits(player.whatsapp_number)
    token = make_pick_token(player.id, current_round.id)
    pick_link = url_for('pick_with_token', token=token, _external=True)
    body = build_pick_message(player.name, current_round.round_number, pick_link)
    
    # Queue the message
    ok, err = queue_whatsapp_message(to_digits, body, player.id)

    if ok:
        flash(f"Queued pick link for {player.name}.", "success")
    else:
        flash(f"Failed to queue message for {player.name}: {err}", "error")
    return redirect(url_for('admin_dashboard'))


@app.route('/player_picks/<int:player_id>')
def player_picks(player_id):
    player = Player.query.get_or_404(player_id)
    picks = Pick.query.filter_by(player_id=player.id).join(Round).order_by(Round.round_number).all()
    return render_template('player_picks.html', player=player, picks=picks)


@app.route('/my_picks/<token>')
def my_picks(token):
    player_id = parse_my_picks_token(token)
    if not player_id:
        flash('Invalid or expired link.', 'error')
        return redirect(url_for('index'))

    player = Player.query.get_or_404(player_id)
    picks = Pick.query.filter_by(player_id=player.id).join(Round).order_by(Round.round_number).all()
    return render_template('my_picks.html', player=player, picks=picks)


# ----------------- Admin: Manage Round -----------------
@app.route('/admin/round/<int:round_id>/manage', methods=['GET'])
def admin_manage_round(round_id):
    round_obj = Round.query.get_or_404(round_id)
    fixtures = Fixture.query.filter_by(round_id=round_obj.id).order_by(Fixture.date.asc()).all()
    return render_template('admin_manage_round.html', round=round_obj, fixtures=fixtures)


# ----------------- Admin: Get Round Links -----------------
@app.route('/admin/round/<int:round_id>/links', methods=['GET'])
def admin_round_links(round_id):
    round_obj = Round.query.get_or_404(round_id)
    fixtures = Fixture.query.filter_by(round_id=round_obj.id).order_by(Fixture.date.asc()).all()
    players = Player.query.filter_by(status='active').all()
    
    player_pick_links = {}
    my_picks_links = {}
    for player in players:
        token = make_pick_token(player.id, round_obj.id)
        player_pick_links[player.id] = url_for('pick_with_token', token=token, _external=True)
        my_picks_token = make_my_picks_token(player.id)
        my_picks_links[player.id] = url_for('my_picks', token=my_picks_token, _external=True)

    return render_template('round_links.html', round=round_obj, fixtures=fixtures, players=players, player_pick_links=player_pick_links, my_picks_links=my_picks_links)


# ----------------- API Endpoints for Local Worker -----------------
def validate_worker_token():
    """Validate the worker API token from Authorization header"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return False
    token = auth_header.replace('Bearer ', '')
    return token == WORKER_API_TOKEN

@app.route('/api/queue/next', methods=['GET'])
def api_queue_next():
    """API endpoint to get next pending messages for the worker"""
    if not validate_worker_token():
        return {'error': 'Unauthorized'}, 401
    
    limit = request.args.get('limit', 10, type=int)
    
    # Get pending messages, mark them as in_progress to avoid double processing
    pending = SendQueue.query.filter_by(status='pending').limit(limit).all()
    
    jobs = []
    for item in pending:
        item.status = 'in_progress'
        item.attempts += 1
        jobs.append({
            'id': item.id,
            'number': item.number,
            'message': item.message,
            'player_id': item.player_id
        })
    
    db.session.commit()
    return jobs

@app.route('/api/queue/mark', methods=['POST'])
def api_queue_mark():
    """API endpoint to mark a job as completed or failed"""
    if not validate_worker_token():
        return {'error': 'Unauthorized'}, 401
    
    data = request.get_json()
    job_id = data.get('id')
    status = data.get('status')  # 'sent' or 'failed'
    error = data.get('error')
    
    item = SendQueue.query.get(job_id)
    if not item:
        return {'error': 'Job not found'}, 404
    
    item.status = status
    if error:
        item.last_error = str(error)
    
    # Check for consecutive failures and mark player unreachable
    if status == 'failed' and item.player_id:
        fails = _consecutive_whatsapp_failures(item.player_id, window=10)
        if fails >= 5:
            player = Player.query.get(item.player_id)
            if player:
                player.unreachable = True
    
    db.session.commit()
    return {'success': True}


# ----------------- Database Initialization Route -----------------
@app.route('/admin/init_db', methods=['GET', 'POST'])
def admin_init_db():
    """Initialize or reset the database"""
    if request.method == 'POST':
        try:
            # Import and run init_db function
            from init_db import init_db
            init_db()
            flash('Database initialized successfully!', 'success')
        except Exception as e:
            flash(f'Database initialization failed: {e}', 'error')
        return redirect(url_for('admin_dashboard'))
    
    # Show confirmation form
    return render_template_string('''
    {% extends "base.html" %}
    {% block title %}Initialize Database{% endblock %}
    {% block content %}
    <div class="container">
        <h2>Initialize Database</h2>
        <div class="alert alert-warning">
            <strong>Warning:</strong> This will create/update database tables. 
            Any existing data may be preserved during migration.
        </div>
        
        <form method="post">
            <button type="submit" class="btn btn-primary">Initialize Database</button>
            <a href="{{ url_for('admin_dashboard') }}" class="btn btn-secondary">Cancel</a>
        </form>
    </div>
    {% endblock %}
    ''')

# ----------------- Missing Admin Routes -----------------

@app.route('/admin/new_game', methods=['POST'])
def admin_new_game():
    """Start a new game - reset all data"""
    try:
        # Delete all records from the tables except for players
        db.session.query(Pick).delete()
        db.session.query(Fixture).delete()
        db.session.query(Round).delete()
        db.session.query(SendQueue).delete()
        # Reset all players to active status
        Player.query.update({Player.status: 'active', Player.unreachable: False})
        db.session.commit()
        flash('New game started successfully! All data reset except players.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while starting new game: {e}', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/load_fixtures/<int:season_year>')
def admin_load_fixtures(season_year):
    """Load fixtures for a given season"""
    fixtures_data = get_premier_league_fixtures_by_season(season_year)
    if not fixtures_data:
        flash(f"No fixtures found for season {season_year} from API.", 'warning')
        return redirect(url_for('admin_dashboard'))

    fixtures_added_count = 0
    for fixture_api in fixtures_data:
        event_id = str(fixture_api['fixture']['id'])
        home_team = fixture_api['teams']['home']['name']
        away_team = fixture_api['teams']['away']['name']
        fixture_date_str = fixture_api['fixture']['date']
        fixture_date = datetime.fromisoformat(fixture_date_str.replace('Z', '+00:00'))
        
        # Extract Premier League matchday number
        round_name = fixture_api['league']['round']
        try:
            pl_matchday = int(round_name.split(' - ')[1])
        except (IndexError, ValueError):
            pl_matchday = 1

        # Check if fixture already exists
        existing_fixture = Fixture.query.filter_by(event_id=event_id).first()
        if not existing_fixture:
            new_fixture = Fixture(
                round_id=None,  # Don't auto-assign to game rounds - let admin create rounds manually
                round_number=pl_matchday,  # Store the Premier League matchday number
                event_id=event_id,
                home_team=home_team,
                away_team=away_team,
                date=fixture_date,
                time=fixture_date.strftime('%H:%M'),
                home_score=fixture_api['goals']['home'],
                away_score=fixture_api['goals']['away'],
                status=fixture_api['fixture']['status']['short']
            )
            db.session.add(new_fixture)
            fixtures_added_count += 1
        else:
            # Update existing fixture
            existing_fixture.round_number = pl_matchday  # Update the PL matchday
            existing_fixture.home_score = fixture_api['goals']['home']
            existing_fixture.away_score = fixture_api['goals']['away']
            existing_fixture.status = fixture_api['fixture']['status']['short']

    db.session.commit()
    flash(f"Loaded {fixtures_added_count} new fixtures for season {season_year}. Existing fixtures updated.", 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/next_round')
def admin_next_round():
    """Setup next round page - intelligently finds next unplayed Premier League matchday"""
    from datetime import date
    from sqlalchemy import text
    
    # Get the highest game round number (LMS rounds reset to 1 after wins, but continue progressing through PL season)
    last_round = Round.query.order_by(Round.round_number.desc()).first()
    next_game_round = (last_round.round_number + 1) if last_round else 1
    
    # SMART LOGIC: Find the next unplayed Premier League matchday
    # Get all fixtures and group them by their Premier League matchday (stored in round_number)
    all_fixtures = Fixture.query.order_by(Fixture.date.asc()).all()
    
    # Group fixtures by Premier League matchday
    matchday_fixtures = {}
    for fixture in all_fixtures:
        # Use the stored round_number which contains the Premier League matchday
        pl_matchday = fixture.round_number if fixture.round_number else 1
        
        if pl_matchday not in matchday_fixtures:
            matchday_fixtures[pl_matchday] = {
                'all': [],
                'assigned': [],
                'unassigned': []
            }
        
        matchday_fixtures[pl_matchday]['all'].append(fixture)
        if fixture.round_id is None:
            matchday_fixtures[pl_matchday]['unassigned'].append(fixture)
        else:
            matchday_fixtures[pl_matchday]['assigned'].append(fixture)
    
    # Find the earliest matchday that has unassigned fixtures
    available_matchdays = sorted(matchday_fixtures.keys())
    next_matchday = None
    next_round_fixtures = []
    
    for matchday in available_matchdays:
        if matchday_fixtures[matchday]['unassigned']:
            next_matchday = matchday
            next_round_fixtures = matchday_fixtures[matchday]['unassigned']
            break
    
    # If no unassigned fixtures found in weekly groups, fall back to any unassigned fixtures
    if not next_round_fixtures:
        unassigned_fixtures = Fixture.query.filter(Fixture.round_id.is_(None)).order_by(Fixture.date.asc()).all()
        if unassigned_fixtures:
            next_round_fixtures = unassigned_fixtures[:10]  # Take next 10 fixtures
            next_matchday = 1
    
    # Calculate start and end dates from selected fixtures
    start_date = None
    end_date = None
    
    if next_round_fixtures:
        fixture_dates = []
        for f in next_round_fixtures:
            if f.date:
                if hasattr(f.date, 'date'):
                    fixture_dates.append(f.date.date())
                else:
                    fixture_dates.append(f.date)
        
        if fixture_dates:
            start_date = min(fixture_dates)
            end_date = max(fixture_dates)
    
    if not start_date:
        start_date = date.today()
        end_date = date.today()
    
    # Get all unassigned fixtures for fallback display
    all_unassigned = Fixture.query.filter(Fixture.round_id.is_(None)).order_by(Fixture.date.asc()).all()
    
    return render_template('next_round.html', 
                         game_round_number=next_game_round,
                         league_round_number=next_matchday or 1,
                         fixtures=next_round_fixtures,
                         all_unassigned=all_unassigned,
                         start_date=start_date,
                         end_date=end_date)

@app.route('/admin/create_next_round', methods=['POST'])
def admin_create_next_round():
    """Create the next round"""
    try:
        game_round_number = int(request.form['game_round_number'])
        league_round_number = int(request.form['league_round_number'])
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        selected_fixtures = request.form.getlist('selected_fixtures')
        
        if not selected_fixtures:
            flash('Please select at least one fixture for the round.', 'error')
            return redirect(url_for('admin_next_round'))
        
        # Parse dates
        from datetime import datetime
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        # Validate dates
        if start_date > end_date:
            flash('Start date cannot be after end date.', 'error')
            return redirect(url_for('admin_next_round'))
        
        # Create new round
        new_round = Round(
            round_number=game_round_number,
            start_date=start_date,
            end_date=end_date,
            status='open'
        )
        db.session.add(new_round)
        db.session.commit()
        
        # Assign selected fixtures to the round
        count = 0
        for fixture_id in selected_fixtures:
            fixture = Fixture.query.get(int(fixture_id))
            if fixture and fixture.round_id is None:
                fixture.round_id = new_round.id
                count += 1
        
        db.session.commit()
        flash(f'Game Round {game_round_number} created successfully with {count} fixtures from League Round {league_round_number}!', 'success')
        
    except ValueError as e:
        flash('Invalid date format. Please use the date picker.', 'error')
        return redirect(url_for('admin_next_round'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating round: {e}', 'error')
        return redirect(url_for('admin_next_round'))
    
    return redirect(url_for('admin_dashboard'))

# Fix the admin_send_whatsapp route name
@app.route('/admin/send_whatsapp', methods=['POST'])
def admin_send_whatsapp():
    """Alias for admin_send_whatsapp_links to match template expectations"""
    app.logger.info("📱 WhatsApp route (alias) called")
    return admin_send_whatsapp_links()

# ----------------- Admin: Auto-update and process round (simplified) -----------------
@app.route('/admin/auto_process_round/<int:round_id>', methods=['POST'])
def admin_auto_update_and_process_round(round_id):
    """Automated one-click round processing."""
    app.logger.info(f"--- Starting auto-processing for round {round_id} ---")
    
    # 1. Auto-update results from API
    rnd = Round.query.get_or_404(round_id)
    fixtures = Fixture.query.filter_by(round_id=rnd.id).all()
    event_ids = [f.event_id for f in fixtures if f.event_id and str(f.event_id).isdigit()]
    
    if event_ids:
        results_by_id = get_fixtures_by_ids(event_ids)
        updated_count = 0
        for f in fixtures:
            if str(f.event_id).isdigit():
                data = results_by_id.get(str(f.event_id))
                if data:
                    goals = data.get('goals') or {}
                    status_short = ((data.get('fixture') or {}).get('status') or {}).get('short')
                    hs = goals.get('home')
                    as_ = goals.get('away')
                    if hs is not None: f.home_score = hs
                    if as_ is not None: f.away_score = as_
                    if status_short: f.status = status_short
                    updated_count += 1
        db.session.commit()
        app.logger.info(f"Auto-updated {updated_count} fixtures from API.")

    # 2. Process eliminations (same logic as admin_process_round)
    fixtures_by_team = {normalize_team(f.home_team): f for f in rnd.fixtures}
    fixtures_by_team.update({normalize_team(f.away_team): f for f in rnd.fixtures})

    undecided = []
    for f in rnd.fixtures:
        if fixture_decision(f) is None and f.status not in ('PST', 'P', 'postponed', 'cancelled'):
            undecided.append(f)
    
    if undecided:
        flash(f'There are {len(undecided)} undecided fixtures after API update. Cannot process round.', 'warning')
        return redirect(url_for('admin_update_results', round_id=round_id))

    eliminated = 0
    survived = 0
    for pick in rnd.picks:
        if pick.is_winner is not None: continue
        fix = fixtures_by_team.get(normalize_team(pick.team_picked))
        if not fix: continue
        outcome = pick_outcome_for_fixture(pick, fix)
        if outcome == 'WIN':
            pick.is_winner = True
            survived += 1
        elif outcome == 'LOSE':
            pick.is_winner = False
            pick.is_eliminated = True
            if pick.player.status != 'eliminated':
                pick.player.status = 'eliminated'
            eliminated += 1

    rnd.status = 'completed'
    db.session.commit()

    flash(f'Auto-processed Round {rnd.round_number}: {survived} survived, {eliminated} eliminated.', 'success')
    app.logger.info(f"--- Finished auto-processing for round {round_id} ---")
    return redirect(url_for('admin_round_summary', round_id=round_id))



