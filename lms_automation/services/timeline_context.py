"""
TimelineContext — safe context manager for instrumenting automation jobs.

Wraps a scheduler job (or any critical operation) so that every run, decision,
checkpoint, and action is persisted to the timeline tables.

Design principles:
  - NEVER raises: all timeline exceptions are logged and swallowed so they
    cannot break the wrapped business logic.
  - Commits timeline records independently, in their own small transactions,
    so the audit trail survives even if the main operation rolls back.
  - The context manager does NOT suppress exceptions from the wrapped code —
    it only handles its own timeline bookkeeping.

Usage::

    from lms_automation.services.timeline_context import TimelineContext

    def process_eliminations(self):
        with self.app.app_context():
            with TimelineContext('process_eliminations',
                                 trigger_type='scheduler') as tl:
                # ... existing job body ...
                tl.checkpoint('before_eliminations_r3')
                tl.decision(step, 'Eliminate player X', rule='...',
                            confidence='high', impact='1 player eliminated')
                tl.action('player_eliminated', outcome='applied',
                          impact='player_id=5 -> eliminated')
            # run is auto-closed in __exit__
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class TimelineContext:
    """
    Context manager that wraps an automation job with full timeline tracking.

    Parameters
    ----------
    job_name : str
        Human-readable name, used as run label in the audit log.
    trigger_type : str
        'scheduler' | 'manual' | 'api' | 'replay'
    mode : str
        'auto' | 'manual'
    organiser_id : int | None
        Scope the run to a specific organiser.  None = unscoped / system-wide.
    actor : str
        Who initiated the run.  Defaults to 'system'.
    """

    def __init__(
        self,
        job_name: str,
        trigger_type: str = 'scheduler',
        mode: str = 'auto',
        organiser_id: Optional[int] = None,
        actor: str = 'system',
    ):
        self.job_name = job_name
        self.trigger_type = trigger_type
        self.mode = mode
        self.organiser_id = organiser_id
        self.actor = actor

        self.run = None
        self._step = 0
        self._actions: int = 0
        self._affected_players: int = 0
        self._affected_rounds: int = 0
        self._affected_reminders: int = 0
        self._has_warnings: bool = False
        self._enabled: bool = True  # set False if import fails

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> 'TimelineContext':
        try:
            from lms_automation.services.run_timeline import (
                create_run, create_checkpoint, append_audit_event,
            )
            from lms_automation.extensions import db

            self.run = create_run(
                trigger_type=self.trigger_type,
                mode=self.mode,
                organiser_id=self.organiser_id,
            )
            # Commit run immediately so it's visible even if the job crashes.
            db.session.commit()

            # Start-of-run checkpoint.
            create_checkpoint(
                run=self.run,
                label=f'run_start_{self.job_name}',
                organiser_id=self.organiser_id,
            )
            db.session.commit()

            logger.debug("TimelineContext: run %s started (%s)", self.run.run_id, self.job_name)
        except Exception as e:
            logger.warning("TimelineContext.__enter__ failed (timeline disabled): %s", e)
            self._enabled = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._enabled or self.run is None:
            return False  # don't suppress caller's exception

        try:
            from lms_automation.services.run_timeline import complete_run, create_checkpoint
            from lms_automation.extensions import db

            if exc_type is not None:
                status = 'failed'
                error_info = f"{exc_type.__name__}: {exc_val}"
                # Append error to audit before committing so it's visible on failure.
                _safe_audit(
                    self.run.run_id,
                    'run_failed',
                    actor=self.actor,
                    organiser_id=self.organiser_id,
                    details={'error': error_info, 'job': self.job_name},
                )
            else:
                status = 'warn' if self._has_warnings else 'success'
                # End-of-run checkpoint (best effort — skip if it was already taken).
                try:
                    create_checkpoint(
                        run=self.run,
                        label=f'run_end_{self.job_name}',
                        organiser_id=self.organiser_id,
                    )
                except Exception:
                    pass

            complete_run(
                self.run,
                status=status,
                error_info=error_info if exc_type else None,
                total_actions=self._actions,
                affected_players=self._affected_players,
                affected_rounds=self._affected_rounds,
                affected_reminders=self._affected_reminders,
            )
            db.session.commit()
            logger.debug(
                "TimelineContext: run %s closed status=%s", self.run.run_id, status
            )
        except Exception as e:
            logger.warning("TimelineContext.__exit__ failed: %s", e)
            try:
                from lms_automation.extensions import db
                db.session.rollback()
            except Exception:
                pass

        return False  # never suppress caller exceptions

    # ------------------------------------------------------------------
    # Per-step helpers — all safe (log + return None on failure)
    # ------------------------------------------------------------------

    def checkpoint(self, label: str):
        """Take a state snapshot.  Safe — never raises."""
        if not self._enabled or self.run is None:
            return None
        try:
            from lms_automation.services.run_timeline import create_checkpoint
            from lms_automation.extensions import db
            cp = create_checkpoint(
                run=self.run,
                label=label,
                organiser_id=self.organiser_id,
            )
            db.session.commit()
            logger.debug("TimelineContext: checkpoint '%s' saved", label)
            return cp
        except Exception as e:
            logger.warning("TimelineContext.checkpoint('%s') failed: %s", label, e)
            return None

    def decision(
        self,
        title: str,
        rule_matched: str = '',
        reasoning: str = '',
        confidence: str = 'medium',
        intended_action: str = '',
        expected_impact: str = '',
        state: str = 'applied',
    ):
        """Record a decision step.  Safe — never raises."""
        if not self._enabled or self.run is None:
            return None
        self._step += 1
        try:
            from lms_automation.services.run_timeline import record_decision
            dec = record_decision(
                run=self.run,
                step_number=self._step,
                title=title,
                rule_matched=rule_matched,
                reasoning=reasoning,
                confidence=confidence,
                intended_action=intended_action,
                expected_impact=expected_impact,
                state=state,
            )
            return dec
        except Exception as e:
            logger.warning("TimelineContext.decision('%s') failed: %s", title, e)
            return None

    def action(
        self,
        action_type: str,
        outcome: str = 'applied',
        actual_impact: str = '',
        actor: Optional[str] = None,
        reversible: bool = True,
        reversal_metadata: Optional[dict] = None,
        decision=None,
        players_delta: int = 0,
        rounds_delta: int = 0,
        reminders_delta: int = 0,
    ):
        """Record a mutation outcome.  Safe — never raises.

        Use players_delta / rounds_delta / reminders_delta to accumulate counts
        that will be reported in complete_run.
        """
        if not self._enabled or self.run is None:
            return None
        try:
            from lms_automation.services.run_timeline import record_action
            from lms_automation.extensions import db
            act = record_action(
                run=self.run,
                action_type=action_type,
                outcome=outcome,
                actual_impact=actual_impact,
                actor=actor or self.actor,
                reversible=reversible,
                reversal_metadata=reversal_metadata,
                decision=decision,
            )
            db.session.commit()
            self._actions += 1
            self._affected_players += players_delta
            self._affected_rounds += rounds_delta
            self._affected_reminders += reminders_delta
            if outcome in ('failed', 'partial'):
                self._has_warnings = True
            return act
        except Exception as e:
            logger.warning("TimelineContext.action('%s') failed: %s", action_type, e)
            return None

    def warn(self, message: str = ''):
        """Mark the run as 'warn' status even if no exception is raised."""
        self._has_warnings = True
        if message:
            logger.warning("TimelineContext.warn: %s (run=%s)", message,
                           self.run.run_id if self.run else 'none')


# ---------------------------------------------------------------------------
# Internal helper — audit even when session may be dirty
# ---------------------------------------------------------------------------

def _safe_audit(
    run_id: Optional[str],
    event_type: str,
    actor: str = 'system',
    organiser_id: Optional[int] = None,
    details: Optional[dict] = None,
):
    """Append an audit event in a fresh, isolated commit attempt."""
    try:
        from lms_automation.services.run_timeline import append_audit_event
        from lms_automation.extensions import db
        append_audit_event(
            event_type=event_type,
            actor=actor,
            run_id=run_id,
            organiser_id=organiser_id,
            details=details or {},
        )
        db.session.commit()
    except Exception as e:
        logger.warning("_safe_audit failed for event_type=%s: %s", event_type, e)
        try:
            from lms_automation.extensions import db
            db.session.rollback()
        except Exception:
            pass
