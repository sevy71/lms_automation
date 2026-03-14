"""
Run Timeline service.

Creates and manages automation run records, decisions, actions, checkpoints,
and audit events.  Call these helpers from scheduler jobs and admin handlers
to build the full timeline that admins can audit, roll back, or replay.

Usage example (in a scheduler job)::

    from lms_automation.services.run_timeline import (
        create_run, complete_run, record_decision, record_action,
        create_checkpoint, append_audit_event,
    )

    run = create_run(trigger_type='scheduler', mode='auto', organiser_id=1)
    cp  = create_checkpoint(run=run, label='Before round completion R3')
    dec = record_decision(run, step_number=1, title='Complete Round 3', ...)
    record_action(run, action_type='round_completed', outcome='applied', ...)
    complete_run(run, status='success')
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from lms_automation.extensions import db
from lms_automation.models import (
    AutomationRun, RunDecision, RunAction, RunCheckpoint, RunAuditEvent,
    Round, Player, Pick, PickToken,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Run lifecycle helpers
# ---------------------------------------------------------------------------

def create_run(
    trigger_type: str = 'scheduler',
    mode: str = 'auto',
    organiser_id: Optional[int] = None,
) -> AutomationRun:
    """Create and persist a new AutomationRun in 'running' state."""
    run = AutomationRun(
        run_id=str(uuid.uuid4()),
        trigger_type=trigger_type,
        mode=mode,
        organiser_id=organiser_id,
        started_at=datetime.utcnow(),
        status='running',
    )
    db.session.add(run)
    db.session.flush()  # get PK without full commit
    append_audit_event(
        event_type='run_started',
        actor='system',
        run_id=run.run_id,
        organiser_id=organiser_id,
        details={'trigger_type': trigger_type, 'mode': mode},
    )
    return run


def complete_run(
    run: AutomationRun,
    status: str = 'success',
    error_info: Optional[str] = None,
    total_actions: int = 0,
    affected_players: int = 0,
    affected_rounds: int = 0,
    affected_reminders: int = 0,
) -> None:
    """Mark a run as completed and persist counts."""
    run.status = status
    run.completed_at = datetime.utcnow()
    run.error_info = error_info
    run.total_actions = total_actions
    run.affected_players = affected_players
    run.affected_rounds = affected_rounds
    run.affected_reminders = affected_reminders
    append_audit_event(
        event_type='run_completed',
        actor='system',
        run_id=run.run_id,
        organiser_id=run.organiser_id,
        details={
            'status': status,
            'total_actions': total_actions,
            'affected_players': affected_players,
            'affected_rounds': affected_rounds,
        },
    )


# ---------------------------------------------------------------------------
# Decision + action recording
# ---------------------------------------------------------------------------

def record_decision(
    run: AutomationRun,
    step_number: int,
    title: str,
    rule_matched: str = '',
    reasoning: str = '',
    confidence: str = 'medium',
    intended_action: str = '',
    expected_impact: str = '',
    state: str = 'applied',
) -> RunDecision:
    """Persist a decision record and return it."""
    dec = RunDecision(
        run_id_fk=run.id,
        step_number=step_number,
        title=title,
        rule_matched=rule_matched,
        reasoning=reasoning,
        confidence=confidence,
        intended_action=intended_action,
        expected_impact=expected_impact,
        state=state,
        decided_at=datetime.utcnow(),
    )
    db.session.add(dec)
    db.session.flush()
    return dec


def record_action(
    run: AutomationRun,
    action_type: str,
    outcome: str = 'applied',
    actual_impact: str = '',
    actor: str = 'system',
    reversible: bool = True,
    reversal_metadata: Optional[dict] = None,
    decision: Optional[RunDecision] = None,
) -> RunAction:
    """Persist an action record and return it."""
    action = RunAction(
        run_id_fk=run.id,
        decision_id=decision.id if decision else None,
        action_type=action_type,
        outcome=outcome,
        actual_impact=actual_impact,
        actor=actor,
        reversible=reversible,
        reversal_metadata=json.dumps(reversal_metadata) if reversal_metadata else None,
        executed_at=datetime.utcnow(),
    )
    db.session.add(action)
    db.session.flush()
    return action


# ---------------------------------------------------------------------------
# Checkpoint creation + state snapshot
# ---------------------------------------------------------------------------

def _build_snapshot(organiser_id: Optional[int] = None) -> dict:
    """
    Capture current critical game state for checkpoint storage.

    Captures:
      - All rounds (status, special_note, cycle_number, round_number, pl_matchday)
      - All players (status, unreachable)
      - Picks for non-pending rounds (is_winner, is_eliminated, team_picked, auto_assigned)
      - PickToken validity flags for active rounds
    """
    snapshot: dict[str, Any] = {
        'captured_at': datetime.utcnow().isoformat(),
        'organiser_id': organiser_id,
        'rounds': [],
        'players': [],
        'picks': [],
        'pick_tokens': [],
    }

    rounds_q = Round.query
    players_q = Player.query
    if organiser_id:
        rounds_q = rounds_q.filter_by(organiser_id=organiser_id)
        players_q = players_q.filter_by(organiser_id=organiser_id)

    rounds = rounds_q.all()
    round_ids = [r.id for r in rounds]

    snapshot['rounds'] = [
        {
            'id': r.id,
            'round_number': r.round_number,
            'pl_matchday': r.pl_matchday,
            'status': r.status,
            'special_note': r.special_note,
            'special_measure': r.special_measure,
            'cycle_number': r.cycle_number,
            'first_kickoff_at': r.first_kickoff_at.isoformat() if r.first_kickoff_at else None,
        }
        for r in rounds
    ]

    snapshot['players'] = [
        {
            'id': p.id,
            'name': p.name,
            'status': p.status,
            'unreachable': p.unreachable,
        }
        for p in players_q.all()
    ]

    if round_ids:
        picks = Pick.query.filter(Pick.round_id.in_(round_ids)).all()
        snapshot['picks'] = [
            {
                'id': pk.id,
                'player_id': pk.player_id,
                'round_id': pk.round_id,
                'team_picked': pk.team_picked,
                'is_winner': pk.is_winner,
                'is_eliminated': pk.is_eliminated,
                'auto_assigned': pk.auto_assigned,
                'auto_reason': pk.auto_reason,
            }
            for pk in picks
        ]

        tokens = PickToken.query.filter(PickToken.round_id.in_(round_ids)).all()
        snapshot['pick_tokens'] = [
            {
                'id': t.id,
                'player_id': t.player_id,
                'round_id': t.round_id,
                'edit_count': t.edit_count,
                'is_used': t.is_used,
                'expires_at': t.expires_at.isoformat() if t.expires_at else None,
            }
            for t in tokens
        ]

    return snapshot


def create_checkpoint(
    run: Optional[AutomationRun] = None,
    label: str = 'Checkpoint',
    organiser_id: Optional[int] = None,
) -> RunCheckpoint:
    """
    Take a state snapshot and persist it as a RunCheckpoint.

    Pass run=None for standalone checkpoints not tied to a specific run.
    """
    snapshot = _build_snapshot(organiser_id=organiser_id)
    cp = RunCheckpoint(
        checkpoint_id=str(uuid.uuid4()),
        run_id_fk=run.id if run else None,
        organiser_id=organiser_id,
        label=label,
        created_at=datetime.utcnow(),
        snapshot=json.dumps(snapshot),
    )
    db.session.add(cp)
    db.session.flush()
    append_audit_event(
        event_type='checkpoint_created',
        actor='system',
        run_id=run.run_id if run else None,
        organiser_id=organiser_id,
        details={'checkpoint_id': cp.checkpoint_id, 'label': label},
    )
    return cp


# ---------------------------------------------------------------------------
# Audit event helper
# ---------------------------------------------------------------------------

def append_audit_event(
    event_type: str,
    actor: str = 'system',
    run_id: Optional[str] = None,
    organiser_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> RunAuditEvent:
    """Append an immutable audit event. Never mutates existing rows."""
    event = RunAuditEvent(
        run_id=run_id,
        organiser_id=organiser_id,
        event_type=event_type,
        actor=actor,
        details=json.dumps(details or {}),
        created_at=datetime.utcnow(),
    )
    db.session.add(event)
    db.session.flush()
    return event


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_recent_runs(organiser_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    """Return serialised summary of recent AutomationRuns, newest first."""
    q = AutomationRun.query.order_by(AutomationRun.started_at.desc()).limit(limit)
    if organiser_id:
        q = AutomationRun.query.filter_by(organiser_id=organiser_id).order_by(
            AutomationRun.started_at.desc()
        ).limit(limit)
    return [_serialise_run(r) for r in q.all()]


def get_run_detail(run_id: str) -> Optional[dict]:
    """Return full run detail including decisions and actions."""
    run = AutomationRun.query.filter_by(run_id=run_id).first()
    if not run:
        return None
    data = _serialise_run(run)
    data['decisions'] = [_serialise_decision(d) for d in run.decisions.order_by(RunDecision.step_number)]
    data['actions'] = [_serialise_action(a) for a in run.actions.order_by(RunAction.executed_at)]
    data['checkpoint_ids'] = [cp.checkpoint_id for cp in run.checkpoints]
    return data


def get_checkpoints(organiser_id: Optional[int] = None, limit: int = 20) -> list[dict]:
    """Return serialised list of checkpoints, newest first."""
    q = RunCheckpoint.query.order_by(RunCheckpoint.created_at.desc()).limit(limit)
    if organiser_id:
        q = RunCheckpoint.query.filter_by(organiser_id=organiser_id).order_by(
            RunCheckpoint.created_at.desc()
        ).limit(limit)
    return [_serialise_checkpoint(cp) for cp in q.all()]


def get_audit_events(
    run_id: Optional[str] = None,
    organiser_id: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return serialised audit events with optional filters."""
    q = RunAuditEvent.query
    if run_id:
        q = q.filter_by(run_id=run_id)
    if organiser_id:
        q = q.filter_by(organiser_id=organiser_id)
    if event_type:
        q = q.filter_by(event_type=event_type)
    q = q.order_by(RunAuditEvent.created_at.desc()).limit(limit)
    return [_serialise_audit_event(e) for e in q.all()]


