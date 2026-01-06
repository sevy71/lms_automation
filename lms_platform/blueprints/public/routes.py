from flask import Blueprint, render_template

bp = Blueprint('public', __name__)


@bp.get('/')
def index():
    return render_template('index.html')


@bp.get('/register')
def player_registration():
    return render_template('player_registration.html')


@bp.get('/rules')
def rules():
    return render_template('rules.html')
