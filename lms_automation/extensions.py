import os
import time
import logging
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.exc import OperationalError, DisconnectionError

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Connection Pool Configuration for Railway/Managed Postgres
# -----------------------------------------------------------------------------
# These settings ensure the app survives transient Postgres restarts:
#
# - pool_pre_ping: Tests connections before use, discarding stale ones
# - pool_recycle: Forces connections to be recycled every 5 min (300s)
#                 This prevents issues with Railway's connection timeouts
# - pool_size: Conservative pool (5 connections) to avoid exhausting DB limits
# - max_overflow: Allows 5 extra connections under load
# - pool_timeout: Wait up to 30s for a connection from the pool
# -----------------------------------------------------------------------------

def get_engine_options():
    """Return SQLAlchemy engine options based on database type."""
    database_uri = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL') or ''

    # SQLite doesn't support connection pooling the same way
    if 'sqlite' in database_uri.lower() or not database_uri:
        return {}

    # PostgreSQL (Railway) - minimal pooling for stability
    # Railway free tier Postgres has limited connections (~20-25 max)
    return {
        'pool_pre_ping': True,       # Test connections before use
        'pool_recycle': 180,         # Recycle connections every 3 minutes
        'pool_size': 2,              # Minimal pool size for stability
        'max_overflow': 3,           # Allow 3 extra connections under load (5 total max)
        'pool_timeout': 30,          # Wait up to 30s for connection
        'connect_args': {
            'connect_timeout': 10,   # Fail fast if DB unreachable
            'keepalives': 1,         # Enable TCP keepalives
            'keepalives_idle': 30,   # Start keepalives after 30s idle
            'keepalives_interval': 10,  # Keepalive every 10s
            'keepalives_count': 5,   # Give up after 5 failed keepalives
        }
    }


# Create db instance - engine options will be applied via SQLALCHEMY_ENGINE_OPTIONS
db = SQLAlchemy()


# -----------------------------------------------------------------------------
# Database Connection Helper with Retry Logic
# -----------------------------------------------------------------------------

def wait_for_db(app, max_retries=5, base_delay=2):
    """
    Wait for database to become available with exponential backoff.

    This prevents the app from crashing if Postgres is temporarily unavailable
    during Railway restarts or brief network issues.

    Args:
        app: Flask application instance
        max_retries: Maximum number of connection attempts (default: 5)
        base_delay: Initial delay in seconds, doubles each retry (default: 2)

    Returns:
        True if connection succeeded, False if all retries exhausted
    """
    for attempt in range(max_retries):
        try:
            with app.app_context():
                # Try to execute a simple query to verify connection
                db.session.execute(db.text('SELECT 1'))
                db.session.rollback()  # Clean up the transaction
                logger.info("Database connection established successfully")
                return True
        except (OperationalError, DisconnectionError) as e:
            delay = base_delay * (2 ** attempt)  # Exponential backoff: 2, 4, 8, 16, 32 seconds
            if attempt < max_retries - 1:
                logger.warning(
                    f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"Database connection failed after {max_retries} attempts. "
                    f"Last error: {e}. App will continue but database operations may fail."
                )
                return False
        except Exception as e:
            # Non-connection errors should be logged but not retried
            logger.error(f"Unexpected database error during startup: {e}")
            return False
    return False


def safe_db_operation(func):
    """
    Decorator for database operations that handles transient connection errors.

    Use this for critical operations that should retry on connection failure.
    """
    def wrapper(*args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (OperationalError, DisconnectionError) as e:
                db.session.rollback()
                if attempt < max_retries - 1:
                    delay = 1 * (2 ** attempt)
                    logger.warning(f"DB operation failed (attempt {attempt + 1}): {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"DB operation failed after {max_retries} attempts: {e}")
                    raise
    return wrapper