# ---------------------------------------------------------------------------
# Rollback helpers
# ---------------------------------------------------------------------------

def _restore_from_snapshot(snapshot: dict, actor: str) -> dict:
    """
    Apply a snapshot dict back to the database.

    Returns a summary of what was changed.
    """
    summary = {
        'players_reverted': 0,
        'picks_reverted': 0,
        'rounds_reverted': 0,
    }

    # Restore player statuses
    player_map = {p['id']: p for p in snapshot.get('players', [])}
    for player_id, snap_player in player_map.items():
        player = Player.query.get(player_id)
        if player and (player.status != snap_player['status'] or
                       player.unreachable != snap_player['unreachable']):
            player.status = snap_player['status']
            player.unreachable = snap_player['unreachable']
            summary['players_reverted'] += 1

    # Restore picks
    pick_map = {p['id']: p for p in snapshot.get('picks', [])}
    for pick_id, snap_pick in pick_map.items():
        pick = Pick.query.get(pick_id)
        if pick:
            changed = (
                pick.is_winner != snap_pick['is_winner'] or
                pick.is_eliminated != snap_pick['is_eliminated'] or
                pick.team_picked != snap_pick['team_picked']
            )
            if changed:
                pick.is_winner = snap_pick['is_winner']
                pick.is_eliminated = snap_pick['is_eliminated']
                pick.team_picked = snap_pick['team_picked']
                pick.auto_assigned = snap_pick['auto_assigned']
                pick.auto_reason = snap_pick['auto_reason']
                summary['picks_reverted'] += 1

    # Restore round statuses and markers
    round_map = {r['id']: r for r in snapshot.get('rounds', [])}
    for round_id, snap_round in round_map.items():
        rnd = Round.query.get(round_id)
        if rnd and (rnd.status != snap_round['status'] or
                    rnd.special_note != snap_round['special_note']):
            rnd.status = snap_round['status']
            rnd.special_note = snap_round['special_note']
            rnd.special_measure = snap_round['special_measure']
            summary['rounds_reverted'] += 1

    return summary


