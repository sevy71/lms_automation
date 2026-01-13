# Round State Machine Specification

This document defines the deterministic state machine for LMS round progression.

## States

| State | Description |
|-------|-------------|
| `pending` | Round created, awaiting fixtures/tokens/activation |
| `active` | Round accepting picks; players can submit/edit picks |
| `picks_locked` | All picks in OR deadline passed + autopick applied; no more edits (tracked via `special_note`) |
| `completed` | All fixtures finished AND eliminations processed |

## Transitions

```
┌──────────┐   fixtures + tokens    ┌─────────┐
│ pending  │ ─────────────────────► │ active  │
└──────────┘                        └────┬────┘
                                         │
                                         │ all picks OR
                                         │ (deadline + autopick)
                                         ▼
                                   ┌─────────────┐
                                   │ picks_locked │
                                   └──────┬──────┘
                                          │
                                          │ all fixtures completed
                                          │ AND eliminations processed
                                          ▼
                                    ┌───────────┐
                                    │ completed │
                                    └───────────┘
```

### Transition: pending → active

**WHEN:** `fixtures exist` AND `tokens generated for all eligible players`

**BY:** `round_progression_orchestrator` job

**Guards:**
- Fixtures must exist for the round
- Token count >= eligible player count

### Transition: active → picks_locked

**WHEN:** `(picks_count == eligible_players_count)` OR `(deadline_passed AND autopick_applied)`

**BY:** `check_all_picks_submitted` OR `apply_missed_picks`

**Implementation:** Adds `picks_locked` to `round.special_note`

**Guards:**
- Check `special_note` doesn't already contain `picks_locked`

### Transition: picks_locked → completed

**WHEN:** ALL of these are true:
1. All fixtures have `status='completed'`
2. Picks exist for every eligible player (manual or auto)
3. All picks have `is_winner` evaluated (not None)

**BY:** `process_eliminations` job

**Guards (NON-NEGOTIABLE):**
- GUARD 1: Fixtures exist for the round
- GUARD 2: All fixtures completed
- GUARD 3: picks_count >= eligible_count (or deadline passed + autopick ran)
- GUARD 4: All picks evaluated (is_winner is not None)

## Critical Invariants

1. **NEVER** mark round completed if `picks_count == 0` for eligible players
2. **NEVER** mark round completed if `picks_count < eligible_count` AND deadline not passed
3. **NEVER** mark round completed if any fixture is still `scheduled`/`live`/`postponed`
4. `is_winner=False` **MUST** always mean `is_eliminated=True` (enforced by invariant job)
5. Auto-picks **MUST** be applied before eliminations if deadline has passed

## Idempotency Rules

| Job | Idempotency Guard |
|-----|-------------------|
| `apply_missed_picks` | Only insert if no pick exists for `(player_id, round_id)` |
| `process_eliminations` | Only set `is_eliminated=True` if `is_winner=False` AND not already eliminated |
| `check_all_picks_submitted` | Only mark `picks_locked` and send notification once (check `special_note`) |
| `_send_picks_published_notification` | Check `picks_published` in `special_note` |
| `_send_round_results_notification` | Check `results_sent` in `special_note` |

## Scheduler Job Intervals (Default)

| Job | Interval | Guard Type |
|-----|----------|------------|
| `update_fixtures` | 5 min | Only updates changed fixtures |
| `sync_fixtures` | 30 min | Only syncs missing/changed fixtures |
| `process_eliminations` | 2 min | Full state machine guards |
| `apply_missed_picks` | 2 min | Only inserts missing picks |
| `check_all_picks_submitted` | 2 min | Only acts if all picks in |
| `round_orchestrator` | 5 min | Only activates ready rounds |
| `generate_tokens` | 10 min | Only creates missing tokens |
| `send_reminders` | 5 min | Uses `is_sent` flag |
| `send_round_announcements` | 10 min | Uses `special_note` marker |
| `enforce_invariant` | 30 min | Only corrects violations |

## Environment Variables

All intervals are configurable via environment variables:

```bash
ELIMINATION_INTERVAL_MINUTES=2      # process_eliminations
AUTOPICK_INTERVAL_MINUTES=2         # apply_missed_picks
PICKS_CHECK_INTERVAL_MINUTES=2      # check_all_picks_submitted
ORCHESTRATOR_INTERVAL_MINUTES=5     # round_progression_orchestrator
TOKEN_GENERATION_INTERVAL_MINUTES=10
REMINDER_INTERVAL_MINUTES=5
ANNOUNCEMENT_INTERVAL_MINUTES=10
FIXTURE_UPDATE_INTERVAL_MINUTES=5
FIXTURE_SYNC_INTERVAL_MINUTES=30
INVARIANT_CHECK_INTERVAL_MINUTES=30

# Fallback fixtures (disabled by default)
ENABLE_FALLBACK_FIXTURES=false
```

## DB Integrity Check

Use the integrity check helper to detect common issues:

```python
from lms_automation.scheduler import check_db_integrity, run_integrity_check_with_logging

# Get issues as dict
result = check_db_integrity()
if not result['ok']:
    for issue in result['issues']:
        print(issue)

# Or log directly
run_integrity_check_with_logging()
```

**Detected Issues:**
1. Rounds with 0 picks but status='completed'
2. Fallback fixtures (event_id like 'fallback_%')
3. Duplicate fixtures in same round
4. Picks referencing teams not in round fixtures
5. Elimination invariant violations (is_winner=False, is_eliminated=False)
