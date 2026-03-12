"""
Structured audit log for admin actions.

Every record is emitted as a single JSON line to the 'lms.audit' logger so it
can be captured, shipped, or searched independently of the application log.

Schema (all fields present on every record):
  ts      ISO-8601 UTC timestamp
  action  snake_case verb describing the operation (e.g. 'admin_login_success')
  outcome 'success' | 'failure' | 'blocked'
  ip      Client IP (set correctly by ProxyFix; see TRUSTED_PROXY_DEPTH in app.py)
  ua      User-Agent header truncated to 120 chars
  ...     Any extra keyword args are merged in as additional context fields

Usage:
  from lms_automation.services.audit import log_admin_action
  log_admin_action("process_results", "success", round_id=3, rounds_completed=1)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from flask import request

audit_logger = logging.getLogger("lms.audit")


def _client_ip() -> str:
    """Return the real client IP.

    ProxyFix (applied in app.py) already rewrites request.remote_addr to the
    true client IP based on X-Forwarded-For, so no manual header parsing is
    needed here.  In local dev (TRUSTED_PROXY_DEPTH=0) ProxyFix is skipped and
    remote_addr is the direct connection address — also correct.
    """
    return request.remote_addr or "unknown"


def log_admin_action(action: str, outcome: str, **details: Any) -> None:
    """
    Emit one structured audit record.

    Parameters
    ----------
    action  : short description of what was attempted, e.g. 'admin_login'
    outcome : 'success', 'failure', or 'blocked'
    **details : arbitrary key/value pairs merged into the record
                (e.g. round_id=3, player_count=12)
    """
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "outcome": outcome,
        "ip": _client_ip(),
        "ua": request.headers.get("User-Agent", "")[:120],
    }
    record.update(details)
    audit_logger.info(json.dumps(record, default=str))
