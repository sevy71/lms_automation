from flask_migrate import Migrate
from lms_automation.models import db

migrate = Migrate()

__all__ = ["db", "migrate"]
