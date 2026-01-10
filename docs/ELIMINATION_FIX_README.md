# LMS Elimination Logic Fix

## Summary of Issues Fixed

### Issue 1: Team Name Mismatch
**Problem**: Fixtures stored team names like "Aston Villa FC" from the Football-Data API, but comparison used exact string matching. This caused picks with different name variants ("Aston Villa", "Villa") to not match fixtures.

**Fix**: Created `lms_automation/team_utils.py` with centralized team name normalization:
- `normalize_team_name()`: Converts any variant to canonical form
- `teams_match()`: Checks if two names refer to the same team
- `display_name()`: Gets short display name for UI

### Issue 2: Draws Not Eliminating Players
**Problem**: The `_update_picks_for_fixture()` method set `is_winner=False` for draws but didn't clearly indicate elimination. The elimination job relied on this flag but the matching was broken.

**Fix**: Updated `_update_picks_for_fixture()` to:
- Use normalized team name comparison
- Explicitly log "DRAW IS NOT A WIN" when setting `is_winner=False`
- Add comprehensive logging for debugging

### Issue 3: Auto-Pick Giving Same Team to Everyone
**Problem**: `_get_auto_pick_team()` returned `sorted(available)[0]` - the first team alphabetically. Since "AFC Bournemouth" is first alphabetically, all players got Bournemouth.

**Fix**: Changed selection algorithm to use `hash(player_id + round_id)` to distribute picks deterministically but fairly across available teams.

## Files Changed

### New Files
1. `lms_automation/team_utils.py` - Centralized team name normalization
2. `scripts/backfill_and_verify.py` - Data verification and backfill script
3. `scripts/verify_elimination_fix.sql` - SQL verification queries
4. `docs/ELIMINATION_FIX_README.md` - This documentation

### Modified Files
1. `lms_automation/scheduler.py`:
   - Import `team_utils`
   - Updated `_update_picks_for_fixture()` for normalized matching
   - Updated `process_eliminations()` with comprehensive logging
   - Updated `_get_auto_pick_team()` with distributed selection

2. `lms_automation/app.py`:
   - Import `team_utils`
   - Updated `team_abbrev()` to use centralized display names
   - Updated pick submission route to use normalized team comparison

## Verification Checklist

### Step 1: Run Syntax Checks
```bash
python3 -m py_compile lms_automation/team_utils.py
python3 -m py_compile lms_automation/scheduler.py
python3 -m py_compile lms_automation/app.py
python3 -m py_compile scripts/backfill_and_verify.py
```

### Step 2: Run Verification Script (Dry Run)
```bash
python scripts/backfill_and_verify.py --dry-run
```

This will show:
- Aston Villa vs Crystal Palace fixture status
- All Villa picks and their current status
- Pick result distribution
- Player status distribution
- What changes would be made

### Step 3: Apply Backfill (if needed)
```bash
python scripts/backfill_and_verify.py --apply
```

### Step 4: Run SQL Verification
Execute queries from `scripts/verify_elimination_fix.sql` to verify:

**Query 6 (CRITICAL)**: Should return ZERO rows
```sql
-- Find picks that are losers but not eliminated
SELECT * FROM picks pk
JOIN players p ON pk.player_id = p.id
WHERE pk.is_winner = false AND pk.is_eliminated = false;
```

**Query 9**: Auto-pick distribution should show variety
```sql
-- Check auto-pick team distribution
SELECT r.round_number, pk.team_picked, COUNT(*) as pick_count
FROM picks pk
JOIN rounds r ON pk.round_id = r.id
WHERE pk.auto_assigned = true
GROUP BY r.round_number, pk.team_picked
ORDER BY r.round_number, pick_count DESC;
```

### Step 5: Check Logs After Next Fixture Update

When fixtures update, you should see structured logs like:

```
=== FIXTURE UPDATE JOB START ===
PICK RESULT: player_id=42 picked 'Aston Villa FC' -> ELIMINATED (draw 2-2) - DRAW IS NOT A WIN
Fixture Aston Villa FC 2-2 Crystal Palace FC: outcome=draw, home_picks_count=3, away_picks_count=1, picks_updated=4
```

When elimination processing runs:

```
=== ELIMINATION PROCESSING JOB START ===
Processing Round 1 (round_id=1)
  fixtures_total=10, fixtures_completed=10, fixtures_draw=1
  picks_total=25
  ROUND 1 SUMMARY: picks_evaluated=25, winners=20, eliminated=5, unmatched=0
  Eliminated players: John(id=5, pick=Aston Villa FC), Jane(id=8, pick=Crystal Palace FC)
```

## Expected Log Lines to Verify Fix

### For Draw Handling
```
PICK RESULT: player_id=X picked 'Aston Villa FC' -> ELIMINATED (draw X-X) - DRAW IS NOT A WIN
```

### For Auto-Pick Distribution
```
Auto-pick for player_id=X (PlayerName): teams_in_round=20, teams_used=1, available=19
Auto-pick selected: player_id=X -> 'Liverpool FC' (canonical: 'Liverpool', index=7/19)
```

### For Unmatched Pick Detection
```
UNMATCHED PICK: player_id=X (PlayerName) picked 'Unknown Team' (canonical: 'Unknown Team') which doesn't match any fixture team - marking as LOSS
```

## Competition Rule Reminder

**ONLY A WIN PROGRESSES. DRAW OR LOSS = ELIMINATED.**

This is enforced in `_update_picks_for_fixture()`:
- Home win → home team picks get `is_winner=True`
- Away win → away team picks get `is_winner=True`
- **Draw → BOTH teams' picks get `is_winner=False`**
- Loss → losing team picks get `is_winner=False`

Then `process_eliminations()` marks all picks with `is_winner=False` as `is_eliminated=True` and updates `player.status='eliminated'`.