def rollback_to_checkpoint(
    checkpoint_id: str,
    actor: str = 'system',
) -> dict:
    """
    Restore game state from a checkpoint snapshot.

    Always writes a new audit event — never silently mutates history.
    Returns a result dict with success flag and impact summary.
    """
    cp = RunCheckpoint.query.filter_by(checkpoint_id=checkpoint_id).first()
    if not cp:
        return {'success': False, 'error': f'Checkpoint {checkpoint_id} not found'}

    try:
        snapshot = json.loads(cp.snapshot)
    except (json.JSONDecodeError, TypeError) as e:
        return {'success': False, 'error': f'Invalid snapshot data: {e}'}

    try:
        summary = _restore_from_snapshot(snapshot, actor=actor)
        db.session.commit()

        append_audit_event(
            event_type='rollback_applied',
            actor=actor,
            run_id=None,
            organiser_id=cp.organiser_id,
            details={
                'checkpoint_id': checkpoint_id,
                'label': cp.label,
                'impact': summary,
            },
        )
        db.session.commit()

        logger.info(
            "Rollback to checkpoint %s completed: %s", checkpoint_id, summary
        )
        return {'success': True, 'checkpoint_id': checkpoint_id, 'impact': summary}

    except Exception as e:
        db.session.rollback()
        logger.error("Rollback to checkpoint %s failed: %s", checkpoint_id, e)

        try:
            append_audit_event(
                event_type='rollback_failed',
                actor=actor,
                organiser_id=cp.organiser_id,
                details={'checkpoint_id': checkpoint_id, 'error': str(e)},
            )
            db.session.commit()
        except Exception:
            pass

        return {'success': False, 'error': str(e)}


