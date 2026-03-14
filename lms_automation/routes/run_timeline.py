"""
Run Timeline Blueprint — API endpoints for the timeline + checkpoint UI.

All routes require admin authentication.  State-changing routes also require
a valid CSRF token (enforced by @admin_required via routes/utils.py).

Prefix: /api/admin/timeline

Super-admin scope rules
-----------------------
* Read endpoints (GET): super-admins without a session organiser_id see **all**
  records (unfiltered).  Scoped admins (organiser or super with session org) see
  only their organiser's records.

* Mutating endpoints (POST rollback / replay / checkpoint): a super-admin
  **must** be bound to an organiser — either via the session ``organiser_id``
  or by supplying ``organiser_id`` in the JSON request body.  Calls without
  any scope are rejected with HTTP 403.

* Checkpoint / run ownership: when an effective ``organiser_id`` is present,
  mutating operations on a specific checkpoint/run verify that the record
  belongs to that organiser.
"""
from __future__ import annotations

import logging
import traceback
from flask import Blueprint, request, jsonify, session

from lms_automation.routes.utils import admin_required
from lms_automation.services.audit import log_admin_action
from lms_automation.services import run_timeline as svc
from lms_automation.models import AutomationRun, RunCheckpoint, db

logger = logging.getLogger(__name__)

run_timeline_bp = Blueprint('run_timeline', __name__)

_CONCURRENCY_LOCK: set[str] = set()  # simple in-process guard per organiser slug


def _actor() -> str:
    """Return current admin user identifier or 'system'."""
    uid = session.get('admin_user_id')
    if uid:
        return f'admin:{uid}'
    return 'system'


def _organiser_id() -> int | None:
    """Return session organiser_id (None for super-admins without scope)."""
    return session.get('organiser_id')


def _is_super_admin() -> bool:
    return session.get('admin_role') == 'super_admin'


def _effective_organiser_id(body: dict | None = None) -> int | None:
    """Resolve the effective organiser_id for the current request.

    For regular admins always returns the session value.
    For super-admins: session value if set, else ``organiser_id`` from body.
    Returns None if super-admin has no scope at all.
    """
    oid = session.get('organiser_id')
    if oid is not None:
        return oid
    if _is_super_admin() and body:
        # Accept explicit organiser_id from request body
        raw = body.get('organiser_id')
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return None


def _require_mutate_scope(body: dict | None = None):
    """Return ``(organiser_id, None)`` or ``(None, error_response)``.

    Mutating endpoints must have a resolved organiser scope.  Super-admins
    without a session organiser_id must supply one in the request body.
    """
    oid = _effective_organiser_id(body)
    if oid is None and _is_super_admin():
        return None, (
            jsonify({
                'success': False,
                'error': (
                    'Super-admin must supply organiser_id in the request body '
                    'when no organiser is bound to the session.'
                ),
            }),
            403,
        )
    return oid, None


def _check_checkpoint_ownership(checkpoint_id: str, organiser_id: int | None):
    """Return error response if checkpoint does not belong to organiser_id, else None."""
    if organiser_id is None:
        return None  # super-admin without scope — allow any (already guarded upstream)
    cp = RunCheckpoint.query.filter_by(checkpoint_id=checkpoint_id).first()
    if cp is None:
        return jsonify({'success': False, 'error': 'Checkpoint not found'}), 404
    if cp.organiser_id is not None and cp.organiser_id != organiser_id:
        log_admin_action('timeline_access_denied', 'blocked',
                         checkpoint_id=checkpoint_id, reason='organiser_mismatch')
        return jsonify({'success': False, 'error': 'Access denied: checkpoint belongs to a different organiser'}), 403
    return None


