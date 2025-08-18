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

# Load environment variables
load_dotenv()

# Import database and models
from .database import db
from .models import Player, Round, Fixture, Pick, SendQueue, WhatsAppSend

# Import our API module
from .football_data_api import get_upcoming_premier_league_fixtures, get_premier_league_fixtures_by_season, get_fixture_by_id, get_fixtures_by_ids

# --- App Initialization ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'lms.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'please_change_me')

# Connect database to the app
db.init_app(app)


# --- Token generator for per-round pick links ---
pick_link_serializer = URLSafeSerializer(app.config['SECRET_KEY'], salt='pick-link')

def make_pick_token(player_id: int, round_id: int) -> str:
    return pick_link_serializer.dumps({'p': int(player_id), 'r': int(round_id)})

def parse_pick_token(token: str):
    try:
        data = pick_link_serializer.loads(token)
        return int(data.get('p')), int(data.get('r'))
    except BadSignature:
        return None, None

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

# --- WhatsApp Cloud API configuration (set via environment variables) ---
WA_ACCESS_TOKEN = os.environ.get('WA_ACCESS_TOKEN')  # Permanent system-user token recommended
WA_PHONE_NUMBER_ID = os.environ.get('WA_PHONE_NUMBER_ID')  # Numeric Phone Number ID for the sending number
WA_API_VERSION = os.environ.get('WA_API_VERSION', 'v23.0')
WA_TEMPLATE_NAME = os.environ.get('WA_TEMPLATE_NAME')  # Optional approved template name (e.g., 'lms_team_pick')
WA_TEMPLATE_LANG = os.environ.get('WA_TEMPLATE_LANG', 'en_GB')
try:
    WA_TEMPLATE_PARAM_COUNT = int(os.environ.get('WA_TEMPLATE_PARAM_COUNT', '4'))
except Exception:
    WA_TEMPLATE_PARAM_COUNT = 4

def _digits_only(s: str) -> str:
    return ''.join(ch for ch in (s or '') if ch.isdigit())

def to_e164_digits(whatsapp_number: str) -> str:
    """
    Returns a digits-only E.164-like string without '+'.
    If the number starts with '0' (UK local), we do NOT guess country code here to avoid mistakes.
    Expect callers to store numbers with country code, e.g., '447...'.
    """
    d = _digits_only(whatsapp_number)
    # Warn in server logs if it looks like a local UK number (starts with 0 and length 11)
    if d.startswith('0') and len(d) == 11:
        app.logger.warning("WhatsApp number looks local (starts with 0). Store as E.164 without '+', e.g., '447...': %s", d)
    return d

def whatsapp_send_message(to_digits: str, body_text: str = None, template_name: str = None, template_params: list = None):
    """
    Send a WhatsApp message using Cloud API.
    - If template_name is provided, sends a template with optional body parameters.
    - Else sends a simple text message (requires the 24h customer window).
    Returns (ok: bool, response_json: dict|None, error_msg: str|None)
    """
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        return False, None, "WhatsApp API not configured (missing WA_ACCESS_TOKEN or WA_PHONE_NUMBER_ID)."

    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    if template_name:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_digits,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": WA_TEMPLATE_LANG},
            }
        }
        if template_params:
            payload["template"]["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in template_params]
            }]
    else:
        if not body_text:
            return False, None, "No body_text provided for text message and no template_name specified."
        payload = {
            "messaging_product": "whatsapp",
            "to": to_digits,
            "type": "text",
            "text": {"body": body_text}
        }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as e:
        return False, None, f"Network error: {e}"

    try:
        data = resp.json()
    except Exception:
        data = None

    if resp.status_code == 200 or resp.status_code == 201:
        return True, data, None
    else:
        # Extract helpful error message
        err = None
        if isinstance(data, dict):
            err = (data.get('error') or {}).get('message') or str(data)
            app.logger.error("WhatsApp API Error Response: %s", json.dumps(data, indent=2)) # Log full error response
        else:
            err = f"HTTP {resp.status_code}"
        app.logger.error("WhatsApp API Call Failed: Status %s, Error: %s", resp.status_code, err) # Log the failure
        return False, data, err

def _consecutive_whatsapp_failures(player_id: int, window: int = 10) -> int:
    """Return number of consecutive failed sends for the given player, looking back up to `window` attempts."""
    sends = WhatsAppSend.query.filter_by(player_id=player_id).order_by(WhatsAppSend.timestamp.desc()).limit(window).all()
    count = 0
    for s in sends:
        if s.ok:
            break
        count += 1
    return count

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