def rollback_last_action(organiser_id: Optional[int] = None, actor: str = 'system') -> dict:
    """
    Attempt to revert the most recent reversible RunAction.

    This is a best-effort rollback: it looks for the last action with a
    checkpoint available before it and restores that checkpoint.
    """
    last_run_q = AutomationRun.query.filter(
        AutomationRun.status.in_(['success', 'warn'])
    ).order_by(AutomationRun.completed_at.desc())
    if organiser_id:
        last_run_q = last_run_q.filter_by(organiser_id=organiser_id)
    last_run = last_run_q.first()

    if not last_run:
        return {'success': False, 'error': 'No completed run found to roll back'}

    # Find the most recent checkpoint associated with that run
    cp = (RunCheckpoint.query
          .filter_by(run_id_fk=last_run.id)
          .order_by(RunCheckpoint.created_at.desc())
          .first())
    if not cp:
        return {'success': False, 'error': 'No checkpoint available for last run'}

    return rollback_to_checkpoint(cp.checkpoint_id, actor=actor)


def rollback_last_run(organiser_id: Optional[int] = None, actor: str = 'system') -> dict:
    """Roll back the entire last completed run to its start-of-run checkpoint."""
    last_run_q = AutomationRun.query.filter(
        AutomationRun.status.in_(['success', 'warn'])
    ).order_by(AutomationRun.completed_at.desc())
    if organiser_id:
        last_run_q = last_run_q.filter_by(organiser_id=organiser_id)
    last_run = last_run_q.first()

    if not last_run:
        return {'success': False, 'error': 'No completed run found to roll back'}

    # Use the earliest checkpoint for the run (start-of-run snapshot)
    cp = (RunCheckpoint.query
          .filter_by(run_id_fk=last_run.id)
          .order_by(RunCheckpoint.created_at.asc())
          .first())
    if not cp:
        return {'success': False, 'error': 'No start-of-run checkpoint found'}

    result = rollback_to_checkpoint(cp.checkpoint_id, actor=actor)
    if result['success']:
        last_run.status = 'rolled-back'
        db.session.commit()
    return result


