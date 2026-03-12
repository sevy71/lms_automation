"""
Phase 2a verification: organiser-scoped eligibility.

Tests prove that:
  1. Two organisers with active players each → eligibility for A returns only A's players
  2. Eligibility for B returns only B's players
  3. Missing organiser context does NOT return global results (returns [])
  4. organiser_id mismatch raises ValueError
  5. Existing single-organiser behaviour (cycle/elimination logic) still works
  6. Prior-round eliminations from another organiser do not bleed through

Run:
    python -m pytest tests/test_eligibility_organiser_scope.py -v
or:
    python tests/test_eligibility_organiser_scope.py
"""
import os
import sys
import unittest

# Ensure project root is on path when run directly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _make_app():
    """
    Create a minimal, isolated Flask app backed by an in-memory SQLite database.

    We deliberately do NOT import lms_automation.app (the production singleton)
    to avoid module-level side effects (security guards, scheduler start, etc.).
    Instead we re-use the shared db/SQLAlchemy instance from extensions.py and
    register it with a fresh test app — Flask-SQLAlchemy 3.x fully supports this
    pattern, giving each app its own engine.
    """
    from flask import Flask
    from lms_automation.extensions import db as _ext_db
    # Ensure all model classes are registered in SQLAlchemy's metadata
    import lms_automation.models  # noqa: F401

    test_app = Flask('lms_test')
    test_app.config['TESTING'] = True
    test_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    test_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    _ext_db.init_app(test_app)
    return test_app