# Route to load fixtures for a specific season into the database
@app.route('/admin/load_fixtures/<int:season_year>')
def admin_load_fixtures(season_year):
    fixtures_data = get_premier_league_fixtures_by_season(season_year)
    if not fixtures_data:
        return f"<p>No fixtures found for season {season_year} from API-Football.</p>"

    fixtures_added_count = 0
    for fixture_api in fixtures_data:
        event_id = str(fixture_api['fixture']['id']) # Ensure event_id is string
        home_team = fixture_api['teams']['home']['name']
        away_team = fixture_api['teams']['away']['name']
        fixture_date_str = fixture_api['fixture']['date']
        fixture_date = datetime.fromisoformat(fixture_date_str.replace('Z', '+00:00'))
        fixture_time = fixture_date.strftime('%H:%M')
        
        # Extract round number (assuming 'round' field exists and is consistent)
        # API-Football's 'round' field is like "Regular Season - 1", "Regular Season - 2"
        round_name = fixture_api['league']['round']
        try:
            round_number = int(round_name.split(' - ')[1])
        except (IndexError, ValueError):
            round_number = 0 # Default or handle error if round name is not as expected

        # Check if round exists, create if not
        round_obj = Round.query.filter_by(round_number=round_number).first()
        if not round_obj:
            # For simplicity, setting start/end dates to fixture date for now.
            # In a real scenario, you'd define round start/end more broadly.
            round_obj = Round(
                round_number=round_number,
                start_date=fixture_date.date(), # Store date only for round
                end_date=fixture_date.date(),   # Store date only for round
                status='open'
            )
            db.session.add(round_obj)
            db.session.commit() # Commit to get round_obj.id

        # Check if fixture already exists to avoid duplicates
        existing_fixture = Fixture.query.filter_by(event_id=event_id).first()
        if not existing_fixture:
            new_fixture = Fixture(
                round_id=round_obj.id,
                event_id=event_id,
                home_team=home_team,
                away_team=away_team,
                date=fixture_date,
                time=fixture_time,
                home_score=fixture_api['goals']['home'],
                away_score=fixture_api['goals']['away'],
                status=fixture_api['fixture']['status']['short'] # e.g., 'FT', 'NS'
            )
            db.session.add(new_fixture)
            fixtures_added_count += 1
        else:
            # Update existing fixture if status or scores have changed
            existing_fixture.home_score = fixture_api['goals']['home']
            existing_fixture.away_score = fixture_api['goals']['away']
            existing_fixture.status = fixture_api['fixture']['status']['short']

    db.session.commit()
    return f"<p>Loaded {fixtures_added_count} new fixtures for season {season_year} into the database. Existing fixtures updated.</p>"

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
    has_wa_config = bool(WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID)

    return render_template('admin_dashboard.html', players=players, current_round=current_round, fixtures=fixtures, player_pick_links=player_pick_links, cleaned_whatsapp_numbers=cleaned_whatsapp_numbers, has_wa_config=has_wa_config)


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

    fixtures = Fixture.query.join(Round).order_by(Round.round_number, Fixture.date).all()

    for fixture in fixtures:
        row = [
            fixture.round.round_number if fixture.round else "N/A",
            fixture.home_team,
            fixture.away_team,
            fixture.date.strftime('%Y-%m-%d'),
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


@app.route('/admin/unassigned_fixtures')
def admin_unassigned_fixtures():
    fixtures = Fixture.query.order_by(Fixture.date.asc()).all()
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

# ----------------- Admin: Send WhatsApp links via Cloud API (batch) -----------------
@app.route('/admin/send_whatsapp_links', methods=['POST'])
def admin_send_whatsapp_links():
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        flash('WhatsApp API not configured. Set WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID env vars.', 'error')
        return redirect(url_for('admin_dashboard'))

    current_round = Round.query.filter_by(status='open').first()
    if not current_round:
        flash('No open round available.', 'error')
        return redirect(url_for('admin_dashboard'))

    players = Player.query.filter_by(status='active').all()
    if not players:
        flash('No active players to message.', 'warning')
        return redirect(url_for('admin_dashboard'))

    sent = 0
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
        to_digits = to_e164_digits(p.whatsapp_number)
        if not to_digits or not to_digits.isdigit():
            details.append(f"{p.name}: invalid number")
            failed += 1
            continue

        token = make_pick_token(p.id, current_round.id)
        pick_link = url_for('pick_with_token', token=token, _external=True)

        # Prefer template if provided via env; else send text
        if WA_TEMPLATE_NAME:
            deadline_str = compute_round_deadline(current_round)
            params = [p.name, current_round.round_number, pick_link, deadline_str]
            # Trim or pad to match expected template param count
            if len(params) > WA_TEMPLATE_PARAM_COUNT:
                params = params[:WA_TEMPLATE_PARAM_COUNT]
            elif len(params) < WA_TEMPLATE_PARAM_COUNT:
                params += [""] * (WA_TEMPLATE_PARAM_COUNT - len(params))
            ok, data, err = whatsapp_send_message(
                to_digits,
                template_name=WA_TEMPLATE_NAME,
                template_params=params
            )
        else:
            body = build_pick_message(p.name, current_round.round_number, pick_link)
            ok, data, err = whatsapp_send_message(to_digits, body_text=body)

        # Persist the send attempt
        try:
            payload_text = json.dumps({'template': WA_TEMPLATE_NAME, 'params': params}) if WA_TEMPLATE_NAME else (body if 'body' in locals() else '')
        except Exception:
            payload_text = ''
        wa_send = WhatsAppSend(player_id=p.id, ok=bool(ok), error_text=(err or None), payload=payload_text)
        db.session.add(wa_send)
        db.session.commit()

        # If failed, check consecutive failures and mark unreachable when >= 5
        if not ok:
            fails = _consecutive_whatsapp_failures(p.id, window=10)
            if fails >= 5:
                p.unreachable = True
                db.session.commit()
        if ok:
            sent += 1
            details.append(f"{p.name}: sent")
        else:
            failed += 1
            details.append(f"{p.name}: failed ({err})")
            app.logger.error(f"Failed to send WhatsApp to {p.name}: {err}") # Log the specific player failure

    flash(f"WhatsApp send complete: {sent} sent, {failed} failed.", 'success' if sent and not failed else 'warning')
    # Also surface a few detail lines for quick debug
    preview = "; ".join(details[:5])
    if preview:
        flash(f"Examples: {preview}", 'info')
    return redirect(url_for('admin_dashboard'))

# ----------------- Admin: Send WhatsApp link to a single player (API) -----------------
@app.route('/admin/send_whatsapp_link/<int:player_id>', methods=['POST'])
def admin_send_whatsapp_link(player_id):
    if not (WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID):
        flash('WhatsApp API not configured. Set WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID env vars.', 'error')
        return redirect(url_for('admin_dashboard'))

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

    if WA_TEMPLATE_NAME:
        deadline_str = compute_round_deadline(current_round)
        params = [player.name, current_round.round_number, pick_link, deadline_str]
        if len(params) > WA_TEMPLATE_PARAM_COUNT:
            params = params[:WA_TEMPLATE_PARAM_COUNT]
        elif len(params) < WA_TEMPLATE_PARAM_COUNT:
            params += [""] * (WA_TEMPLATE_PARAM_COUNT - len(params))
        ok, data, err = whatsapp_send_message(
            to_digits,
            template_name=WA_TEMPLATE_NAME,
            template_params=params
        )
    else:
        body = build_pick_message(p.name, current_round.round_number, pick_link)
        ok, data, err = whatsapp_send_message(to_digits, body_text=body)

    # Persist the send attempt
    try:
        payload_text = json.dumps({'template': WA_TEMPLATE_NAME, 'params': params}) if WA_TEMPLATE_NAME else (body if 'body' in locals() else '')
    except Exception:
        payload_text = ''
    wa_send = WhatsAppSend(player_id=player.id, ok=bool(ok), error_text=(err or None), payload=payload_text)
    db.session.add(wa_send)
    db.session.commit()

    if not ok:
        fails = _consecutive_whatsapp_failures(p.id, window=10)
        if fails >= 5:
            player.unreachable = True
            db.session.commit()

    if ok:
        flash(f"Sent pick link to {player.name}.", "success")
    else:
        flash(f"Failed to send to {player.name}: {err}", "error")
    return redirect(url_for('admin_dashboard'))


@app.route('/player_picks/<int:player_id>')
def player_picks(player_id):
    player = Player.query.get_or_404(player_id)
    picks = Pick.query.filter_by(player_id=player.id).join(Round).order_by(Round.round_number).all()
    return render_template('player_picks.html', player=player, picks=picks)


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
    for player in players:
        token = make_pick_token(player.id, round_obj.id)
        player_pick_links[player.id] = url_for('pick_with_token', token=token, _external=True)

    return render_template('round_links.html', round=round_obj, fixtures=fixtures, players=players, player_pick_links=player_pick_links)


if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Create database tables (will not add columns to existing tables)

        # Ensure small runtime migration: add `unreachable` column to `player` if missing.
        try:
            # Use PRAGMA to inspect existing columns in sqlite
            with db.engine.connect() as conn:
                res = conn.execute(text("PRAGMA table_info('player')")).all()
                cols = [r[1] for r in res]
                if 'unreachable' not in cols:
                    app.logger.info('Adding missing player.unreachable column to database')
                    conn.execute(text("ALTER TABLE player ADD COLUMN unreachable BOOLEAN DEFAULT 0"))
                    conn.commit()
                # Check for WhatsAppSend table
                res = conn.execute(text("PRAGMA table_info('whatsapp_send')")).all()
                if not res: # Table does not exist
                    app.logger.info('Creating missing whatsapp_send table')
                    # This will create the table if it doesn't exist
                    # For simplicity, relying on SQLAlchemy's create_all() to handle the actual table creation
                    # after the initial check. If create_all() is not sufficient for new tables,
                    # a more robust migration strategy (e.g., Alembic) would be needed.
                    pass # create_all() will handle this on next run if not already created
                    WhatsAppSend.__table__.create(db.engine) # Explicitly create the table
        except Exception as e:
            app.logger.exception('Failed to ensure DB schema: %s', e)
    # Start the Flask dev server
    app.run(debug=True, port=5001)
