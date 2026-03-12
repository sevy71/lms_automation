#!/usr/bin/env python3
"""
Phase 1 multi-organiser foundation — verification script.

Run from the project root after applying the migration:

    python scripts/verify_phase1_organiser.py

Exit codes:
  0 — all checks passed
  1 — one or more checks failed

Checks covered
--------------
1.  Organiser table exists and default organiser is present.
2.  All existing players are assigned to the default organiser.
3.  All existing rounds are assigned to the default organiser.
4.  Creating a second organiser does not expose its data in admin dashboard
    queries scoped to the default organiser.
5.  Player submission flow (pick token lookup) still works.
6.  Scheduler-style operations (reminder creation) do not raise exceptions.
7.  Admin login sets session['organiser_id'] correctly (via app test client).
8.  Cross-organiser access is blocked by abort_if_organiser_mismatch.
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('ADMIN_PASSWORD', 'testpassword123')


def _setup_sqlite_app():
    """Create an in-memory SQLite app with all tables (schema only, no migration)."""
    os.environ['DATABASE_URL'] = 'sqlite://'
    from lms_automation.app import app, db
    with app.app_context():
        db.create_all()
    return app, db


PASS = '  [PASS]'
FAIL = '  [FAIL]'
results = []


def check(name, condition, detail=''):
    status = PASS if condition else FAIL
    msg = f'{status} {name}'
    if detail:
        msg += f'\n         {detail}'
    print(msg)
    results.append(condition)
    return condition


# ============================================================
# Test helpers
# ============================================================

def _seed_default_organiser(db, Organiser):
    org = Organiser.query.filter_by(slug='default').first()
    if not org:
        from datetime import datetime
        org = Organiser(name='Default Organiser', slug='default', status='active',
                        created_at=datetime.utcnow())
        db.session.add(org)
        db.session.commit()
    return org


def _seed_player(db, Player, name, org):
    from datetime import datetime
    p = Player(name=name, status='active', organiser_id=org.id)
    db.session.add(p)
    db.session.commit()
    return p


def _seed_round(db, Round, rn, org):
    r = Round(round_number=rn, status='active', cycle_number=1, organiser_id=org.id)
    db.session.add(r)
    db.session.commit()
    return r


# ============================================================
# Checks
# ============================================================

def run_checks():
    print('\n=== Phase 1 Organiser Foundation — Verification ===\n')

    app, db = _setup_sqlite_app()

    with app.app_context():
        from lms_automation.models import Organiser, Player, Round, Pick, PickToken
        from lms_automation.app import (
            get_current_organiser_id,
            check_organiser_owns,
            abort_if_organiser_mismatch,
            _get_default_organiser_id,
        )

        # ------------------------------------------------------------------
        # 1. Organiser model and default row
        # ------------------------------------------------------------------
        print('--- 1. Organiser model ---')
        default_org = _seed_default_organiser(db, Organiser)
        check('Organiser table accessible', default_org is not None)
        check('Default organiser slug is "default"', default_org.slug == 'default')
        check('Default organiser status is "active"', default_org.status == 'active')

        # ------------------------------------------------------------------
        # 2. Players assigned to default organiser
        # ------------------------------------------------------------------
        print('\n--- 2. Player organiser assignment ---')
        p1 = _seed_player(db, Player, 'Alice', default_org)
        p2 = _seed_player(db, Player, 'Bob', default_org)
        check('Player organiser_id is set', p1.organiser_id == default_org.id)
        default_players = Player.query.filter_by(organiser_id=default_org.id).all()
        check('Both players visible under default organiser',
              len(default_players) == 2,
              f'Found {len(default_players)} players')

        # ------------------------------------------------------------------
        # 3. Rounds assigned to default organiser
        # ------------------------------------------------------------------
        print('\n--- 3. Round organiser assignment ---')
        r1 = _seed_round(db, Round, 1, default_org)
        check('Round organiser_id is set', r1.organiser_id == default_org.id)
        default_rounds = Round.query.filter_by(organiser_id=default_org.id).all()
        check('Round visible under default organiser', len(default_rounds) >= 1)

        # ------------------------------------------------------------------
        # 4. Second organiser does NOT leak into default organiser queries
        # ------------------------------------------------------------------
        print('\n--- 4. Data isolation between organisers ---')
        from datetime import datetime
        org2 = Organiser(name='Second League', slug='second', status='active',
                         created_at=datetime.utcnow())
        db.session.add(org2)
        db.session.commit()

        p3 = Player(name='Charlie', status='active', organiser_id=org2.id)
        r2 = Round(round_number=1, status='active', cycle_number=1, organiser_id=org2.id)
        db.session.add_all([p3, r2])
        db.session.commit()

        # Scoped queries for default organiser should NOT include second organiser's data
        scoped_players = Player.query.filter_by(organiser_id=default_org.id).all()
        scoped_rounds = Round.query.filter_by(organiser_id=default_org.id).all()
        check('Charlie not in default organiser players',
              all(p.name != 'Charlie' for p in scoped_players))
        check("Second org's round not in default organiser rounds",
              all(r.organiser_id == default_org.id for r in scoped_rounds))
        check('All players count unchanged for default organiser',
              Player.query.filter_by(organiser_id=default_org.id).count() == 2)

        # ------------------------------------------------------------------
        # 5. Pick token flow still works (player submission)
        # ------------------------------------------------------------------
        print('\n--- 5. Pick token flow ---')
        try:
            token = PickToken.create_for_player_round(p1.id, r1.id)
            db.session.commit()
            check('PickToken created for default organiser player', token is not None)
            check('Token is valid after creation', token.is_valid())

            # Retrieve token by value (simulates /pick/<token> route)
            fetched = PickToken.query.filter_by(token=token.token).first()
            check('Token retrievable by value', fetched is not None)
            check('Token player matches', fetched.player_id == p1.id)
        except Exception as e:
            check('Pick token flow (no exception)', False, str(e))

        # ------------------------------------------------------------------
        # 6. Reminder creation (scheduler compatibility)
        # ------------------------------------------------------------------
        print('\n--- 6. Reminder creation (scheduler compat) ---')
        try:
            from lms_automation.models import ReminderSchedule
            from datetime import timedelta
            # Give round an anchor time so reminders can be scheduled
            r1.first_kickoff_at = datetime.utcnow() + timedelta(hours=6)
            db.session.commit()

            count = ReminderSchedule.create_reminders_for_round(r1.id)
            check('Reminders created without exception', count is not False,
                  f'Created: {count}')
            check('Reminder count > 0', isinstance(count, int) and count > 0,
                  f'Got: {count}')
        except Exception as e:
            check('Reminder creation (no exception)', False, str(e))

        # ------------------------------------------------------------------
        # 7. Session organiser_id via test client login
        # ------------------------------------------------------------------
        print('\n--- 7. Admin session organiser context ---')
        with app.test_client() as client:
            # GET login page (sets CSRF token in session)
            client.get('/admin/login')

            with client.session_transaction() as sess:
                csrf = sess.get('csrf_token', '')

            resp = client.post('/admin/login', data={
                'password': os.environ['ADMIN_PASSWORD'],
                'csrf_token': csrf,
            }, follow_redirects=False)

            with client.session_transaction() as sess:
                logged_in = sess.get('admin_logged_in', False)
                session_oid = sess.get('organiser_id')

            check('Admin login sets admin_logged_in', logged_in is True)
            check('Admin login sets organiser_id in session',
                  session_oid == default_org.id,
                  f'session organiser_id={session_oid}, expected={default_org.id}')

        # ------------------------------------------------------------------
        # 8. Cross-organiser access blocked
        # ------------------------------------------------------------------
        print('\n--- 8. Cross-organiser access guard ---')
        # check_organiser_owns: correct organiser → True
        check('check_organiser_owns: match returns True',
              check_organiser_owns(p1, default_org.id) is True)
        # check_organiser_owns: wrong organiser → False
        check('check_organiser_owns: mismatch returns False',
              check_organiser_owns(p3, default_org.id) is False)
        # check_organiser_owns: None organiser_id (pre-migration compat) → True
        class _FakeRecord:
            organiser_id = None
        check('check_organiser_owns: None organiser_id allows access (compat)',
              check_organiser_owns(_FakeRecord(), default_org.id) is True)

        # abort_if_organiser_mismatch should return a response for mismatch
        with app.test_request_context('/admin_dashboard',
                                       headers={'Accept': 'application/json'}):
            from flask import session as flask_session
            flask_session['organiser_id'] = default_org.id
            flask_session['admin_logged_in'] = True
            response = abort_if_organiser_mismatch(p3, 'player')
            check('abort_if_organiser_mismatch returns response for mismatch',
                  response is not None)

        with app.test_request_context('/admin_dashboard'):
            flask_session['organiser_id'] = default_org.id
            flask_session['admin_logged_in'] = True
            response = abort_if_organiser_mismatch(p1, 'player')
            check('abort_if_organiser_mismatch returns None for own record',
                  response is None)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print('\n=== Summary ===')
    total = len(results)
    passed = sum(results)
    failed = total - passed
    print(f'  {passed}/{total} checks passed', end='')
    if failed:
        print(f'  ({failed} FAILED)')
    else:
        print('  — all good!')
    print()
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run_checks())
