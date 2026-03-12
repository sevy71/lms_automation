"""
Telegram rate-limiting & retry verification tests.

Tests prove that:
  1. Bulk send respects the global RPS cap (sends are paced)
  2. Same chat is not spammed faster than per-chat minimum interval
  3. Simulated 429 obeys retry_after (sleeps ≥ retry_after before retrying)
  4. Transient 5xx failures retry and eventually succeed (or fail cleanly)
  5. No duplicate delivery when retries occur (idempotency via outbox key)
  6. Max retries are bounded — does not spin forever

Run:
    python -m pytest tests/test_telegram_rate_limiting.py -v
or:
    python tests/test_telegram_rate_limiting.py
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call

# Ensure project root is on path when run directly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_200():
    """Simulate a successful Telegram API response."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"ok": True}
    return r


def _mock_429(retry_after: int = 5):
    """Simulate a Telegram 429 flood-control response."""
    r = MagicMock()
    r.status_code = 429
    r.text = f'{{"ok": false, "error_code": 429, "description": "Too Many Requests: retry after {retry_after}", "parameters": {{"retry_after": {retry_after}}}}}'
    r.json.return_value = {
        "ok": False,
        "error_code": 429,
        "description": f"Too Many Requests: retry after {retry_after}",
        "parameters": {"retry_after": retry_after},
    }
    return r


def _mock_500():
    """Simulate a Telegram 500 server error."""
    r = MagicMock()
    r.status_code = 500
    r.text = "Internal Server Error"
    r.json.return_value = {"ok": False}
    return r


def _mock_400():
    """Simulate a Telegram 400 client error (e.g. chat not found)."""
    r = MagicMock()
    r.status_code = 400
    r.text = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    r.json.return_value = {"ok": False}
    return r


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestGlobalRateCap(unittest.TestCase):
    """Test 1: Bulk send respects global RPS cap."""

    def setUp(self):
        import lms_automation.services.telegram_client as tc
        tc.reset_state()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
        os.environ["TELEGRAM_GLOBAL_RPS"] = "10"          # 10/s → 100ms gap
        os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "0"
        os.environ["TELEGRAM_MAX_RETRIES"] = "1"

    def tearDown(self):
        for k in ["TELEGRAM_GLOBAL_RPS", "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS",
                  "TELEGRAM_MAX_RETRIES"]:
            os.environ.pop(k, None)

    def test_sends_are_paced_globally(self):
        """10 sends at 10 RPS must take at least (N-1) * 0.1s ≈ 0.9s."""
        import lms_automation.services.telegram_client as tc

        n = 10
        send_times = []

        with patch("requests.post", return_value=_mock_200()):
            for i in range(n):
                before = time.monotonic()
                ok, err = tc.send_message(chat_id=f"chat_{i}", text="hi")
                send_times.append(time.monotonic())
                self.assertTrue(ok, f"send {i} failed: {err}")

        # The gap between the first and last send should be at least (n-1) * min_interval.
        # min_interval = 1/10 = 0.1s; for n=10 that is 0.9s minimum.
        # We allow generous 30% tolerance on top for timing jitter in CI.
        min_interval = 1.0 / 10
        expected_min = (n - 1) * min_interval * 0.70   # 70% of theoretical minimum
        elapsed = send_times[-1] - send_times[0]
        self.assertGreaterEqual(
            elapsed, expected_min,
            f"10 sends took only {elapsed:.3f}s, expected ≥ {expected_min:.3f}s "
            f"(global RPS cap not respected)",
        )


class TestPerChatThrottle(unittest.TestCase):
    """Test 2: Same chat is not spammed faster than per-chat minimum interval."""

    def setUp(self):
        import lms_automation.services.telegram_client as tc
        tc.reset_state()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
        os.environ["TELEGRAM_GLOBAL_RPS"] = "1000"         # effectively unlimited
        os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "200"  # 200ms gap
        os.environ["TELEGRAM_MAX_RETRIES"] = "1"

    def tearDown(self):
        for k in ["TELEGRAM_GLOBAL_RPS", "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS",
                  "TELEGRAM_MAX_RETRIES"]:
            os.environ.pop(k, None)

    def test_per_chat_interval_enforced(self):
        """3 sends to the same chat at 200ms interval must take ≥ 0.4s."""
        import lms_automation.services.telegram_client as tc

        n = 3
        send_times = []

        with patch("requests.post", return_value=_mock_200()):
            for _ in range(n):
                tc.send_message(chat_id="same_chat", text="hi")
                send_times.append(time.monotonic())

        min_interval_s = 0.200
        expected_min = (n - 1) * min_interval_s * 0.70
        elapsed = send_times[-1] - send_times[0]
        self.assertGreaterEqual(
            elapsed, expected_min,
            f"3 sends to same chat took {elapsed:.3f}s, expected ≥ {expected_min:.3f}s "
            f"(per-chat interval not respected)",
        )

    def test_different_chats_not_blocked_by_per_chat(self):
        """Sends to different chats are NOT rate-limited by per-chat tracking."""
        import lms_automation.services.telegram_client as tc

        n = 5
        start = time.monotonic()

        with patch("requests.post", return_value=_mock_200()):
            for i in range(n):
                tc.send_message(chat_id=f"different_chat_{i}", text="hi")

        elapsed = time.monotonic() - start
        # At 200ms per-chat interval, different chats should not cause 200ms*5=1s delay.
        # Allow 0.7s max (generous for jitter).
        self.assertLess(
            elapsed, 0.70,
            f"5 sends to different chats took {elapsed:.3f}s — "
            f"per-chat throttle incorrectly applied across chats",
        )


