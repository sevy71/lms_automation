import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Config:
    """Base configuration for modular LMS platform experiment."""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DISPLAY_TIMEZONE = os.environ.get('DISPLAY_TIMEZONE', 'Europe/London')

    _db_url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
    if _db_url:
        SQLALCHEMY_DATABASE_URI = _db_url.replace('postgres://', 'postgresql://')
    else:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{BASE_DIR / 'lms.db'}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
    ADMIN_WHATSAPP = os.environ.get('ADMIN_WHATSAPP')
    CRON_SECRET = os.environ.get('CRON_SECRET')


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
