from flask import Blueprint, jsonify, request

from ...auth import admin_required
from ...services.picks import build_picks_grid
from ...services import reminders as reminder_service

bp = Blueprint('api', __name__, url_prefix='/api')


@bp.get('/picks-grid')
@admin_required
def picks_grid():
    """Return picks grid data, optionally filtered by cycle.

    Query params:
        cycle: int - filter to a specific cycle. If omitted, defaults to current cycle.
    """
    requested_cycle = request.args.get('cycle', type=int)
    data = build_picks_grid(cycle=requested_cycle)
    return jsonify({'success': True, **data})


@bp.post('/admin/schedule-reminders/<int:round_id>')
@admin_required
def schedule_reminders(round_id: int):
    try:
        created = reminder_service.schedule_round_reminders(round_id)
        return jsonify({
            'success': True,
            'message': f'Created {created} reminders',
            'reminders_created': created,
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@bp.get('/admin/due-reminders')
@admin_required
def due_reminders():
    try:
        reminders = reminder_service.due_reminders_payload()
        return jsonify({'success': True, 'due_reminders': reminders, 'count': len(reminders)})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@bp.post('/admin/mark-reminder-sent/<int:reminder_id>')
@admin_required
def mark_reminder_sent(reminder_id: int):
    try:
        updated = reminder_service.mark_reminder_sent(reminder_id)
        if not updated:
            return jsonify({'success': False, 'error': 'Reminder not found'}), 404
        return jsonify({'success': True, 'message': 'Reminder marked as sent'})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500