class TestFloodControl429(unittest.TestCase):
    """Test 3: Simulated 429 obeys retry_after."""

    def setUp(self):
        import lms_automation.services.telegram_client as tc
        tc.reset_state()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
        os.environ["TELEGRAM_GLOBAL_RPS"] = "1000"
        os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "0"
        os.environ["TELEGRAM_MAX_RETRIES"] = "3"
        os.environ["TELEGRAM_BACKOFF_BASE_MS"] = "10"

    def tearDown(self):
        for k in ["TELEGRAM_GLOBAL_RPS", "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS",
                  "TELEGRAM_MAX_RETRIES", "TELEGRAM_BACKOFF_BASE_MS"]:
            os.environ.pop(k, None)

    def test_429_retries_after_retry_after(self):
        """After a 429, the client must wait ≥ retry_after seconds before retry."""
        import lms_automation.services.telegram_client as tc

        retry_after = 1  # 1 second (small enough for test)
        sleep_calls = []

        original_sleep = time.sleep

        def tracking_sleep(secs):
            sleep_calls.append(secs)
            # Don't actually sleep the full amount in tests — just record it.

        responses = [_mock_429(retry_after), _mock_200()]

        with patch("requests.post", side_effect=responses):
            with patch("time.sleep", side_effect=tracking_sleep):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertTrue(ok, f"Expected success after 429 retry, got error: {err}")
        # At least one sleep should be ≥ retry_after (plus jitter, but we track the
        # raw argument passed to sleep).
        significant_sleeps = [s for s in sleep_calls if s >= retry_after]
        self.assertTrue(
            significant_sleeps,
            f"Expected at least one sleep ≥ {retry_after}s after 429, "
            f"got sleep calls: {sleep_calls}",
        )

    def test_429_exhausted_retries_returns_failure(self):
        """When all retries are 429s, send_message must return (False, ...)."""
        import lms_automation.services.telegram_client as tc

        os.environ["TELEGRAM_MAX_RETRIES"] = "2"

        responses = [_mock_429(1), _mock_429(1)]

        with patch("requests.post", side_effect=responses):
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertFalse(ok)
        self.assertIn("429", err)

    def test_429_bounded_api_calls(self):
        """Client must not make more than TELEGRAM_MAX_RETRIES API calls."""
        import lms_automation.services.telegram_client as tc

        max_retries = 3
        os.environ["TELEGRAM_MAX_RETRIES"] = str(max_retries)

        with patch("requests.post", return_value=_mock_429(1)) as mock_post:
            with patch("time.sleep"):
                tc.send_message(chat_id="chat_1", text="hi")

        self.assertLessEqual(
            mock_post.call_count, max_retries,
            f"Expected ≤ {max_retries} API calls, got {mock_post.call_count}",
        )


