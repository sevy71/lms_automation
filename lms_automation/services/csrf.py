"""
Shared CSRF helpers.

Generates and validates a per-session CSRF token.  The token is stored in the
Flask session and can be delivered to templates via the ``csrf_token`` Jinja
global, or to JS via the ``<meta name="csrf-token">`` injected in base.html.

For form submissions: read token from ``request.form['csrf_token']``.
For AJAX/JSON calls:  read token from ``X-CSRF-Token`` request header.
"""
from __future__ import annotations

import hmac
import secrets

from flask import session


def generate_csrf_token() -> str:
    """Return (and lazily create) the CSRF token for the current session."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf_token(token: str) -> bool:
    """Return True if *token* matches the session token (constant-time compare)."""
    expected = session.get("csrf_token", "")
    return bool(expected and hmac.compare_digest(expected, token))


def get_request_csrf_token(request) -> str:
    """Extract the CSRF token from either the X-CSRF-Token header or form field."""
    return request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
