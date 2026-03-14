"""
Run Timeline test suite.

Covers:
  1. Service layer: create/complete run, decisions, actions, checkpoints
  2. Exception path: failed run records correct status + audit event
  3. Rollback: rollback_to_checkpoint restores player/pick/round state
  4. Rollback last run: marks run as 'rolled-back'
  5. Concurrency guard: second replay_apply for same organiser returns 409
  6. Replay dry-run: returns preview without writing to DB
  7. Retention: prune_old_data respects retention policy env vars
  8. Super-admin scope: mutating endpoints require organiser scope

Run:
    python -m pytest tests/test_run_timeline.py -v
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault('SECRET_KEY', 'test-secret-not-for-prod')
os.environ.setdefault('ADMIN_PASSWORD', 'TestPassword123!')
os.environ.setdefault('FLASK_ENV', 'testing')


# ---------------------------------------------------------------------------
# App + blueprint factory
# ---------------------------------------------------------------------------

def _make_app():
    """Minimal Flask app with run-timeline blueprint, backed by in-memory SQLite."""
    from flask import Flask
    from lms_automation.extensions import db as _ext_db
    import lms_automation.models  # noqa: F401

    app = Flask('lms_test_timeline')
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = 'test-secret-not-for-prod'
    app.config['WTF_CSRF_ENABLED'] = False

    _ext_db.init_app(app)

    # Register blueprints required for route tests
    from lms_automation.routes.run_timeline import run_timeline_bp
    from lms_automation.services.csrf import generate_csrf_token  # noqa: F401
    app.register_blueprint(run_timeline_bp)

    # Minimal admin_login route so redirect works
    @app.route('/admin/login')
    def admin_login():
        return 'login', 200

    return app


def _create_tables(db):
    db.create_all()


def _seed_organiser(db):
    """Create two organisers."""
    from lms_automation.models import Organiser
    org_a = Organiser(name='Org A', slug='org-a', status='active')
    org_b = Organiser(name='Org B', slug='org-b', status='active')
    db.session.add_all([org_a, org_b])
    db.session.commit()
    return org_a, org_b


# ---------------------------------------------------------------------------
# Base test class
# ---------------------------------------------------------------------------

class TimelineTestBase(unittest.TestCase):

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from lms_automation.extensions import db as _db
        self.db = _db
        _create_tables(_db)
        self.org_a, self.org_b = _seed_organiser(_db)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _CSRF_TOKEN = 'test-csrf-token-static'

    def _admin_session(self, client, organiser_id, role='organiser_admin'):
        """Push a minimal admin session (including CSRF token) into the test client."""
        with client.session_transaction() as sess:
            sess['admin_logged_in'] = True
            sess['admin_user_id'] = 1
            sess['admin_role'] = role
            sess['organiser_id'] = organiser_id
            sess['csrf_token'] = self._CSRF_TOKEN

    def _super_admin_session(self, client, organiser_id=None):
        self._admin_session(client, organiser_id, role='super_admin')

    def _post(self, client, url, json_body=None):
        """POST with CSRF token header."""
        return client.post(
            url,
            json=json_body or {},
            headers={'X-CSRF-Token': self._CSRF_TOKEN},
        )


# ---------------------------------------------------------------------------
# 1. Service layer — create/complete/decision/action/checkpoint
# ---------------------------------------------------------------------------

class TestServiceLayer(TimelineTestBase):

    def test_create_run_persists(self):
        from lms_automation.services.run_timeline import create_run
        from lms_automation.models import AutomationRun

        run = create_run(trigger_type='scheduler', mode='auto', organiser_id=self.org_a.id)
        self.db.session.commit()

        persisted = AutomationRun.query.filter_by(run_id=run.run_id).first()
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, 'running')
        self.assertEqual(persisted.trigger_type, 'scheduler')
        self.assertEqual(persisted.organiser_id, self.org_a.id)

    def test_create_run_emits_run_started_audit(self):
        from lms_automation.services.run_timeline import create_run
        from lms_automation.models import RunAuditEvent

        run = create_run(trigger_type='manual', mode='manual', organiser_id=self.org_a.id)
        self.db.session.commit()

        evt = RunAuditEvent.query.filter_by(run_id=run.run_id, event_type='run_started').first()
        self.assertIsNotNone(evt)

    def test_complete_run_updates_status(self):
        from lms_automation.services.run_timeline import create_run, complete_run
        from lms_automation.models import AutomationRun

        run = create_run(organiser_id=self.org_a.id)
        complete_run(run, status='success', total_actions=3, affected_players=2, affected_rounds=1)
        self.db.session.commit()

        persisted = AutomationRun.query.get(run.id)
        self.assertEqual(persisted.status, 'success')
        self.assertEqual(persisted.total_actions, 3)
        self.assertEqual(persisted.affected_players, 2)
        self.assertIsNotNone(persisted.completed_at)

    def test_record_decision_and_action(self):
        from lms_automation.services.run_timeline import create_run, record_decision, record_action
        from lms_automation.models import RunDecision, RunAction

        run = create_run(organiser_id=self.org_a.id)
        dec = record_decision(run, step_number=1, title='Test decision',
                              rule_matched='rule_x', confidence='high')
        action = record_action(run, action_type='round_completed', outcome='applied',
                               actual_impact='round 3 done', actor='system', reversible=True,
                               decision=dec)
        self.db.session.commit()

        self.assertIsNotNone(RunDecision.query.get(dec.id))
        self.assertIsNotNone(RunAction.query.get(action.id))
        self.assertEqual(action.decision_id, dec.id)
        self.assertTrue(action.reversible)

    def test_create_checkpoint_stores_snapshot(self):
        from lms_automation.services.run_timeline import create_run, create_checkpoint
        from lms_automation.models import RunCheckpoint

        run = create_run(organiser_id=self.org_a.id)
        cp = create_checkpoint(run=run, label='Before round 3', organiser_id=self.org_a.id)
        self.db.session.commit()

        persisted = RunCheckpoint.query.filter_by(checkpoint_id=cp.checkpoint_id).first()
        self.assertIsNotNone(persisted)
        snapshot = json.loads(persisted.snapshot)
        self.assertIn('captured_at', snapshot)
        self.assertIn('rounds', snapshot)
        self.assertIn('players', snapshot)

    def test_get_recent_runs_scoped(self):
        from lms_automation.services.run_timeline import create_run, complete_run, get_recent_runs

        run_a = create_run(organiser_id=self.org_a.id)
        complete_run(run_a, status='success')
        run_b = create_run(organiser_id=self.org_b.id)
        complete_run(run_b, status='success')
        self.db.session.commit()

        runs_a = get_recent_runs(organiser_id=self.org_a.id)
        run_ids_a = [r['run_id'] for r in runs_a]
        self.assertIn(run_a.run_id, run_ids_a)
        self.assertNotIn(run_b.run_id, run_ids_a)

    def test_get_run_detail_includes_decisions_and_actions(self):
        from lms_automation.services.run_timeline import (
            create_run, complete_run, record_decision, record_action, get_run_detail,
        )

        run = create_run(organiser_id=self.org_a.id)
        dec = record_decision(run, step_number=1, title='Decision A')
        record_action(run, action_type='act_x', outcome='applied', decision=dec)
        complete_run(run, status='success')
        self.db.session.commit()

        detail = get_run_detail(run.run_id)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail['decisions']), 1)
        self.assertEqual(len(detail['actions']), 1)

    def test_get_run_detail_returns_none_for_missing(self):
        from lms_automation.services.run_timeline import get_run_detail
        self.assertIsNone(get_run_detail('nonexistent-run-id'))


# ---------------------------------------------------------------------------
# 2. Exception path: failed run
# ---------------------------------------------------------------------------

class TestFailedRunPath(TimelineTestBase):

    def test_complete_run_with_error_status(self):
        from lms_automation.services.run_timeline import create_run, complete_run
        from lms_automation.models import AutomationRun

        run = create_run(organiser_id=self.org_a.id)
        complete_run(run, status='failed', error_info='Timeout error')
        self.db.session.commit()

        persisted = AutomationRun.query.get(run.id)
        self.assertEqual(persisted.status, 'failed')
        self.assertEqual(persisted.error_info, 'Timeout error')

    def test_failed_run_emits_run_completed_audit(self):
        from lms_automation.services.run_timeline import create_run, complete_run
        from lms_automation.models import RunAuditEvent

        run = create_run(organiser_id=self.org_a.id)
        complete_run(run, status='failed', error_info='Something broke')
        self.db.session.commit()

        evt = RunAuditEvent.query.filter_by(run_id=run.run_id, event_type='run_completed').first()
        self.assertIsNotNone(evt)
        details = json.loads(evt.details)
        self.assertEqual(details['status'], 'failed')


# ---------------------------------------------------------------------------
# 3. Rollback: rollback_to_checkpoint restores state
# ---------------------------------------------------------------------------

class TestRollback(TimelineTestBase):

    def _make_player_and_checkpoint(self):
        """Create a player at 'active' status, take a checkpoint, then eliminate them."""
        from lms_automation.models import Player
        from lms_automation.services.run_timeline import create_run, create_checkpoint

        player = Player(name='TestPlayer', organiser_id=self.org_a.id, status='active')
        self.db.session.add(player)
        self.db.session.flush()

        run = create_run(organiser_id=self.org_a.id)
        cp = create_checkpoint(run=run, label='Before elimination', organiser_id=self.org_a.id)
        self.db.session.commit()

        # Now eliminate the player (simulating what process_eliminations does)
        player.status = 'eliminated'
        self.db.session.commit()

        return player, cp

    def test_rollback_restores_player_status(self):
        from lms_automation.services.run_timeline import rollback_to_checkpoint

        player, cp = self._make_player_and_checkpoint()
        self.assertEqual(player.status, 'eliminated')

        result = rollback_to_checkpoint(cp.checkpoint_id, actor='admin:1')
        self.assertTrue(result['success'])

        self.db.session.refresh(player)
        self.assertEqual(player.status, 'active')

    def test_rollback_emits_audit_event(self):
        from lms_automation.models import RunAuditEvent
        from lms_automation.services.run_timeline import rollback_to_checkpoint

        _, cp = self._make_player_and_checkpoint()
        rollback_to_checkpoint(cp.checkpoint_id, actor='admin:1')

        evt = RunAuditEvent.query.filter_by(event_type='rollback_applied').first()
        self.assertIsNotNone(evt)
        details = json.loads(evt.details)
        self.assertEqual(details['checkpoint_id'], cp.checkpoint_id)

    def test_rollback_nonexistent_checkpoint_returns_error(self):
        from lms_automation.services.run_timeline import rollback_to_checkpoint

        result = rollback_to_checkpoint('does-not-exist', actor='system')
        self.assertFalse(result['success'])
        self.assertIn('not found', result['error'])


# ---------------------------------------------------------------------------
# 4. Rollback last run marks run as rolled-back
# ---------------------------------------------------------------------------

class TestRollbackLastRun(TimelineTestBase):

    def test_rollback_last_run_marks_rolled_back(self):
        from lms_automation.models import AutomationRun
        from lms_automation.services.run_timeline import (
            create_run, complete_run, create_checkpoint, rollback_last_run,
        )

        run = create_run(organiser_id=self.org_a.id)
        create_checkpoint(run=run, label='Start of run', organiser_id=self.org_a.id)
        complete_run(run, status='success')
        self.db.session.commit()

        result = rollback_last_run(organiser_id=self.org_a.id, actor='admin:1')
        self.assertTrue(result['success'])

        persisted = AutomationRun.query.get(run.id)
        self.assertEqual(persisted.status, 'rolled-back')

    def test_rollback_last_run_no_run_returns_error(self):
        from lms_automation.services.run_timeline import rollback_last_run

        result = rollback_last_run(organiser_id=self.org_a.id, actor='admin:1')
        self.assertFalse(result['success'])
        self.assertIn('No completed run', result['error'])


# ---------------------------------------------------------------------------
# 5. Concurrency guard: second replay_apply for same organiser returns 409
# ---------------------------------------------------------------------------

class TestConcurrencyGuard(TimelineTestBase):

    def _create_checkpoint_for_org(self, organiser_id):
        from lms_automation.services.run_timeline import create_checkpoint
        cp = create_checkpoint(run=None, label='Test checkpoint', organiser_id=organiser_id)
        self.db.session.commit()
        return cp

    def test_concurrent_replay_returns_409(self):
        from lms_automation.routes.run_timeline import _CONCURRENCY_LOCK

        cp = self._create_checkpoint_for_org(self.org_a.id)
        organiser_key = str(self.org_a.id)

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)

            # Pre-lock to simulate an in-progress run
            _CONCURRENCY_LOCK.add(organiser_key)
            try:
                resp = self._post(
                    client,
                    f'/api/admin/timeline/replay/apply/{cp.checkpoint_id}',
                )
                self.assertEqual(resp.status_code, 409)
                data = resp.get_json()
                self.assertFalse(data['success'])
                self.assertIn('already in progress', data['error'])
            finally:
                _CONCURRENCY_LOCK.discard(organiser_key)

    def test_different_organiser_not_blocked(self):
        """A lock for org_a should not block org_b."""
        from lms_automation.routes.run_timeline import _CONCURRENCY_LOCK

        cp_b = self._create_checkpoint_for_org(self.org_b.id)
        organiser_key_a = str(self.org_a.id)

        with self.app.test_client() as client:
            self._admin_session(client, self.org_b.id)
            _CONCURRENCY_LOCK.add(organiser_key_a)
            try:
                # org_b replay should not be blocked by org_a lock
                # It will fail at the service level (no run records) but not 409
                resp = self._post(client, f'/api/admin/timeline/replay/apply/{cp_b.checkpoint_id}')
                self.assertNotEqual(resp.status_code, 409)
            finally:
                _CONCURRENCY_LOCK.discard(organiser_key_a)


# ---------------------------------------------------------------------------
# 6. Replay dry-run: returns preview, no DB writes
# ---------------------------------------------------------------------------

class TestReplayDryRun(TimelineTestBase):

    def test_dry_run_returns_preview_not_404(self):
        from lms_automation.services.run_timeline import create_checkpoint

        cp = create_checkpoint(run=None, label='Dry-run test', organiser_id=self.org_a.id)
        self.db.session.commit()

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            resp = self._post(client, f'/api/admin/timeline/replay/dry-run/{cp.checkpoint_id}')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data['success'])
            self.assertTrue(data.get('dry_run'))
            self.assertIn('preview', data)

    def test_dry_run_does_not_create_run_record(self):
        from lms_automation.models import AutomationRun
        from lms_automation.services.run_timeline import create_checkpoint

        count_before = AutomationRun.query.count()

        cp = create_checkpoint(run=None, label='Dry-run no-write', organiser_id=self.org_a.id)
        self.db.session.commit()

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            self._post(client, f'/api/admin/timeline/replay/dry-run/{cp.checkpoint_id}')

        count_after = AutomationRun.query.count()
        self.assertEqual(count_before, count_after)


# ---------------------------------------------------------------------------
# 7. Retention: prune_old_data respects env-var policy
# ---------------------------------------------------------------------------

class TestRetention(TimelineTestBase):

    def _backdate(self, obj, field, days):
        """Move a timestamp column backward by N days."""
        setattr(obj, field, datetime.utcnow() - timedelta(days=days))
        self.db.session.commit()

    def test_old_checkpoints_pruned(self):
        from lms_automation.models import RunCheckpoint
        from lms_automation.services.run_timeline import create_checkpoint, prune_old_data

        cp = create_checkpoint(run=None, label='Old cp', organiser_id=self.org_a.id)
        self.db.session.commit()
        cp_id = cp.checkpoint_id
        # Back-date by 35 days (beyond default 30-day retention)
        self._backdate(cp, 'created_at', 35)

        deleted = prune_old_data(checkpoint_days=30)
        self.db.session.expire_all()
        self.assertGreater(deleted.get('checkpoints', 0), 0)
        self.assertIsNone(RunCheckpoint.query.filter_by(checkpoint_id=cp_id).first())

    def test_recent_checkpoints_not_pruned(self):
        from lms_automation.models import RunCheckpoint
        from lms_automation.services.run_timeline import create_checkpoint, prune_old_data

        cp = create_checkpoint(run=None, label='Recent cp', organiser_id=self.org_a.id)
        self.db.session.commit()
        # Only 5 days old — within retention window

        deleted = prune_old_data(checkpoint_days=30)
        self.assertIsNotNone(RunCheckpoint.query.filter_by(checkpoint_id=cp.checkpoint_id).first())

    def test_audit_events_not_pruned_by_default(self):
        from lms_automation.models import RunAuditEvent
        from lms_automation.services.run_timeline import append_audit_event, prune_old_data

        evt = append_audit_event(
            event_type='test_event', actor='system', organiser_id=self.org_a.id
        )
        self.db.session.commit()
        self._backdate(evt, 'created_at', 100)

        # audit_days=0 means never prune
        deleted = prune_old_data(checkpoint_days=0, audit_days=0, run_days=0)
        self.assertEqual(deleted.get('audit_events', 0), 0)
        self.assertIsNotNone(RunAuditEvent.query.get(evt.id))

    def test_old_runs_pruned_when_configured(self):
        from lms_automation.models import AutomationRun
        from lms_automation.services.run_timeline import create_run, complete_run, prune_old_data

        run = create_run(organiser_id=self.org_a.id)
        complete_run(run, status='success')
        self.db.session.commit()
        run_id_val = run.id
        self._backdate(run, 'completed_at', 35)

        deleted = prune_old_data(run_days=30)
        self.db.session.expire_all()
        self.assertGreater(deleted.get('runs', 0), 0)
        self.assertIsNone(AutomationRun.query.filter_by(id=run_id_val).first())

    def test_failed_runs_never_pruned(self):
        """Runs with 'failed' status are excluded from retention deletion."""
        from lms_automation.models import AutomationRun
        from lms_automation.services.run_timeline import create_run, complete_run, prune_old_data

        run = create_run(organiser_id=self.org_a.id)
        complete_run(run, status='failed')
        self.db.session.commit()
        self._backdate(run, 'completed_at', 100)

        prune_old_data(run_days=30)
        self.assertIsNotNone(AutomationRun.query.get(run.id))


# ---------------------------------------------------------------------------
# 8. Super-admin scope hardening
# ---------------------------------------------------------------------------

class TestSuperAdminScope(TimelineTestBase):

    def _create_checkpoint(self, organiser_id):
        from lms_automation.services.run_timeline import create_checkpoint
        cp = create_checkpoint(run=None, label='Test', organiser_id=organiser_id)
        self.db.session.commit()
        return cp

    def test_super_admin_without_scope_blocked_on_rollback_last_action(self):
        """Super-admin with no session organiser_id and no body organiser_id → 403."""
        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=None)
            resp = self._post(client, '/api/admin/timeline/rollback/last-action')
            self.assertEqual(resp.status_code, 403)
            self.assertFalse(resp.get_json()['success'])

    def test_super_admin_without_scope_blocked_on_rollback_last_run(self):
        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=None)
            resp = self._post(client, '/api/admin/timeline/rollback/last-run')
            self.assertEqual(resp.status_code, 403)

    def test_super_admin_with_body_scope_allowed_on_rollback(self):
        """Super-admin supplying organiser_id in body is allowed past the scope check."""
        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=None)
            # No completed runs exist — the service layer returns 400, not 403
            resp = self._post(
                client,
                '/api/admin/timeline/rollback/last-action',
                json_body={'organiser_id': self.org_a.id},
            )
            # 403 would be a scope failure; anything else means scope check passed
            self.assertNotEqual(resp.status_code, 403)

    def test_super_admin_with_session_scope_allowed(self):
        """Super-admin with session organiser_id doesn't need body scope."""
        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=self.org_a.id)
            resp = self._post(client, '/api/admin/timeline/rollback/last-action')
            self.assertNotEqual(resp.status_code, 403)

    def test_organiser_admin_cannot_rollback_other_organiser_checkpoint(self):
        """Org-A admin cannot roll back a checkpoint belonging to Org-B."""
        cp_b = self._create_checkpoint(self.org_b.id)

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            resp = self._post(
                client,
                f'/api/admin/timeline/rollback/checkpoint/{cp_b.checkpoint_id}',
            )
            self.assertEqual(resp.status_code, 403)
            data = resp.get_json()
            self.assertFalse(data['success'])

    def test_organiser_admin_can_rollback_own_checkpoint(self):
        """Org-A admin can roll back their own checkpoint (service returns success/400 not 403)."""
        cp_a = self._create_checkpoint(self.org_a.id)

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            resp = self._post(
                client,
                f'/api/admin/timeline/rollback/checkpoint/{cp_a.checkpoint_id}',
            )
            # 403 = access denied; 200/400 = scope passed (400 means nothing to roll back)
            self.assertNotEqual(resp.status_code, 403)

    def test_super_admin_without_scope_blocked_on_create_checkpoint(self):
        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=None)
            resp = self._post(
                client,
                '/api/admin/timeline/checkpoints',
                json_body={'label': 'Manual checkpoint'},
            )
            self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_request_redirected(self):
        with self.app.test_client() as client:
            resp = client.get('/api/admin/timeline/runs')
            self.assertIn(resp.status_code, (302, 401))


