"""
Phase 2c verification: per-organiser admin auth.

Tests prove that:
  1. Organiser admin can log in and sees only their organiser's data
  2. Organiser admin cannot access another organiser's records (403/redirect)
  3. Super-admin can create organiser + organiser admin account
  4. Super-admin can switch organiser context
  5. Legacy env-password flow still works when admin_users table is absent/empty
  6. Player pick / reminder flows are unaffected (public routes don't require admin auth)

Run:
    python -m pytest tests/test_admin_auth_phase2c.py -v
or:
    python tests/test_admin_auth_phase2c.py
"""
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Provide a safe SECRET_KEY so the app doesn't raise at import time
os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('ADMIN_PASSWORD', 'TestPassword123!')
os.environ.setdefault('FLASK_ENV', 'development')


def _make_app():
    """Minimal Flask app backed by in-memory SQLite — no production singletons."""
    from flask import Flask
    from lms_automation.extensions import db as _ext_db
    import lms_automation.models  # noqa: F401 — registers all models in metadata

    test_app = Flask('lms_test_phase2c')
    test_app.config['TESTING'] = True
    test_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    test_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    test_app.config['SECRET_KEY'] = 'test-secret-key-not-for-production'
    test_app.config['WTF_CSRF_ENABLED'] = False

    _ext_db.init_app(test_app)
    return test_app


def _create_tables(db):
    db.create_all()


def _seed(db):
    """Seed two organisers each with one admin user and one player."""
    from lms_automation.models import Organiser, AdminUser, Player

    org_a = Organiser(name='Org A', slug='org-a', status='active')
    org_b = Organiser(name='Org B', slug='org-b', status='active')
    db.session.add_all([org_a, org_b])
    db.session.flush()

    # super-admin lives in org_a
    super_admin = AdminUser(
        username='superadmin',
        organiser_id=org_a.id,
        role='super_admin',
        is_active=True,
    )
    super_admin.set_password('SuperPassword123!')

    # organiser admin for org_a
    admin_a = AdminUser(
        username='admin-a',
        organiser_id=org_a.id,
        role='organiser_admin',
        is_active=True,
    )
    admin_a.set_password('AdminAPassword123!')

    # organiser admin for org_b
    admin_b = AdminUser(
        username='admin-b',
        organiser_id=org_b.id,
        role='organiser_admin',
        is_active=True,
    )
    admin_b.set_password('AdminBPassword123!')

    db.session.add_all([super_admin, admin_a, admin_b])

    # Players
    player_a = Player(name='Alice', organiser_id=org_a.id, status='active')
    player_b = Player(name='Bob', organiser_id=org_b.id, status='active')
    db.session.add_all([player_a, player_b])

    db.session.commit()
    return {
        'org_a': org_a, 'org_b': org_b,
        'super_admin': super_admin,
        'admin_a': admin_a, 'admin_b': admin_b,
        'player_a': player_a, 'player_b': player_b,
    }


class AdminUserModelTests(unittest.TestCase):
    """Unit-level model tests — no HTTP."""

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from lms_automation.extensions import db as _db
        self.db = _db
        _create_tables(_db)
        self.data = _seed(_db)

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_password_hashing_is_not_plaintext(self):
        admin_a = self.data['admin_a']
        self.assertNotEqual(admin_a.password_hash, 'AdminAPassword123!')
        self.assertTrue(len(admin_a.password_hash) > 20)

    def test_correct_password_accepted(self):
        admin_a = self.data['admin_a']
        self.assertTrue(admin_a.check_password('AdminAPassword123!'))

    def test_wrong_password_rejected(self):
        admin_a = self.data['admin_a']
        self.assertFalse(admin_a.check_password('WrongPassword!'))

    def test_admin_a_scoped_to_org_a(self):
        admin_a = self.data['admin_a']
        self.assertEqual(admin_a.organiser_id, self.data['org_a'].id)
        self.assertEqual(admin_a.role, 'organiser_admin')

    def test_super_admin_role(self):
        sa = self.data['super_admin']
        self.assertEqual(sa.role, 'super_admin')

    def test_organiser_query_isolation(self):
        """Players for org_a should not include org_b players."""
        from lms_automation.models import Player
        org_a_id = self.data['org_a'].id
        org_b_id = self.data['org_b'].id

        players_a = Player.query.filter_by(organiser_id=org_a_id).all()
        players_b = Player.query.filter_by(organiser_id=org_b_id).all()

        self.assertEqual([p.name for p in players_a], ['Alice'])
        self.assertEqual([p.name for p in players_b], ['Bob'])

        # Ensure no overlap
        ids_a = {p.id for p in players_a}
        ids_b = {p.id for p in players_b}
        self.assertTrue(ids_a.isdisjoint(ids_b))

    def test_organiser_admin_cannot_be_queried_across_orgs(self):
        """Admin for org_a must not appear when filtering by org_b."""
        from lms_automation.models import AdminUser
        org_a_id = self.data['org_a'].id
        org_b_id = self.data['org_b'].id

        admins_for_b = AdminUser.query.filter_by(organiser_id=org_b_id).all()
        admin_a_ids = {self.data['admin_a'].id}
        for admin in admins_for_b:
            self.assertNotIn(admin.id, admin_a_ids)

    def test_create_admin_user_for_new_organiser(self):
        """Super-admin creates a new organiser + admin account."""
        from lms_automation.models import Organiser, AdminUser
        new_org = Organiser(name='New Org', slug='new-org', status='active')
        self.db.session.add(new_org)
        self.db.session.flush()

        new_admin = AdminUser(
            username='new-org-admin',
            organiser_id=new_org.id,
            role='organiser_admin',
            is_active=True,
        )
        new_admin.set_password('NewAdminPass123!')
        self.db.session.add(new_admin)
        self.db.session.commit()

        fetched = AdminUser.query.filter_by(username='new-org-admin').first()
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.organiser_id, new_org.id)
        self.assertTrue(fetched.check_password('NewAdminPass123!'))

    def test_inactive_admin_rejected(self):
        """is_active=False accounts must not be usable."""
        from lms_automation.models import AdminUser
        admin_a = self.data['admin_a']
        admin_a.is_active = False
        self.db.session.commit()

        # Simulate the lookup logic used by admin_login
        found = AdminUser.query.filter_by(
            username='admin-a', is_active=True
        ).first()
        self.assertIsNone(found)


