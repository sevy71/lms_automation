"""
Central Telegram send wrapper.

All outbound Telegram Bot API calls in this application route through
``telegram_client.send_message()``.  This single path provides:

  - Global send-rate cap          (TELEGRAM_GLOBAL_RPS, default 20 msg/s)
  - Per-chat minimum interval     (TELEGRAM_PER_CHAT_MIN_INTERVAL_MS, default 1200 ms)
  - Small random jitter           (20–120 ms per send, hard-coded)
  - 429 / flood-control handling  (parse ``retry_after``, sleep + jitter, retry)
  - Exponential backoff           for 5xx / network errors
  - Bounded retry count           (TELEGRAM_MAX_RETRIES, default 5)
  - Structured log lines          on every throttle event / retry / failure

CONFIG (env vars, all optional with defaults shown)
====================================================
  TELEGRAM_GLOBAL_RPS              max global sends per second     (default: 20)
  TELEGRAM_PER_CHAT_MIN_INTERVAL_MS min gap between sends to same  (default: 1200 ms)
                                   chat ID
  TELEGRAM_MAX_RETRIES             per-send retry cap              (default: 5)
  TELEGRAM_BACKOFF_BASE_MS         first retry backoff             (default: 500 ms)
  TELEGRAM_BACKOFF_MAX_MS          backoff cap                     (default: 30 000 ms)

Batch-campaign vars (consumed by the delivery worker, not this module):
  TELEGRAM_BATCH_SIZE              records per delivery batch      (default: 50)
  TELEGRAM_BATCH_PAUSE_MS          pause between batches           (default: 1000 ms)

RETRY BEHAVIOUR
===============
  - 200 OK          → success, return immediately
  - 429             → parse ``retry_after`` from JSON, sleep (retry_after + jitter),
                       retry up to TELEGRAM_MAX_RETRIES
  - 4xx (not 429)   → permanent client error, no retry
  - 5xx / timeout   → exponential backoff (BASE * 2^(attempt-1), capped at MAX),
                       retry up to TELEGRAM_MAX_RETRIES
  - network error   → same as 5xx

IDEMPOTENCY NOTE
================
This module never stores state about whether a message was sent; idempotency
is the responsibility of the outbox layer (see notifications.py).  Retries
here are within a single logical send attempt; if all retries fail, the caller
receives (False, error_str) and can decide whether to reschedule.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jitter (hard-coded, not configurable — intentionally small and random)
# ---------------------------------------------------------------------------

_JITTER_MIN_MS: int = 20
_JITTER_MAX_MS: int = 120


def _jitter() -> float:
    """Return a random jitter value in seconds (20–120 ms)."""
    return random.randint(_JITTER_MIN_MS, _JITTER_MAX_MS) / 1000.0


# ---------------------------------------------------------------------------
# Config helpers (re-read from env on every call to pick up runtime changes)
# ---------------------------------------------------------------------------

def _cfg_int(key: str, default: int) -> int:
    try:
        val = int(os.environ.get(key, default))
        return max(1, val)
    except (ValueError, TypeError):
        return default


def _global_rps() -> int:
    return _cfg_int("TELEGRAM_GLOBAL_RPS", 20)


def _per_chat_min_interval() -> float:
    return _cfg_int("TELEGRAM_PER_CHAT_MIN_INTERVAL_MS", 1200) / 1000.0


def _max_retries() -> int:
    return _cfg_int("TELEGRAM_MAX_RETRIES", 5)


def _backoff_base() -> float:
    return _cfg_int("TELEGRAM_BACKOFF_BASE_MS", 500) / 1000.0


def _backoff_max() -> float:
    return _cfg_int("TELEGRAM_BACKOFF_MAX_MS", 30_000) / 1000.0


# ---------------------------------------------------------------------------
# Rate-limiter state (module-level, thread-safe)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_last_global_send: float = 0.0          # monotonic time of the last reserved global slot
_per_chat_last: dict[str, float] = {}   # chat_id → monotonic time of last reserved slot

_DELIVERY_TIMEOUT_SECS: int = 10


def _throttle(chat_id: str) -> None:
    """
    Enforce the global RPS cap and the per-chat minimum send interval.

    Both checks are performed atomically inside a lock so that concurrent
    callers reserve disjoint slots.  The actual sleep happens *outside* the
    lock so that threads waiting on different chats do not block each other.

    A small random jitter (20–120 ms) is always added after the mandatory wait
    to spread bursts and avoid correlated retries.
    """
    global _last_global_send

    min_global = 1.0 / _global_rps()
    min_chat = _per_chat_min_interval()

    with _lock:
        now = time.monotonic()
        global_wait = max(0.0, min_global - (now - _last_global_send))
        chat_wait = max(0.0, min_chat - (now - _per_chat_last.get(chat_id, 0.0)))
        wait = max(global_wait, chat_wait)

        # Reserve both slots at the projected send time so that the next
        # concurrent caller correctly queues behind us.
        send_at = now + wait
        _last_global_send = send_at
        _per_chat_last[chat_id] = send_at

    if wait > 0:
        logger.debug(
            "telegram_client throttle: chat_id=%s waiting=%.3fs "
            "(global_wait=%.3fs chat_wait=%.3fs)",
            chat_id, wait, global_wait, chat_wait,
        )
        time.sleep(wait)

    time.sleep(_jitter())  # always add small random jitter


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_message(
    chat_id: str,
    text: str,
    button_url: Optional[str] = None,
    button_text: Optional[str] = None,
    parse_mode: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send a single Telegram message through the Bot API.

    Applies per-chat and global rate limiting, handles 429 flood-control
    responses, and retries transient 5xx / network errors with exponential
    backoff.  Never raises.

    Returns:
        ``(True, None)``         on success
        ``(False, error_str)``   after all retries are exhausted
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return False, "TELEGRAM_BOT_TOKEN not set"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if button_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{
                "text": button_text or "Open",
                "url": button_url,
            }]]
        }

    max_ret = _max_retries()
    b_base = _backoff_base()
    b_max = _backoff_max()

    last_error: Optional[str] = None

    for attempt in range(1, max_ret + 1):
        _throttle(chat_id)

        try:
            response = requests.post(url, json=payload, timeout=_DELIVERY_TIMEOUT_SECS)

            if response.status_code == 200:
                logger.debug(
                    "telegram_client sent: chat_id=%s attempt=%s/%s",
                    chat_id, attempt, max_ret,
                )
                return True, None

            if response.status_code == 429:
                # Telegram flood control — honour the retry_after instruction.
                try:
                    data = response.json()
                    retry_after = int(data.get("parameters", {}).get("retry_after", 10))
                except Exception:
                    retry_after = 10

                last_error = f"429 retry_after={retry_after}s"
                wait = retry_after + _jitter()
                logger.warning(
                    "telegram_client 429 flood-control: chat_id=%s attempt=%s/%s "
                    "retry_after=%ss total_wait=%.2fs",
                    chat_id, attempt, max_ret, retry_after, wait,
                )
                if attempt < max_ret:
                    time.sleep(wait)
                    continue
                break  # exhausted retries on 429

            # Non-200, non-429 HTTP response.
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.warning(
                "telegram_client HTTP error: chat_id=%s attempt=%s/%s status=%s",
                chat_id, attempt, max_ret, response.status_code,
            )
            # 4xx (excluding 429) are permanent client errors — do not retry.
            if 400 <= response.status_code < 500:
                break

            # 5xx — apply exponential backoff and retry.
            if attempt < max_ret:
                backoff = min(b_base * (2 ** (attempt - 1)), b_max) + _jitter()
                logger.warning(
                    "telegram_client 5xx backoff: chat_id=%s attempt=%s/%s "
                    "sleeping=%.2fs",
                    chat_id, attempt, max_ret, backoff,
                )
                time.sleep(backoff)

        except requests.Timeout:
            last_error = f"timeout after {_DELIVERY_TIMEOUT_SECS}s"
            logger.warning(
                "telegram_client timeout: chat_id=%s attempt=%s/%s",
                chat_id, attempt, max_ret,
            )
            if attempt < max_ret:
                backoff = min(b_base * (2 ** (attempt - 1)), b_max) + _jitter()
                time.sleep(backoff)

        except Exception as exc:
            last_error = str(exc)[:500]
            logger.warning(
                "telegram_client exception: chat_id=%s attempt=%s/%s error=%s",
                chat_id, attempt, max_ret, last_error,
            )
            if attempt < max_ret:
                backoff = min(b_base * (2 ** (attempt - 1)), b_max) + _jitter()
                time.sleep(backoff)

    logger.error(
        "telegram_client FAILED: chat_id=%s attempts=%s/%s last_error=%s",
        chat_id, max_ret, max_ret, last_error,
    )
    return False, last_error


# ---------------------------------------------------------------------------
# Introspection helpers (used by tests / admin tooling)
# ---------------------------------------------------------------------------

def reset_state() -> None:
    """
    Reset all rate-limiter state.

    Intended for use in tests only — clears per-chat and global timestamps so
    tests start from a clean slate without cross-test interference.
    """
    global _last_global_send
    with _lock:
        _last_global_send = 0.0
        _per_chat_last.clear()


def get_state_snapshot() -> dict:
    """
    Return a copy of current rate-limiter state (for tests / observability).
    """
    with _lock:
        return {
            "last_global_send": _last_global_send,
            "per_chat_count": len(_per_chat_last),
            "per_chat": dict(_per_chat_last),
        }