class EligibilityOrganiserScopeTests(unittest.TestCase):

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()

        from lms_automation.extensions import db as _db
        from lms_automation.models import Organiser, Player, Round, Pick
        self.db = _db
        self.Organiser = Organiser
        self.Player = Player
        self.Round = Round
        self.Pick = Pick

        _db.create_all()

        # --- Create two organisers ---
        self.org_a = Organiser(name='Organiser A', slug='org-a', status='active')
        self.org_b = Organiser(name='Organiser B', slug='org-b', status='active')
        _db.session.add_all([self.org_a, self.org_b])
        _db.session.flush()

        # --- Players for organiser A ---
        self.p_a1 = Player(name='Alice', status='active', organiser_id=self.org_a.id)
        self.p_a2 = Player(name='Adam',  status='active', organiser_id=self.org_a.id)
        # --- Players for organiser B ---
        self.p_b1 = Player(name='Bob',   status='active', organiser_id=self.org_b.id)
        self.p_b2 = Player(name='Bella', status='active', organiser_id=self.org_b.id)
        # --- Eliminated player for org A (global status) ---
        self.p_a_elim = Player(name='Axel_out', status='eliminated', organiser_id=self.org_a.id)

        _db.session.add_all([
            self.p_a1, self.p_a2, self.p_b1, self.p_b2, self.p_a_elim,
        ])

        # --- Rounds ---
        self.round_a = Round(
            round_number=1, status='active', cycle_number=1,
            organiser_id=self.org_a.id,
        )
        self.round_b = Round(
            round_number=1, status='active', cycle_number=1,
            organiser_id=self.org_b.id,
        )
        _db.session.add_all([self.round_a, self.round_b])
        _db.session.commit()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    # ------------------------------------------------------------------
    # Core isolation tests
    # ------------------------------------------------------------------

    def test_org_a_round_returns_only_org_a_players(self):
        from lms_automation.eligibility import get_eligible_players_for_round
        result = get_eligible_players_for_round(self.round_a)
        ids = {p.id for p in result}

        self.assertIn(self.p_a1.id, ids, "Alice should be eligible for org A")
        self.assertIn(self.p_a2.id, ids, "Adam should be eligible for org A")
        self.assertNotIn(self.p_b1.id, ids, "Bob (org B) must NOT appear in org A results")
        self.assertNotIn(self.p_b2.id, ids, "Bella (org B) must NOT appear in org A results")
        self.assertNotIn(self.p_a_elim.id, ids, "Globally eliminated player must NOT appear")

    def test_org_b_round_returns_only_org_b_players(self):
        from lms_automation.eligibility import get_eligible_players_for_round
        result = get_eligible_players_for_round(self.round_b)
        ids = {p.id for p in result}

        self.assertIn(self.p_b1.id, ids, "Bob should be eligible for org B")
        self.assertIn(self.p_b2.id, ids, "Bella should be eligible for org B")
        self.assertNotIn(self.p_a1.id, ids, "Alice (org A) must NOT appear in org B results")
        self.assertNotIn(self.p_a2.id, ids, "Adam (org A) must NOT appear in org B results")

    def test_no_organiser_context_returns_empty(self):
        """A round with no organiser_id must NOT return global results."""
        from lms_automation.eligibility import get_eligible_players_for_round

        orphan_round = self.Round(
            round_number=99, status='active', cycle_number=1,
            organiser_id=None,
        )
        self.db.session.add(orphan_round)
        self.db.session.commit()

        result = get_eligible_players_for_round(orphan_round)
        self.assertEqual(result, [], "No organiser context must return [] not global list")

    def test_none_round_no_explicit_organiser_returns_empty(self):
        """Passing None round with no organiser_id must fail safe."""
        from lms_automation.eligibility import get_eligible_players_for_round
        result = get_eligible_players_for_round(None)
        self.assertEqual(result, [])

    def test_organiser_id_mismatch_raises(self):
        """Explicit organiser_id that disagrees with round.organiser_id must raise."""
        from lms_automation.eligibility import get_eligible_players_for_round
        with self.assertRaises(ValueError):
            get_eligible_players_for_round(self.round_a, organiser_id=self.org_b.id)

    # ------------------------------------------------------------------
    # Elimination scoping
    # ------------------------------------------------------------------

    def test_prior_round_elimination_scoped_to_organiser(self):
        """
        Elimination in an org A prior round must not affect org B eligibility,
        and vice versa.

        Strategy: use setUp's round_a as the "prior" round (lower ID already
        exists), then create round_a_next AFTER — it gets a higher autoincrement
        ID and is therefore the "current" round for the eligibility call.
        """
        from lms_automation.eligibility import get_eligible_players_for_round

        # Record Alice's elimination in setUp's round_a (which has a LOWER id)
        pick_elim = self.Pick(
            player_id=self.p_a1.id,
            round_id=self.round_a.id,
            is_eliminated=True,
            team_picked='Arsenal',
        )
        self.db.session.add(pick_elim)

        # Create the "current" org A round — autoincrement ensures id > round_a.id
        round_a_next = self.Round(
            round_number=2, status='active', cycle_number=1,
            organiser_id=self.org_a.id,
        )
        self.db.session.add(round_a_next)
        self.db.session.commit()

        # Org A current round: Alice should be eliminated (prior round in same cycle)
        result_a = get_eligible_players_for_round(round_a_next)
        ids_a = {p.id for p in result_a}
        self.assertNotIn(self.p_a1.id, ids_a, "Alice eliminated in org A prior round")
        self.assertIn(self.p_a2.id, ids_a, "Adam still eligible in org A")

        # Org B round: Bob and Bella completely unaffected by org A elimination
        result_b = get_eligible_players_for_round(self.round_b)
        ids_b = {p.id for p in result_b}
        self.assertIn(self.p_b1.id, ids_b, "Bob unaffected by org A elimination")
        self.assertIn(self.p_b2.id, ids_b, "Bella unaffected by org A elimination")

    def test_single_organiser_cycle_elimination_still_works(self):
        """
        Regression: standard cycle-scoped elimination logic is unchanged.

        Strategy: use setUp's round_a as the "prior" round (lower ID), create
        round_a_next with a higher autoincrement ID as the "current" round.
        """
        from lms_automation.eligibility import get_eligible_players_for_round

        # Eliminate Adam in setUp's round_a (lower id = prior round)
        pick = self.Pick(
            player_id=self.p_a2.id,
            round_id=self.round_a.id,
            is_eliminated=True,
            team_picked='Chelsea',
        )
        self.db.session.add(pick)

        # Create the current round (higher autoincrement ID than round_a)
        round_a_next = self.Round(
            round_number=2, status='active', cycle_number=1,
            organiser_id=self.org_a.id,
        )
        self.db.session.add(round_a_next)
        self.db.session.commit()

        result = get_eligible_players_for_round(round_a_next)
        ids = {p.id for p in result}
        self.assertIn(self.p_a1.id, ids, "Alice still eligible")
        self.assertNotIn(self.p_a2.id, ids, "Adam eliminated in prior org A round")

    def test_explicit_organiser_id_matching_round_is_accepted(self):
        """Explicit organiser_id matching round.organiser_id should work normally."""
        from lms_automation.eligibility import get_eligible_players_for_round
        result = get_eligible_players_for_round(self.round_a, organiser_id=self.org_a.id)
        ids = {p.id for p in result}
        self.assertIn(self.p_a1.id, ids)
        self.assertIn(self.p_a2.id, ids)
        self.assertNotIn(self.p_b1.id, ids)


if __name__ == '__main__':
    unittest.main(verbosity=2)