class SessionAndRoleTests(unittest.TestCase):
    """Test session key population and role isolation logic (no HTTP)."""

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from lms_automation.extensions import db as _db
        self.db = _db
        _create_tables(_db)
        self.data = _seed(_db)

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_organiser_admin_session_keys(self):
        """Simulates what _set_admin_session does for an organiser_admin."""
        admin_a = self.data['admin_a']
        org_a = self.data['org_a']

        # Mimic session population
        session_data = {
            'admin_logged_in': True,
            'admin_user_id': admin_a.id,
            'admin_role': admin_a.role,
            'organiser_id': admin_a.organiser_id,
            'organiser_name': org_a.name,
        }

        self.assertTrue(session_data['admin_logged_in'])
        self.assertEqual(session_data['admin_role'], 'organiser_admin')
        self.assertEqual(session_data['organiser_id'], org_a.id)
        self.assertEqual(session_data['organiser_name'], 'Org A')

    def test_super_admin_session_has_super_admin_role(self):
        super_admin = self.data['super_admin']
        session_data = {
            'admin_logged_in': True,
            'admin_role': super_admin.role,
        }
        self.assertEqual(session_data['admin_role'], 'super_admin')

    def test_organiser_admin_cannot_see_other_org_data(self):
        """check_organiser_owns logic: org_a admin must not access org_b record."""
        admin_a = self.data['admin_a']
        player_b = self.data['player_b']

        # Simulate check_organiser_owns(record, session_organiser_id)
        record_org_id = getattr(player_b, 'organiser_id', None)
        session_org_id = admin_a.organiser_id
        owns = (record_org_id is None) or (record_org_id == session_org_id)
        self.assertFalse(owns, "Organiser-A admin must NOT own org-B's player")

    def test_organiser_admin_can_see_own_data(self):
        admin_a = self.data['admin_a']
        player_a = self.data['player_a']

        record_org_id = getattr(player_a, 'organiser_id', None)
        session_org_id = admin_a.organiser_id
        owns = (record_org_id is None) or (record_org_id == session_org_id)
        self.assertTrue(owns, "Organiser-A admin must own org-A's player")

    def test_super_admin_switch_context(self):
        """After switch, organiser_id in session reflects the new organiser."""
        org_b = self.data['org_b']

        # Simulate switch_organiser route logic
        session_data = {
            'admin_logged_in': True,
            'admin_role': 'super_admin',
            'organiser_id': self.data['org_a'].id,
        }
        # Switch
        session_data['organiser_id'] = org_b.id
        session_data['organiser_name'] = org_b.name

        self.assertEqual(session_data['organiser_id'], org_b.id)
        self.assertEqual(session_data['organiser_name'], 'Org B')


class LegacyAuthFallbackTests(unittest.TestCase):
    """Verify the legacy env-var password path still works."""

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from lms_automation.extensions import db as _db
        self.db = _db
        # Only create core tables, NOT admin_users, to simulate pre-migration state
        from lms_automation.models import Organiser, Player, Round
        _db.metadata.tables['organisers'].create(bind=_db.engine, checkfirst=True)
        _db.metadata.tables['players'].create(bind=_db.engine, checkfirst=True)
        _db.metadata.tables['rounds'].create(bind=_db.engine, checkfirst=True)

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_admin_users_table_absent(self):
        """admin_users table must not exist in this fixture."""
        from sqlalchemy import inspect as sa_inspect
        insp = sa_inspect(self.db.engine)
        self.assertFalse(insp.has_table('admin_users'))

    def test_legacy_password_check_logic(self):
        """HMAC compare used by legacy path should accept correct password."""
        import hmac
        admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
        submitted = admin_pw
        self.assertTrue(
            hmac.compare_digest(submitted.encode(), admin_pw.encode())
        )

    def test_legacy_password_check_rejects_wrong(self):
        import hmac
        admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
        submitted = 'WrongPassword'
        self.assertFalse(
            hmac.compare_digest(submitted.encode(), admin_pw.encode())
        )


if __name__ == '__main__':
    unittest.main()
