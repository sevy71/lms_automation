"""
Notification outbox — transactional enqueue + async delivery.

ARCHITECTURE
============
Game-state code calls ``enqueue_notification()`` inside the same DB transaction
that changes round/player state.  The function adds a ``NotificationOutbox`` row
to the current session without committing; the caller's commit persists both the
state change and the pending notification atomically.

A dedicated APScheduler job calls ``deliver_pending_notifications()`` every
minute.  It delivers each pending row via the Telegram Bot API with a hard
timeout, respects exponential backoff between retries, and marks rows ``dead``
after ``_MAX_ATTEMPTS`` failures.

IDEMPOTENCY
===========
Every logical notification event carries a deterministic ``idempotency_key``
of the form ``'{event}:{telegram_id}:{scope}'``.  Before inserting, the enqueue
function checks whether a row with the same key already exists in ``pending`` or
``sent`` status and skips the insert if so.  This prevents duplicate messages
when caller paths are retried (e.g. due to a rolled-back game-state transaction
that is then re-entered).

RETRY BACKOFF
=============
Delivery attempts use exponential backoff capped at ``_BACKOFF_MAX_SECS``::

    delay_after_attempt_N = min(BASE * 2^(N-1), MAX)
    N=1  → 60 s
    N=2  → 120 s
    N=3  → dead  (with default max_attempts=3)

If max_attempts is raised, delays extend further up to the cap (900 s = 15 min).

OBSERVABILITY
=============
Query ``notification_outbox`` directly, or use the admin API:
    GET  /api/admin/notifications/status   — counts + recent dead
    POST /api/admin/notifications/requeue  — reset dead rows to pending

LOCAL DEV
=========
Set TELEGRAM_BOT_TOKEN for live sends, or leave unset — rows reach ``dead``
after 3 attempts (visible in the table).  The requeue admin action works
identically in development.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from lms_automation.models import db, NotificationOutbox

logger = logging.getLogger(__name__)

_DELIVERY_TIMEOUT_SECS: int = 10
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECS: int = 60    # first retry wait
_BACKOFF_MAX_SECS: int = 900    # cap at 15 min


# ---------------------------------------------------------------------------
# Enqueue (write path — called inside game-state transactions)
# ---------------------------------------------------------------------------

def enqueue_notification(
    telegram_id: Optional[str],
    message: str,
    button_url: Optional[str] = None,
    button_text: Optional[str] = None,
    round_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
) -> bool:
    """
    Add one notification to the outbox.

    Does NOT commit — the caller must commit so the row lands in the same
    transaction as the game-state changes.

    Idempotency: if ``idempotency_key`` is given and a ``pending`` or ``sent``
    row with the same key already exists, the insert is skipped and ``False`` is
    returned.  This prevents duplicate messages when a caller path is retried.

    Returns ``False`` (no-op) when ``telegram_id`` is falsy.
    """
    if not telegram_id:
        return False

    if idempotency_key:
        existing = (
            NotificationOutbox.query
            .filter(
                NotificationOutbox.idempotency_key == idempotency_key,
                NotificationOutbox.status.in_(["pending", "sent"]),
            )
            .first()
        )
        if existing:
            logger.debug(
                "enqueue_notification: skipped duplicate idem_key=%s "
                "(outbox_id=%s status=%s)",
                idempotency_key, existing.id, existing.status,
            )
            return False

    db.session.add(NotificationOutbox(
        telegram_id=telegram_id,
        message=message,
        button_url=button_url,
        button_text=button_text,
        round_id=round_id,
        idempotency_key=idempotency_key,
        status="pending",
    ))
    return True


# ---------------------------------------------------------------------------
# Delivery worker (called by the scheduler job)
# ---------------------------------------------------------------------------

def deliver_pending_notifications() -> dict:
    """
    One delivery pass over all pending outbox rows.

    Records not yet past their backoff window are skipped and retried on the
    next scheduler tick.  Each record is committed independently so a crash
    mid-batch does not leave rows in an inconsistent state.

    Backoff schedule (default)::

        attempt 1 → wait  60 s  → attempt 2
        attempt 2 → wait 120 s  → attempt 3
        attempt 3 → DEAD

    Returns an observability summary::

        {"sent": N, "skipped_backoff": N, "retrying": N, "dead": N, "total": N}
    """
    try:
        pending = (
            NotificationOutbox.query
            .filter_by(status="pending")
            .order_by(NotificationOutbox.id)
            .all()
        )
    except Exception as exc:
        logger.error("deliver_pending: failed to query outbox: %s", exc)
        return {"sent": 0, "skipped_backoff": 0, "retrying": 0, "dead": 0, "total": 0}

    sent = skipped_backoff = retrying = dead = 0

    for record in pending:
        if not _backoff_elapsed(record):
            skipped_backoff += 1
            continue

        try:
            ok, err = _do_send(record)
            record.attempts += 1
            record.last_attempt_at = datetime.now(timezone.utc)

            if ok:
                record.status = "sent"
                record.sent_at = datetime.now(timezone.utc)
                sent += 1
                logger.info(
                    "Delivered outbox_id=%s telegram_id=%s (attempt %s)",
                    record.id, record.telegram_id, record.attempts,
                )
            else:
                record.last_error = err
                if record.attempts >= record.max_attempts:
                    record.status = "dead"
                    dead += 1
                    logger.error(
                        "Notification outbox_id=%s DEAD after %s attempts — last error: %s",
                        record.id, record.attempts, err,
                    )
                else:
                    retrying += 1
                    next_delay = min(
                        _BACKOFF_BASE_SECS * (2 ** record.attempts),
                        _BACKOFF_MAX_SECS,
                    )
                    logger.warning(
                        "Notification outbox_id=%s attempt %s/%s failed (%s)"
                        " — retry in ~%ds",
                        record.id, record.attempts, record.max_attempts,
                        err, next_delay,
                    )

            db.session.commit()

        except Exception as exc:
            logger.error(
                "deliver_pending: unexpected error for outbox_id=%s: %s",
                record.id, exc,
            )
            try:
                db.session.rollback()
            except Exception:
                pass

    if pending:
        logger.info(
            "deliver_pending_notifications complete: "
            "sent=%s skipped_backoff=%s retrying=%s dead=%s total=%s",
            sent, skipped_backoff, retrying, dead, len(pending),
        )

    return {
        "sent": sent,
        "skipped_backoff": skipped_backoff,
        "retrying": retrying,
        "dead": dead,
        "total": len(pending),
    }


def requeue_dead_notifications(round_id: Optional[int] = None) -> int:
    """
    Reset dead outbox rows back to ``pending`` for re-delivery.

    Clears ``attempts``, ``last_error``, and ``last_attempt_at`` so the rows
    are treated as fresh and will be delivered on the next scheduler tick
    (no backoff wait since ``attempts`` is 0 after reset).

    Args:
        round_id: when provided, only requeue dead rows for that round.

    Returns number of rows requeued.  Commits internally (admin action context).
    """
    q = NotificationOutbox.query.filter_by(status="dead")
    if round_id is not None:
        q = q.filter_by(round_id=round_id)
    dead_records = q.all()

    for record in dead_records:
        record.status = "pending"
        record.attempts = 0
        record.last_error = None
        record.last_attempt_at = None

    if dead_records:
        db.session.commit()

    return len(dead_records)


# ---------------------------------------------------------------------------
# Backoff helper
# ---------------------------------------------------------------------------

def _backoff_elapsed(record: NotificationOutbox) -> bool:
    """
    Return ``True`` if the record is due for another delivery attempt.

    A record with zero attempts (or no ``last_attempt_at``) is always eligible.
    Otherwise the required delay is ``BASE * 2^(attempts-1)`` capped at MAX.
    """
    if record.attempts == 0 or record.last_attempt_at is None:
        return True
    delay = min(_BACKOFF_BASE_SECS * (2 ** (record.attempts - 1)), _BACKOFF_MAX_SECS)
    last = record.last_attempt_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= last + timedelta(seconds=delay)


# ---------------------------------------------------------------------------
# Low-level HTTP send (private)
# ---------------------------------------------------------------------------

def _do_send(record: NotificationOutbox) -> tuple[bool, str | None]:
    """
    Make one Telegram Bot API call.

    Returns ``(True, None)`` on success or ``(False, error_string)`` on failure.
    Never raises.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return False, "TELEGRAM_BOT_TOKEN not set"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict = {"chat_id": record.telegram_id, "text": record.message}

    if record.button_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{
                "text": record.button_text or "Open",
                "url": record.button_url,
            }]]
        }

    try:
        response = requests.post(url, json=payload, timeout=_DELIVERY_TIMEOUT_SECS)
        if response.status_code == 200:
            return True, None
        err = f"HTTP {response.status_code}: {response.text[:200]}"
        return False, err
    except requests.Timeout:
        return False, f"timeout after {_DELIVERY_TIMEOUT_SECS}s"
    except Exception as exc:
        return False, str(exc)[:500]
