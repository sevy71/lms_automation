"""
Organiser management — QR, edit, archive/unarchive, and guarded delete.

Covers (Part E requirements):
  1. Super-admin can view / download QR; organiser-admin cannot.
  2. QR download URL resolves to correct organiser registration link (slug in URL).
  3. Edit organiser updates name and slug safely; rejects duplicate slug.
  4. Archive / unarchive works and status is reflected.
  5. Delete blocked for default organiser.
  6. Delete blocked when organiser has data (players or rounds).
  7. Delete succeeds when organiser is empty and slug is confirmed.
  8. Organiser-admin cannot access super-admin management routes.

Run:
    python -m pytest tests/test_organiser_management.py -v
or:
    python tests/test_organiser_management.py
"""
import os
import secrets
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Must set DATABASE_URL *before* importing the app module because the URI is
# resolved at module level.
os.environ['SECRET_KEY'] = 'test-secret-not-for-prod'
os.environ['ADMIN_PASSWORD'] = 'TestPassword123!'
os.environ['FLASK_ENV'] = 'testing'

_DB_FILE = tempfile.mktemp(suffix='.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_DB_FILE}'

from lms_automation.app import app as _lms_app   # noqa: E402
from lms_automation.extensions import db as _db  # noqa: E402

# Module-level fixture IDs populated in setUpModule
_ORG_IDS: dict = {}


# ---------------------------------------------------------------------------
# Module-level setup / teardown
# ---------------------------------------------------------------------------

def setUpModule():
    """Create tables and seed test data once for the whole module."""
    _lms_app.config['TESTING'] = True
    _lms_app.config['RATELIMIT_ENABLED'] = False  # belt-and-suspenders

    with _lms_app.app_context():
        _db.create_all()
        from lms_automation.models import Organiser, AdminUser, Player, Round

        default_org = Organiser(name='Default', slug='default', status='active')
        org_a = Organiser(name='Org Alpha', slug='org-alpha', status='active')
        org_empty = Organiser(name='Empty Org', slug='empty-org', status='active')
        _db.session.add_all([default_org, org_a, org_empty])
        _db.session.flush()

        super_admin = AdminUser(
            username='superadmin',
            organiser_id=default_org.id,
            role='super_admin',
            is_active=True,
        )
        super_admin.set_password('SuperSecret123!')

        org_admin = AdminUser(
            username='orgadmin',
            organiser_id=org_a.id,
            role='organiser_admin',
            is_active=True,
        )
        org_admin.set_password('OrgAdminPass123!')

        _db.session.add_all([super_admin, org_admin])

        player = Player(name='Test Player', organiser_id=org_a.id, status='active')
        rnd = Round(round_number=1, organiser_id=org_a.id, status='pending')
        _db.session.add_all([player, rnd])

        _db.session.commit()

        _ORG_IDS['default_org_id'] = default_org.id
        _ORG_IDS['org_a_id'] = org_a.id
        _ORG_IDS['org_empty_id'] = org_empty.id
        _ORG_IDS['super_admin_id'] = super_admin.id
        _ORG_IDS['org_admin_id'] = org_admin.id


def tearDownModule():
    """Remove temp DB file."""
    try:
        os.unlink(_DB_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    return _lms_app.test_client()


def _set_session(client, role='super_admin', organiser_id=None, user_id=None):
    """Directly inject an authenticated admin session into the test client.

    Bypasses the login form (and its rate limit) entirely.
    """
    if organiser_id is None:
        organiser_id = _ORG_IDS.get('default_org_id', 1)
    if user_id is None:
        user_id = _ORG_IDS.get('super_admin_id', 1)
    token = secrets.token_hex(32)
    with client.session_transaction() as sess:
        sess['admin_logged_in'] = True
        sess['admin_role'] = role
        sess['admin_user_id'] = user_id
        sess['admin_username'] = 'superadmin' if role == 'super_admin' else 'orgadmin'
        sess['organiser_id'] = organiser_id
        sess['organiser_name'] = 'Test Org'
        sess['csrf_token'] = token  # matches lms_automation/services/csrf.py


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get('csrf_token', '')


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestQRAccess(unittest.TestCase):
    """Part A: QR generation access control."""

    def setUp(self):
        self.client = _make_client()

    # --- Super-admin can view QR page ---
    def test_super_admin_can_view_qr(self):
        _set_session(self.client)
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/qr',
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Registration QR', resp.data)
        self.assertIn(b'org-alpha', resp.data)

    # --- Super-admin can download QR PNG ---
    def test_super_admin_can_download_qr_png(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest('qrcode library not installed')
        _set_session(self.client)
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/qr/download',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, 'image/png')
        cd = resp.headers.get('Content-Disposition', '')
        self.assertIn('register-org-alpha.png', cd)

    # --- QR response is a valid PNG ---
    def test_qr_png_has_png_magic_bytes(self):
        try:
            import qrcode  # noqa: F401
        except ImportError:
            self.skipTest('qrcode library not installed')
        _set_session(self.client)
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/qr/download',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data[:4] == b'\x89PNG', 'Response is not a PNG file')

    # --- Organiser-admin cannot access QR routes ---
    def test_organiser_admin_cannot_view_qr(self):
        _set_session(self.client, role='organiser_admin',
                     organiser_id=_ORG_IDS['org_a_id'],
                     user_id=_ORG_IDS['org_admin_id'])
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/qr',
            follow_redirects=True,
        )
        self.assertNotIn(b'Registration QR', resp.data)

    def test_organiser_admin_cannot_download_qr(self):
        _set_session(self.client, role='organiser_admin',
                     organiser_id=_ORG_IDS['org_a_id'],
                     user_id=_ORG_IDS['org_admin_id'])
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/qr/download',
            follow_redirects=True,
        )
        self.assertNotEqual(resp.content_type, 'image/png')


class TestEditOrganiser(unittest.TestCase):
    """Part B: Edit organiser details."""

    def setUp(self):
        self.client = _make_client()
        _set_session(self.client)
        # Reset org_empty to a known state before each test
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_empty_id'])
            if org:
                org.name = 'Empty Org'
                org.slug = 'empty-org'
                org.status = 'active'
                _db.session.commit()

    def _post_edit(self, org_id, name, slug, status):
        return self.client.post(
            f'/admin/organisers/{org_id}/edit',
            data={'name': name, 'slug': slug, 'status': status,
                  'csrf_token': _csrf(self.client)},
            follow_redirects=True,
        )

    def test_edit_name_and_slug(self):
        resp = self._post_edit(
            _ORG_IDS['org_empty_id'], 'Empty Renamed', 'empty-renamed', 'active',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'updated successfully', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_empty_id'])
            self.assertEqual(org.name, 'Empty Renamed')
            self.assertEqual(org.slug, 'empty-renamed')

    def test_duplicate_slug_rejected(self):
        resp = self._post_edit(
            _ORG_IDS['org_empty_id'], 'Empty Org', 'org-alpha', 'active',
        )
        self.assertIn(b'already in use', resp.data)

    def test_invalid_slug_rejected(self):
        resp = self._post_edit(
            _ORG_IDS['org_empty_id'], 'Empty Org', 'INVALID SLUG!!', 'active',
        )
        self.assertIn(b'Slug may only contain', resp.data)

    def test_cannot_rename_default_slug(self):
        resp = self._post_edit(
            _ORG_IDS['default_org_id'], 'Default Renamed', 'new-default', 'active',
        )
        self.assertIn(b'Cannot rename', resp.data)

    def test_organiser_admin_cannot_edit(self):
        client2 = _make_client()
        _set_session(client2, role='organiser_admin',
                     organiser_id=_ORG_IDS['org_a_id'],
                     user_id=_ORG_IDS['org_admin_id'])
        with client2.session_transaction() as sess:
            tok = sess.get('_csrf_token', '')
        resp = client2.post(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/edit',
            data={'name': 'Hack', 'slug': 'hack', 'status': 'active',
                  'csrf_token': tok},
            follow_redirects=True,
        )
        self.assertNotIn(b'updated successfully', resp.data)


class TestArchiveOrganiser(unittest.TestCase):
    """Part C: Archive / unarchive."""

    def setUp(self):
        self.client = _make_client()
        _set_session(self.client)
        # Ensure org_a starts active before each test
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_a_id'])
            org.status = 'active'
            _db.session.commit()

    def _post_archive(self, org_id):
        return self.client.post(
            f'/admin/organisers/{org_id}/archive',
            data={'csrf_token': _csrf(self.client)},
            follow_redirects=True,
        )

    def test_archive_active_org(self):
        resp = self._post_archive(_ORG_IDS['org_a_id'])
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'archived', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_a_id'])
            self.assertEqual(org.status, 'archived')

    def test_unarchive_archived_org(self):
        self._post_archive(_ORG_IDS['org_a_id'])  # archive first
        resp = self._post_archive(_ORG_IDS['org_a_id'])  # now unarchive
        self.assertIn(b'active', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_a_id'])
            self.assertEqual(org.status, 'active')

    def test_cannot_archive_default_org(self):
        resp = self._post_archive(_ORG_IDS['default_org_id'])
        self.assertIn(b'Cannot archive', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['default_org_id'])
            self.assertEqual(org.status, 'active')


class TestDeleteOrganiser(unittest.TestCase):
    """Part D: Guarded delete."""

    def setUp(self):
        self.client = _make_client()
        _set_session(self.client)
        # Recreate empty-org if it was deleted by a prior test
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            if not _db.session.get(Organiser, _ORG_IDS['org_empty_id']):
                org = Organiser(name='Empty Org', slug='empty-org', status='active')
                _db.session.add(org)
                _db.session.commit()
                _ORG_IDS['org_empty_id'] = org.id

    def _post_delete(self, org_id, confirm_slug):
        return self.client.post(
            f'/admin/organisers/{org_id}/delete',
            data={'confirm_slug': confirm_slug, 'csrf_token': _csrf(self.client)},
            follow_redirects=True,
        )

    def test_delete_blocked_for_default_organiser(self):
        resp = self._post_delete(_ORG_IDS['default_org_id'], 'default')
        self.assertIn(b'Cannot delete the default', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            self.assertIsNotNone(_db.session.get(Organiser, _ORG_IDS['default_org_id']))

    def test_delete_blocked_when_organiser_has_data(self):
        resp = self._post_delete(_ORG_IDS['org_a_id'], 'org-alpha')
        self.assertIn(b'Cannot delete', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            self.assertIsNotNone(_db.session.get(Organiser, _ORG_IDS['org_a_id']))

    def test_delete_blocked_on_wrong_slug(self):
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            if not _db.session.get(Organiser, _ORG_IDS['org_empty_id']):
                self.skipTest('empty-org not present')
        resp = self._post_delete(_ORG_IDS['org_empty_id'], 'wrong-slug')
        self.assertIn(b'Confirmation slug did not match', resp.data)

    def test_delete_succeeds_for_empty_organiser_with_correct_slug(self):
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            org = _db.session.get(Organiser, _ORG_IDS['org_empty_id'])
            if not org:
                self.skipTest('empty-org not present (already deleted)')
            slug = org.slug

        resp = self._post_delete(_ORG_IDS['org_empty_id'], slug)
        self.assertIn(b'deleted successfully', resp.data)
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            self.assertIsNone(_db.session.get(Organiser, _ORG_IDS['org_empty_id']))

    def test_organiser_admin_cannot_delete(self):
        client2 = _make_client()
        _set_session(client2, role='organiser_admin',
                     organiser_id=_ORG_IDS['org_a_id'],
                     user_id=_ORG_IDS['org_admin_id'])
        tok = _csrf(client2)
        resp = client2.post(
            f'/admin/organisers/{_ORG_IDS["org_a_id"]}/delete',
            data={'confirm_slug': 'org-alpha', 'csrf_token': tok},
            follow_redirects=True,
        )
        # super_admin_required redirects org-admin to dashboard (200 after follow)
        self.assertEqual(resp.status_code, 200)
        # The critical check: org_a must still exist
        with _lms_app.app_context():
            from lms_automation.models import Organiser
            self.assertIsNotNone(
                _db.session.get(Organiser, _ORG_IDS['org_a_id']),
                'org_a was deleted but should have been blocked',
            )


class TestRegistrationOrganiserContext(unittest.TestCase):
    """Registration page passes organiser context via ?organiser=<slug>."""

    def setUp(self):
        self.client = _make_client()

    def test_register_page_without_organiser(self):
        resp = self.client.get('/register')
        self.assertEqual(resp.status_code, 200)

    def test_register_page_with_valid_organiser_slug(self):
        resp = self.client.get('/register?organiser=org-alpha')
        self.assertEqual(resp.status_code, 200)

    def test_register_page_with_unknown_organiser_slug(self):
        resp = self.client.get('/register?organiser=nonexistent')
        self.assertEqual(resp.status_code, 200)


class TestSuperAdminOnlyRoutes(unittest.TestCase):
    """Organiser-admin cannot access super-admin management routes."""

    def setUp(self):
        self.client = _make_client()
        _set_session(self.client, role='organiser_admin',
                     organiser_id=_ORG_IDS.get('org_a_id', 2),
                     user_id=_ORG_IDS.get('org_admin_id', 2))

    def test_organiser_admin_blocked_from_list_organisers(self):
        resp = self.client.get('/admin/organisers', follow_redirects=True)
        self.assertNotIn(b'All Organisers', resp.data)

    def test_organiser_admin_blocked_from_qr_view(self):
        resp = self.client.get(
            f'/admin/organisers/{_ORG_IDS.get("org_a_id", 2)}/qr',
            follow_redirects=True,
        )
        self.assertNotIn(b'Registration QR', resp.data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
