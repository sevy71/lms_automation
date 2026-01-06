from flask import Blueprint, redirect, render_template, url_for

from ...auth import admin_required
from ...services import admin as admin_service
from ...services import reminders as reminder_service

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.get('/')
@admin_required
def index():
    return redirect(url_for('.dashboard'))


@bp.get('/dashboard')
@admin_required
def dashboard():
    context = admin_service.dashboard_context()
    return render_template('admin_dashboard.html', **context)


@bp.get('/picks-grid')
@admin_required
def picks_grid():
    return render_template('picks_grid.html')


@bp.get('/reminders')
@admin_required
def reminders_dashboard():
    context = reminder_service.dashboard_context()
    return render_template('reminders_dashboard.html', **context)
