# Production entry-point for Railway (and Heroku-style platforms).
#
# --workers 1  is required: APScheduler uses background threads that must not
#              be duplicated across Gunicorn worker processes.  A single worker
#              is sufficient because the scheduler runs in its own threads and
#              the Flask app itself is I/O-bound.
#
# The background scheduler is auto-started by lms_automation/app.py when the
# WSGI module loads (gunicorn calls app:app directly, bypassing run_web.py).
#
# To disable the in-process scheduler and use run_scheduler.py separately,
# set the environment variable START_SCHEDULER=false on the web dyno and
# re-enable the worker dyno below.
web: gunicorn "lms_automation.app:app" --workers 1 --bind "0.0.0.0:$PORT"

# Standalone scheduler process — only enable this if you set START_SCHEDULER=false
# on the web dyno above to avoid running two scheduler instances simultaneously.
# worker: python run_scheduler.py
