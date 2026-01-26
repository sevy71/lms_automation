"""
LMS V2 - Configuration
"""
import os


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Database - Railway Postgres or local SQLite
    DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PUBLIC_URL')
    if DATABASE_URL:
        # Railway uses postgres:// but SQLAlchemy needs postgresql://
        SQLALCHEMY_DATABASE_URI = DATABASE_URL.replace('postgres://', 'postgresql://')
    else:
        SQLALCHEMY_DATABASE_URI = 'sqlite:///lms.db'

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # PostgreSQL connection pool settings for Railway resilience
    @staticmethod
    def get_engine_options():
        """Return engine options based on database type."""
        db_uri = Config.SQLALCHEMY_DATABASE_URI
        if 'sqlite' in db_uri:
            return {}
        # PostgreSQL - conservative pooling for Railway
        return {
            'pool_pre_ping': True,
            'pool_recycle': 180,
            'pool_size': 2,
            'max_overflow': 3,
            'pool_timeout': 30,
            'connect_args': {
                'connect_timeout': 10,
                'keepalives': 1,
                'keepalives_idle': 30,
                'keepalives_interval': 10,
                'keepalives_count': 5,
            }
        }

    # Admin
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

    # Telegram
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

    # Football Data API (support both variable names)
    FOOTBALL_API_TOKEN = os.environ.get('FOOTBALL_DATA_API_TOKEN') or os.environ.get('FOOTBALL_API_TOKEN')
    FOOTBALL_API_BASE = 'https://api.football-data.org/v4'
    PL_COMPETITION_ID = 2021  # Premier League

    # Game settings
    MAX_ROUNDS_PER_CYCLE = 20
    PICK_DEADLINE_HOURS_BEFORE = 1  # Hours before first kickoff

    # Scheduler intervals (minutes)
    FIXTURE_SYNC_INTERVAL = int(os.environ.get('FIXTURE_SYNC_INTERVAL', 30))
    FIXTURE_UPDATE_INTERVAL = int(os.environ.get('FIXTURE_UPDATE_INTERVAL', 5))
    ELIMINATION_INTERVAL = int(os.environ.get('ELIMINATION_INTERVAL', 2))
    REMINDER_INTERVAL = int(os.environ.get('REMINDER_INTERVAL', 5))

    # Timezone
    DISPLAY_TIMEZONE = os.environ.get('DISPLAY_TIMEZONE', 'Europe/London')