# ---------------------------------------------------------------------------
# GET /api/admin/timeline/runs
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/runs', methods=['GET'])
@admin_required
def list_runs():
    """Return recent automation runs, newest first."""
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        organiser_id = _organiser_id()
        runs = svc.get_recent_runs(organiser_id=organiser_id, limit=limit)
        return jsonify({'success': True, 'runs': runs, 'count': len(runs)})
    except Exception as e:
        logger.error('list_runs error: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/admin/timeline/runs/<run_id>
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/runs/<run_id>', methods=['GET'])
@admin_required
def get_run(run_id: str):
    """Return full detail for a single run including decisions and actions."""
    try:
        data = svc.get_run_detail(run_id)
        if data is None:
            return jsonify({'success': False, 'error': 'Run not found'}), 404
        # Scope check: scoped admins may only read their own runs
        oid = _organiser_id()
        if oid is not None and data.get('organiser_id') not in (None, oid):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        return jsonify({'success': True, 'run': data})
    except Exception as e:
        logger.error('get_run error: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/admin/timeline/checkpoints
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/checkpoints', methods=['GET'])
@admin_required
def list_checkpoints():
    """Return recent checkpoints."""
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
        organiser_id = _organiser_id()
        checkpoints = svc.get_checkpoints(organiser_id=organiser_id, limit=limit)
        return jsonify({'success': True, 'checkpoints': checkpoints, 'count': len(checkpoints)})
    except Exception as e:
        logger.error('list_checkpoints error: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/admin/timeline/audit
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/audit', methods=['GET'])
@admin_required
def list_audit():
    """Return audit events with optional filters."""
    try:
        run_id = request.args.get('run_id')
        event_type = request.args.get('event_type')
        limit = min(int(request.args.get('limit', 50)), 200)
        organiser_id = _organiser_id()
        events = svc.get_audit_events(
            run_id=run_id,
            organiser_id=organiser_id,
            event_type=event_type,
            limit=limit,
        )
        return jsonify({'success': True, 'events': events, 'count': len(events)})
    except Exception as e:
        logger.error('list_audit error: %s', e)
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/rollback/last-action
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/rollback/last-action', methods=['POST'])
@admin_required
def rollback_last_action():
    """Roll back the most recent reversible action."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    try:
        result = svc.rollback_last_action(organiser_id=organiser_id, actor=_actor())
        status = 'success' if result.get('success') else 'failure'
        log_admin_action('timeline_rollback_last_action', status,
                         impact=result.get('impact'), error=result.get('error'))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        logger.error('rollback_last_action error: %s\n%s', e, traceback.format_exc())
        log_admin_action('timeline_rollback_last_action', 'failure', error=str(e))
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/rollback/last-run
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/rollback/last-run', methods=['POST'])
@admin_required
def rollback_last_run():
    """Roll back the entire last completed run to its start-of-run checkpoint."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    try:
        result = svc.rollback_last_run(organiser_id=organiser_id, actor=_actor())
        status = 'success' if result.get('success') else 'failure'
        log_admin_action('timeline_rollback_last_run', status,
                         impact=result.get('impact'), error=result.get('error'))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        logger.error('rollback_last_run error: %s\n%s', e, traceback.format_exc())
        log_admin_action('timeline_rollback_last_run', 'failure', error=str(e))
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/rollback/checkpoint/<checkpoint_id>
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/rollback/checkpoint/<checkpoint_id>', methods=['POST'])
@admin_required
def rollback_checkpoint(checkpoint_id: str):
    """Roll back to a specific checkpoint."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    ownership_err = _check_checkpoint_ownership(checkpoint_id, organiser_id)
    if ownership_err:
        return ownership_err
    try:
        result = svc.rollback_to_checkpoint(checkpoint_id=checkpoint_id, actor=_actor())
        status = 'success' if result.get('success') else 'failure'
        log_admin_action('timeline_rollback_checkpoint', status,
                         checkpoint_id=checkpoint_id,
                         impact=result.get('impact'), error=result.get('error'))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        logger.error('rollback_checkpoint error: %s\n%s', e, traceback.format_exc())
        log_admin_action('timeline_rollback_checkpoint', 'failure',
                         checkpoint_id=checkpoint_id, error=str(e))
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/replay/dry-run/<checkpoint_id>
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/replay/dry-run/<checkpoint_id>', methods=['POST'])
@admin_required
def replay_dry_run(checkpoint_id: str):
    """Preview what a replay from a checkpoint would change (no writes)."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    ownership_err = _check_checkpoint_ownership(checkpoint_id, organiser_id)
    if ownership_err:
        return ownership_err
    try:
        result = svc.replay_from_checkpoint(
            checkpoint_id=checkpoint_id, dry_run=True, actor=_actor()
        )
        status = 'success' if result.get('success') else 'failure'
        log_admin_action('timeline_replay_dry_run', status, checkpoint_id=checkpoint_id)
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        logger.error('replay_dry_run error: %s\n%s', e, traceback.format_exc())
        log_admin_action('timeline_replay_dry_run', 'failure',
                         checkpoint_id=checkpoint_id, error=str(e))
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/replay/apply/<checkpoint_id>
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/replay/apply/<checkpoint_id>', methods=['POST'])
@admin_required
def replay_apply(checkpoint_id: str):
    """Apply a replay from a checkpoint — creates a new run record."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    ownership_err = _check_checkpoint_ownership(checkpoint_id, organiser_id)
    if ownership_err:
        return ownership_err

    organiser_key = str(organiser_id or 'global')

    # Concurrency guard: prevent simultaneous conflicting replays
    if organiser_key in _CONCURRENCY_LOCK:
        return jsonify({
            'success': False,
            'error': 'A run is already in progress for this organiser. Please wait.',
        }), 409

    _CONCURRENCY_LOCK.add(organiser_key)
    try:
        result = svc.replay_from_checkpoint(
            checkpoint_id=checkpoint_id, dry_run=False, actor=_actor()
        )
        status = 'success' if result.get('success') else 'failure'
        log_admin_action('timeline_replay_apply', status,
                         checkpoint_id=checkpoint_id, new_run_id=result.get('new_run_id'))
        return jsonify(result), (200 if result.get('success') else 400)
    except Exception as e:
        logger.error('replay_apply error: %s\n%s', e, traceback.format_exc())
        log_admin_action('timeline_replay_apply', 'failure',
                         checkpoint_id=checkpoint_id, error=str(e))
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        _CONCURRENCY_LOCK.discard(organiser_key)


# ---------------------------------------------------------------------------
# POST /api/admin/timeline/checkpoints  (manual checkpoint creation)
# ---------------------------------------------------------------------------

@run_timeline_bp.route('/api/admin/timeline/checkpoints', methods=['POST'])
@admin_required
def create_manual_checkpoint():
    """Create a manual checkpoint labelled by the admin."""
    body = request.get_json(silent=True) or {}
    organiser_id, err = _require_mutate_scope(body)
    if err:
        return err
    try:
        label = (body.get('label') or 'Manual checkpoint').strip()[:200]
        cp = svc.create_checkpoint(run=None, label=label, organiser_id=organiser_id)
        db.session.commit()
        log_admin_action('timeline_create_checkpoint', 'success',
                         checkpoint_id=cp.checkpoint_id, label=label)
        return jsonify({
            'success': True,
            'checkpoint': svc._serialise_checkpoint(cp),
        })
    except Exception as e:
        logger.error('create_manual_checkpoint error: %s', e)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