class TestTransientFailures(unittest.TestCase):
    """Test 4: Transient 5xx failures retry then recover (or fail cleanly)."""

    def setUp(self):
        import lms_automation.services.telegram_client as tc
        tc.reset_state()
        os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
        os.environ["TELEGRAM_GLOBAL_RPS"] = "1000"
        os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "0"
        os.environ["TELEGRAM_BACKOFF_BASE_MS"] = "10"

    def tearDown(self):
        for k in ["TELEGRAM_GLOBAL_RPS", "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS",
                  "TELEGRAM_BACKOFF_BASE_MS", "TELEGRAM_MAX_RETRIES"]:
            os.environ.pop(k, None)

    def test_5xx_retries_then_succeeds(self):
        """Two 5xx errors followed by 200 — should succeed on third attempt."""
        import lms_automation.services.telegram_client as tc

        os.environ["TELEGRAM_MAX_RETRIES"] = "3"
        responses = [_mock_500(), _mock_500(), _mock_200()]

        with patch("requests.post", side_effect=responses) as mock_post:
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertTrue(ok, f"Expected success after 2 retries, got: {err}")
        self.assertEqual(mock_post.call_count, 3)

    def test_5xx_all_retries_exhausted_returns_failure(self):
        """All retries are 5xx — must return (False, ...) cleanly."""
        import lms_automation.services.telegram_client as tc

        os.environ["TELEGRAM_MAX_RETRIES"] = "3"
        responses = [_mock_500(), _mock_500(), _mock_500()]

        with patch("requests.post", side_effect=responses):
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertFalse(ok)
        self.assertIn("500", err)

    def test_4xx_no_retry(self):
        """4xx errors (except 429) must NOT be retried."""
        import lms_automation.services.telegram_client as tc

        os.environ["TELEGRAM_MAX_RETRIES"] = "5"

        with patch("requests.post", return_value=_mock_400()) as mock_post:
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertFalse(ok)
        # Should only have made ONE API call (no retry for 4xx).
        self.assertEqual(
            mock_post.call_count, 1,
            f"4xx should not be retried, but got {mock_post.call_count} API calls",
        )

    def test_network_exception_retries(self):
        """Network exceptions trigger retry with backoff."""
        import lms_automation.services.telegram_client as tc
        import requests as req_lib

        os.environ["TELEGRAM_MAX_RETRIES"] = "3"

        with patch("requests.post",
                   side_effect=[req_lib.ConnectionError("network down"),
                                 req_lib.ConnectionError("network down"),
                                 _mock_200()]) as mock_post:
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertTrue(ok, f"Expected success after network retry: {err}")
        self.assertEqual(mock_post.call_count, 3)

    def test_max_retries_is_bounded(self):
        """Client never exceeds TELEGRAM_MAX_RETRIES API calls regardless of errors."""
        import lms_automation.services.telegram_client as tc

        os.environ["TELEGRAM_MAX_RETRIES"] = "5"

        with patch("requests.post", return_value=_mock_500()) as mock_post:
            with patch("time.sleep"):
                ok, err = tc.send_message(chat_id="chat_1", text="hi")

        self.assertFalse(ok)
        self.assertLessEqual(
            mock_post.call_count, 5,
            f"Expected ≤ 5 API calls, got {mock_post.call_count}",
        )


class TestNoDuplicateDelivery(unittest.TestCase):
    """Test 5: No duplicate delivery when retries occur (outbox idempotency)."""

    def _make_app(self):
        """Create a minimal Flask app with SQLite in-memory DB."""
        import flask
        import flask_sqlalchemy

        app = flask.Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["TESTING"] = True

        from lms_automation.models import db
        db.init_app(app)
        with app.app_context():
            db.create_all()

        return app

    def test_idempotency_key_prevents_duplicate_enqueue(self):
        """Enqueuing the same idempotency_key twice inserts only one row."""
        app = self._make_app()
        with app.app_context():
            from lms_automation.models import db, NotificationOutbox
            from lms_automation.services.notifications import enqueue_notification

            key = "test_event:chat_99:round_1"
            first = enqueue_notification(
                telegram_id="chat_99",
                message="Hello",
                idempotency_key=key,
            )
            db.session.commit()

            second = enqueue_notification(
                telegram_id="chat_99",
                message="Hello",
                idempotency_key=key,
            )
            db.session.commit()

            self.assertTrue(first, "First enqueue should succeed")
            self.assertFalse(second, "Duplicate enqueue should be skipped")

            count = NotificationOutbox.query.filter_by(
                idempotency_key=key
            ).count()
            self.assertEqual(count, 1, "Only one row should exist for the same idempotency key")

    def test_sent_row_blocks_reenqueue(self):
        """A 'sent' row with the same idempotency_key prevents re-enqueue."""
        app = self._make_app()
        with app.app_context():
            from lms_automation.models import db, NotificationOutbox
            from lms_automation.services.notifications import enqueue_notification
            from datetime import datetime, timezone

            key = "test_event:chat_88:round_2"
            enqueue_notification(
                telegram_id="chat_88",
                message="Hello",
                idempotency_key=key,
            )
            # Simulate successful delivery.
            row = NotificationOutbox.query.filter_by(idempotency_key=key).first()
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            db.session.commit()

            # Attempt re-enqueue (e.g. scheduler running again).
            second = enqueue_notification(
                telegram_id="chat_88",
                message="Hello",
                idempotency_key=key,
            )
            db.session.commit()

            self.assertFalse(second, "Re-enqueue after 'sent' should be skipped")
            count = NotificationOutbox.query.filter_by(
                idempotency_key=key
            ).count()
            self.assertEqual(count, 1)

    def test_only_mark_delivered_on_200(self):
        """Outbox row is marked 'sent' only on confirmed 200 from Telegram."""
        app = self._make_app()
        with app.app_context():
            from lms_automation.models import db, NotificationOutbox
            from lms_automation.services.notifications import (
                enqueue_notification, deliver_pending_notifications,
            )
            from datetime import datetime, timedelta, timezone

            os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
            os.environ["TELEGRAM_MAX_RETRIES"] = "1"
            os.environ["TELEGRAM_GLOBAL_RPS"] = "1000"
            os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "0"

            enqueue_notification(
                telegram_id="chat_77",
                message="Test",
                idempotency_key="only_200:chat_77:1",
            )
            db.session.commit()

            # First delivery attempt: simulate 500 (should stay pending/retrying).
            with patch("requests.post", return_value=_mock_500()):
                with patch("time.sleep"):
                    deliver_pending_notifications()

            row = NotificationOutbox.query.filter_by(
                idempotency_key="only_200:chat_77:1"
            ).first()
            self.assertNotEqual(
                row.status, "sent",
                "Row must NOT be marked sent after 500 response",
            )

            # Advance last_attempt_at far into the past so backoff has elapsed.
            row.last_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=300)
            db.session.commit()

            # Second delivery attempt: simulate 200 (should become sent).
            with patch("requests.post", return_value=_mock_200()):
                with patch("time.sleep"):
                    deliver_pending_notifications()

            db.session.refresh(row)
            self.assertEqual(
                row.status, "sent",
                f"Row must be 'sent' after 200 response, got: {row.status}",
            )

        for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_MAX_RETRIES",
                  "TELEGRAM_GLOBAL_RPS", "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"]:
            os.environ.pop(k, None)


