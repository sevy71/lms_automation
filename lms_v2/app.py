"""
LMS V2 - Main Flask Application
Last Man Standing Premier League Prediction Game
"""
import os
import time
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_migrate import Migrate
from sqlalchemy.exc import OperationalError, DisconnectionError

from lms_v2.config import Config
from lms_v2.models import db, Player, Round, Fixture, Pick, PickToken, Reminder

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def create_app():
    """Application factory."""
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(Config)
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = Config.get_engine_options()

    # Initialize extensions
    db.init_app(app)
    Migrate(app, db)

    # Wait for database with retry
    if wait_for_db(app):
        # Create tables if they don't exist
        with app.app_context():
            try:
                db.create_all()
                logger.info("Database tables created/verified")
            except Exception as e:
                logger.error(f"Error creating tables: {e}")

    # Register blueprints
    from lms_v2.routes.admin import admin_bp
    from lms_v2.routes.player import player_bp
    from lms_v2.routes.api import api_bp

    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(player_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    # Home route
    @app.route('/')
    def index():
        return render_template('index.html')

    # Health check
    @app.route('/health')
    def health():
        try:
            db.session.execute(db.text('SELECT 1'))
            return jsonify({'status': 'healthy', 'database': 'connected'})
        except Exception as e:
            return jsonify({'status': 'unhealthy', 'database': str(e)}), 500

    # Reset database (one-time use to fix schema mismatch)
    @app.route('/reset-db/<secret>')
    def reset_db(secret):
        if secret != 'confirm-reset-2026':
            return jsonify({'error': 'Invalid secret'}), 403
        try:
            # Drop all tables with CASCADE to handle foreign keys
            db.session.execute(db.text('DROP SCHEMA public CASCADE'))
            db.session.execute(db.text('CREATE SCHEMA public'))
            db.session.commit()
            # Recreate tables
            db.create_all()
            # Seed with test players
            from lms_v2.seed import seed_players
            seed_players()
            return jsonify({'status': 'Database reset complete', 'message': 'All tables recreated with 20 test players'})
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500

    return app


def wait_for_db(app, max_retries=5, base_delay=2):
    """Wait for database connection with exponential backoff."""
    for attempt in range(max_retries):
        try:
            with app.app_context():
                db.session.execute(db.text('SELECT 1'))
                db.session.rollback()
                logger.info("Database connection established")
                return True
        except (OperationalError, DisconnectionError) as e:
            delay = base_delay * (2 ** attempt)
            if attempt < max_retries - 1:
                logger.warning(f"DB connection attempt {attempt + 1}/{max_retries} failed. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"DB connection failed after {max_retries} attempts. App will continue.")
                return False
        except Exception as e:
            logger.error(f"Unexpected DB error: {e}")
            return False
    return False


# Auth decorator for admin routes
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated


# Create the app instance
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