def replay_from_checkpoint(
    checkpoint_id: str,
    dry_run: bool = True,
    actor: str = 'system',
) -> dict:
    """
    Replay automation logic from a checkpoint state.

    Dry run:  restore snapshot to a temporary in-memory dict, run the
              evaluation logic, and return a preview report without any DB writes.

    Apply:    restore snapshot to DB, create a new run record, and trigger
              the manual process-results pipeline on the restored state.

    Returns a result dict with the preview or new run_id.
    """
    cp = RunCheckpoint.query.filter_by(checkpoint_id=checkpoint_id).first()
    if not cp:
        return {'success': False, 'error': f'Checkpoint {checkpoint_id} not found'}

    try:
        snapshot = json.loads(cp.snapshot)
    except (json.JSONDecodeError, TypeError) as e:
        return {'success': False, 'error': f'Invalid snapshot data: {e}'}

    if dry_run:
        # Dry-run: compute what would change without writing
        preview = _compute_replay_preview(snapshot)
        append_audit_event(
            event_type='replay_dry_run',
            actor=actor,
            organiser_id=cp.organiser_id,
            details={
                'checkpoint_id': checkpoint_id,
                'label': cp.label,
                'preview': preview,
            },
        )
        db.session.commit()
        return {'success': True, 'dry_run': True, 'preview': preview}

    # Apply replay: restore state → run the post-restore pipeline with full timeline tracking
    try:
        # Step 1 — restore snapshot
        restore_summary = _restore_from_snapshot(snapshot, actor=actor)
        db.session.commit()

        # Step 2 — open a new replay run (own timeline)
        from lms_automation.services.timeline_context import TimelineContext
        new_tl = TimelineContext(
            'replay_from_checkpoint',
            trigger_type='replay',
            mode='manual',
            organiser_id=cp.organiser_id,
            actor=actor,
        )
        new_tl.__enter__()

        # Step 3 — invoke the post-restore processing pipeline.
        # We replicate the core logic of manual_process_results so that replay
        # is a proper execution (with decisions/actions) rather than just a
        # state restore.
        pipeline_result = _run_post_restore_pipeline(
            tl=new_tl,
            organiser_id=cp.organiser_id,
            actor=actor,
        )

        # Step 4 — close the replay run
        new_tl.__exit__(None, None, None)
        new_run_id = new_tl.run.run_id if new_tl.run else None

        append_audit_event(
            event_type='replay_applied',
            actor=actor,
            run_id=new_run_id,
            organiser_id=cp.organiser_id,
            details={
                'checkpoint_id': checkpoint_id,
                'label': cp.label,
                'restore_impact': restore_summary,
                'pipeline_result': pipeline_result,
                'new_run_id': new_run_id,
            },
        )
        db.session.commit()

        return {
            'success': True,
            'dry_run': False,
            'new_run_id': new_run_id,
            'restore_impact': restore_summary,
            'pipeline_result': pipeline_result,
        }

    except Exception as e:
        db.session.rollback()
        logger.error("Replay from checkpoint %s failed: %s", checkpoint_id, e)
        try:
            append_audit_event(
                event_type='replay_failed',
                actor=actor,
                organiser_id=cp.organiser_id,
                details={'checkpoint_id': checkpoint_id, 'error': str(e)},
            )
            db.session.commit()
        except Exception:
            pass
        return {'success': False, 'error': str(e)}


def _compute_replay_preview(snapshot: dict) -> dict:
    """
    Compute what a replay would change without writing to the DB.

    Compares snapshot state against current DB state.
    """
    preview = {
        'players_that_would_change': [],
        'rounds_that_would_change': [],
        'picks_that_would_change': 0,
    }

    for snap_player in snapshot.get('players', []):
        current = Player.query.get(snap_player['id'])
        if current and current.status != snap_player['status']:
            preview['players_that_would_change'].append({
                'id': current.id,
                'name': current.name,
                'current_status': current.status,
                'snapshot_status': snap_player['status'],
            })

    for snap_round in snapshot.get('rounds', []):
        current = Round.query.get(snap_round['id'])
        if current and (current.status != snap_round['status'] or
                        current.special_note != snap_round['special_note']):
            preview['rounds_that_would_change'].append({
                'id': current.id,
                'round_number': current.round_number,
                'current_status': current.status,
                'snapshot_status': snap_round['status'],
            })

    for snap_pick in snapshot.get('picks', []):
        current = Pick.query.get(snap_pick['id'])
        if current and (current.is_winner != snap_pick['is_winner'] or
                        current.is_eliminated != snap_pick['is_eliminated']):
            preview['picks_that_would_change'] += 1

    return preview


# ---------------------------------------------------------------------------
# Post-restore processing pipeline (used by replay apply)
# ---------------------------------------------------------------------------