class TestBulkCampaignBatching(unittest.TestCase):
    """Test 6: Bulk campaign respects batch size and logs progress."""

    def _make_app(self):
        import flask
        app = flask.Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        app.config["TESTING"] = True
        from lms_automation.models import db
        db.init_app(app)
        with app.app_context():
            db.create_all()
        return app

    def test_batch_pause_applied_between_batches(self):
        """With 10 records and batch_size=3, expect 2 inter-batch pauses."""
        app = self._make_app()
        with app.app_context():
            from lms_automation.models import db, NotificationOutbox
            from lms_automation.services.notifications import (
                enqueue_notification, deliver_pending_notifications,
            )

            os.environ["TELEGRAM_BOT_TOKEN"] = "test_token"
            os.environ["TELEGRAM_MAX_RETRIES"] = "1"
            os.environ["TELEGRAM_GLOBAL_RPS"] = "1000"
            os.environ["TELEGRAM_PER_CHAT_MIN_INTERVAL_MS"] = "0"
            os.environ["TELEGRAM_BATCH_SIZE"] = "3"
            os.environ["TELEGRAM_BATCH_PAUSE_MS"] = "300"  # 300ms: above jitter range (20-120ms)

            n = 10
            for i in range(n):
                enqueue_notification(
                    telegram_id=f"chat_bulk_{i}",
                    message="bulk",
                    idempotency_key=f"bulk:chat_{i}:1",
                )
            db.session.commit()

            sleep_calls = []

            def tracking_sleep(s):
                sleep_calls.append(s)

            with patch("requests.post", return_value=_mock_200()):
                with patch("time.sleep", side_effect=tracking_sleep):
                    result = deliver_pending_notifications()

            self.assertEqual(result["sent"], n)
            # 10 records / batch_size 3 = 4 batches → 3 inter-batch pauses.
            # Batch pause is 300ms; jitter is 20-120ms — filter on ≥ 150ms.
            batch_pauses = [s for s in sleep_calls if s >= 0.150]
            self.assertEqual(
                len(batch_pauses), 3,
                f"Expected 3 inter-batch pauses ≥ 150ms, got sleep_calls={sleep_calls}",
            )

        for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_MAX_RETRIES", "TELEGRAM_GLOBAL_RPS",
                  "TELEGRAM_PER_CHAT_MIN_INTERVAL_MS", "TELEGRAM_BATCH_SIZE",
                  "TELEGRAM_BATCH_PAUSE_MS", "TELEGRAM_BACKOFF_BASE_MS"]:
            os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# Entry point for direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