# ---------------------------------------------------------------------------
# 9. Read endpoints: super-admin without scope sees all records
# ---------------------------------------------------------------------------

class TestReadEndpointScope(TimelineTestBase):

    def test_super_admin_no_scope_sees_all_runs(self):
        from lms_automation.services.run_timeline import create_run, complete_run

        run_a = create_run(organiser_id=self.org_a.id)
        complete_run(run_a, status='success')
        run_b = create_run(organiser_id=self.org_b.id)
        complete_run(run_b, status='success')
        self.db.session.commit()

        with self.app.test_client() as client:
            self._super_admin_session(client, organiser_id=None)
            resp = client.get('/api/admin/timeline/runs')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            run_ids = [r['run_id'] for r in data['runs']]
            self.assertIn(run_a.run_id, run_ids)
            self.assertIn(run_b.run_id, run_ids)

    def test_scoped_admin_sees_only_own_runs(self):
        from lms_automation.services.run_timeline import create_run, complete_run

        run_a = create_run(organiser_id=self.org_a.id)
        complete_run(run_a, status='success')
        run_b = create_run(organiser_id=self.org_b.id)
        complete_run(run_b, status='success')
        self.db.session.commit()

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            resp = client.get('/api/admin/timeline/runs')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            run_ids = [r['run_id'] for r in data['runs']]
            self.assertIn(run_a.run_id, run_ids)
            self.assertNotIn(run_b.run_id, run_ids)

    def test_get_run_detail_cross_org_blocked(self):
        """Org-A admin cannot read a run belonging to Org-B."""
        from lms_automation.services.run_timeline import create_run, complete_run

        run_b = create_run(organiser_id=self.org_b.id)
        complete_run(run_b, status='success')
        self.db.session.commit()

        with self.app.test_client() as client:
            self._admin_session(client, self.org_a.id)
            resp = client.get(f'/api/admin/timeline/runs/{run_b.run_id}')
            self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