def _run_post_restore_pipeline(
    tl,
    organiser_id: Optional[int],
    actor: str,
) -> dict:
    """
    Run the core elimination-evaluation logic on current DB state.

    This replicates the essential decision loop from process_eliminations so
    that a replay produces a proper timeline (decisions + actions) rather than
    just a silent state restore.

    Returns a summary dict.
    """
    from lms_automation.team_utils import normalize_team_name
    from lms_automation.eligibility import get_eligible_players_for_round
    from lms_automation.services.markers import add_marker

    summary = {
        'rounds_processed': 0,
        'eliminations': 0,
        'survivors': 0,
        'skipped': 0,
    }
    step = 0

    active_rounds = Round.query.filter_by(status='active').all()
    for round_obj in active_rounds:
        fixtures = round_obj.fixtures
        picks = Pick.query.filter_by(round_id=round_obj.id).all()
        eligible = get_eligible_players_for_round(round_obj)

        if not fixtures or not picks or len(picks) < len(eligible):
            summary['skipped'] += 1
            continue

        all_complete = all(f.status == 'completed' for f in fixtures)
        if not all_complete:
            summary['skipped'] += 1
            continue

        step += 1
        tl.checkpoint(f'replay_before_r{round_obj.round_number}')
        tl.decision(
            title=f'Replay: evaluate picks for Round {round_obj.round_number}',
            rule_matched='replay_all_fixtures_complete',
            reasoning=f'{len(picks)} picks, {len(fixtures)} completed fixtures',
            confidence='high',
            intended_action='evaluate_picks_mark_round_completed',
            expected_impact=f'up to {len(picks)} picks evaluated',
        )

        fixture_outcomes: dict[str, bool] = {}
        for fx in fixtures:
            if fx.home_score is None or fx.away_score is None:
                continue
            home = normalize_team_name(fx.home_team)
            away = normalize_team_name(fx.away_team)
            if fx.home_score > fx.away_score:
                fixture_outcomes[home], fixture_outcomes[away] = True, False
            elif fx.away_score > fx.home_score:
                fixture_outcomes[home], fixture_outcomes[away] = False, True
            else:
                fixture_outcomes[home] = fixture_outcomes[away] = False

        elim_count = 0
        surv_count = 0
        for pick in picks:
            pick_norm = normalize_team_name(pick.team_picked)
            result = fixture_outcomes.get(pick_norm, False)
            pick.is_winner = result
            if result:
                if pick.player.status != 'active':
                    pick.player.status = 'active'
                pick.is_eliminated = False
                surv_count += 1
            else:
                if not pick.is_eliminated:
                    pick.is_eliminated = True
                    pick.player.status = 'eliminated'
                    elim_count += 1

        round_obj.status = 'completed'
        add_marker(round_obj, 'results_sent')
        db.session.commit()

        tl.action(
            action_type='replay_round_completed',
            outcome='applied',
            actual_impact=f'round_id={round_obj.id} r{round_obj.round_number}: elim={elim_count} surv={surv_count}',
            actor=actor,
            reversible=True,
            players_delta=elim_count,
            rounds_delta=1,
        )
        summary['rounds_processed'] += 1
        summary['eliminations'] += elim_count
        summary['survivors'] += surv_count

    return summary


# ---------------------------------------------------------------------------
# Data retention
# ---------------------------------------------------------------------------

