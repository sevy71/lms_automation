"""
Shared route utilities — decorators used across multiple Blueprints.
"""
from functools import wraps
from flask import session, redirect, request, url_for, jsonify

from lms_automation.services.csrf import validate_csrf_token, get_request_csrf_token
from lms_automation.services.audit import log_admin_action

_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def admin_required(f):
    """Redirect/reject if the session is not authenticated.

    For state-changing methods (POST/PUT/PATCH/DELETE) also validates the CSRF
    token delivered either as ``X-CSRF-Token`` header or ``csrf_token`` form field.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.url))

        if request.method in _CSRF_METHODS:
            token = get_request_csrf_token(request)
            if not validate_csrf_token(token):
                log_admin_action(
                    "admin_api",
                    "blocked",
                    endpoint=request.endpoint,
                    reason="csrf_mismatch",
                )
                return jsonify({"success": False, "error": "CSRF validation failed"}), 403

        return f(*args, **kwargs)
    return decorated_function
