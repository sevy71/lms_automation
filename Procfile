web: echo "=== RUNNING DATABASE MIGRATION ===" && python -m flask --app lms_automation.app:app db upgrade && echo "=== MIGRATION COMPLETE ===" && python run_with_scheduler.py