def prune_old_data(
    checkpoint_days: Optional[int] = None,
    audit_days: Optional[int] = None,
    run_days: Optional[int] = None,
) -> dict:
    """
    Prune old timeline records per retention policy.

    Environment variables (override defaults):
      RUN_TIMELINE_CHECKPOINT_RETENTION_DAYS  default 30
      RUN_TIMELINE_AUDIT_RETENTION_DAYS       default 0 (never prune)
      RUN_TIMELINE_RUN_RETENTION_DAYS         default 0 (never prune)

    Returns counts of deleted rows per table.
    """
    import os
    from datetime import timedelta

    if checkpoint_days is None:
        checkpoint_days = int(os.environ.get('RUN_TIMELINE_CHECKPOINT_RETENTION_DAYS', 30))
    if audit_days is None:
        audit_days = int(os.environ.get('RUN_TIMELINE_AUDIT_RETENTION_DAYS', 0))
    if run_days is None:
        run_days = int(os.environ.get('RUN_TIMELINE_RUN_RETENTION_DAYS', 0))

    cutoff = datetime.utcnow()
    deleted: dict[str, int] = {}

    if checkpoint_days > 0:
        threshold = cutoff - timedelta(days=checkpoint_days)
        q = RunCheckpoint.query.filter(RunCheckpoint.created_at < threshold)
        count = q.count()
        q.delete(synchronize_session=False)
        deleted['checkpoints'] = count
        logger.info("Retention: pruned %d checkpoint(s) older than %d days", count, checkpoint_days)

    if audit_days > 0:
        threshold = cutoff - timedelta(days=audit_days)
        q = RunAuditEvent.query.filter(RunAuditEvent.created_at < threshold)
        count = q.count()
        q.delete(synchronize_session=False)
        deleted['audit_events'] = count
        logger.info("Retention: pruned %d audit event(s) older than %d days", count, audit_days)

    if run_days > 0:
        threshold = cutoff - timedelta(days=run_days)
        # Only prune completed runs (never prune 'running' or 'failed' without audit)
        q = AutomationRun.query.filter(
            AutomationRun.completed_at < threshold,
            AutomationRun.status.in_(['success', 'warn', 'rolled-back']),
        )
        count = q.count()
        q.delete(synchronize_session=False)
        deleted['runs'] = count
        logger.info("Retention: pruned %d run(s) older than %d days", count, run_days)

    if deleted:
        db.session.commit()

    return deleted


# ---------------------------------------------------------------------------
# Serialisers (DB model → JSON-safe dict)
# ---------------------------------------------------------------------------

def _serialise_run(run: AutomationRun) -> dict:
    return {
        'id': run.id,
        'run_id': run.run_id,
        'organiser_id': run.organiser_id,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'completed_at': run.completed_at.isoformat() if run.completed_at else None,
        'trigger_type': run.trigger_type,
        'mode': run.mode,
        'status': run.status,
        'total_actions': run.total_actions,
        'affected_players': run.affected_players,
        'affected_rounds': run.affected_rounds,
        'affected_reminders': run.affected_reminders,
        'error_info': run.error_info,
    }


def _serialise_decision(d: RunDecision) -> dict:
    return {
        'id': d.id,
        'step_number': d.step_number,
        'title': d.title,
        'rule_matched': d.rule_matched,
        'reasoning': d.reasoning,
        'confidence': d.confidence,
        'intended_action': d.intended_action,
        'expected_impact': d.expected_impact,
        'state': d.state,
        'decided_at': d.decided_at.isoformat() if d.decided_at else None,
    }


def _serialise_action(a: RunAction) -> dict:
    return {
        'id': a.id,
        'action_type': a.action_type,
        'outcome': a.outcome,
        'actual_impact': a.actual_impact,
        'actor': a.actor,
        'reversible': a.reversible,
        'executed_at': a.executed_at.isoformat() if a.executed_at else None,
    }


def _serialise_checkpoint(cp: RunCheckpoint) -> dict:
    try:
        snap = json.loads(cp.snapshot)
        players_count = len(snap.get('players', []))
        rounds_count = len(snap.get('rounds', []))
        picks_count = len(snap.get('picks', []))
        summary = f'{players_count} players, {rounds_count} rounds, {picks_count} picks'
    except Exception:
        summary = 'Snapshot available'
    return {
        'id': cp.id,
        'checkpoint_id': cp.checkpoint_id,
        'run_id_fk': cp.run_id_fk,
        'organiser_id': cp.organiser_id,
        'label': cp.label,
        'created_at': cp.created_at.isoformat() if cp.created_at else None,
        'snapshot_summary': summary,
    }


def _serialise_audit_event(e: RunAuditEvent) -> dict:
    try:
        details = json.loads(e.details) if e.details else {}
    except Exception:
        details = {}
    return {
        'id': e.id,
        'run_id': e.run_id,
        'organiser_id': e.organiser_id,
        'event_type': e.event_type,
        'actor': e.actor,
        'details': details,
        'created_at': e.created_at.isoformat() if e.created_at else None,
    }
