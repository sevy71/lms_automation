"""Authentication helpers for admin views."""

from __future__ import annotations

from functools import wraps
from typing import Callable, TypeVar

from flask import flash, redirect, request, session, url_for

F = TypeVar('F', bound=Callable[..., object])


def admin_required(view: F) -> F:
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get('admin_authenticated'):
            return view(*args, **kwargs)
        flash('Please log in as admin to access this page.', 'warning')
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for('auth.login', next=next_url))

    return wrapper  # type: ignore[return-value]
