# Elimination Fix - Verification Guide

## Root Causes Fixed

### 🔴 ROOT CAUSE #1: Token/Announcement Jobs Used `Player.status='active'` Globally
**Problem**: Jobs queried `Player.query.filter_by(status='active')` which ignored per-round eliminations tracked in `picks.is_eliminated`. This meant:
- Round 1 completes, sets `pick.is_eliminated=True` for losing picks
- Round 2 is created manually BEFORE elimination job updates `player.status='eliminated'`
- Token generation runs, queries `status='active'`, finds all 31 players → sends pick links to everyone

**Fix**: Created `_get_eligible_players_for_round()` helper that checks BOTH:
1. Global `player.status='active'` (not already globally eliminated/winner)
2. No prior `picks.is_eliminated=True` in this cycle

Updated these functions to use the helper:
- `generate_round_tokens()` (line 636)
- `send_new_round_announcements()` (line 519)
- `round_progression_orchestrator()` (line 791)
- `apply_missed_picks()` (line 724)

### 🔴 ROOT CAUSE #2: Elimination Processing Updated `player.status` But Commit Might Fail Silently
**Problem**: Elimination job updated `player.status='eliminated'` but if there were any issues before commit, changes wouldn't persist.

**Fix**:
- Added detailed player-level logging (shows who was eliminated)
- Added post-commit verification (counts active vs eliminated players)
- Uses existing commit at end of processing (line 375)

---

## SQL Verification Queries

### BEFORE FIX - Check Current State

```sql
-- 1. How many players show as active globally?
SELECT COUNT(*) as active_count
FROM players
WHERE status = 'active';
-- Expected before fix: 31 (all players still active)

-- 2. How many players were eliminated in Round 1 (per-round elimination)?
SELECT COUNT(DISTINCT player_id) as eliminated_in_round_1
FROM picks
WHERE round_id = (SELECT id FROM rounds WHERE round_number = 1)
  AND is_eliminated = TRUE;
-- Expected: ~7 (whoever lost Round 1)

-- 3. Show the mismatch: players with is_eliminated=TRUE but status='active'
SELECT
    p.id,
    p.name,
    p.status as player_status,
    pk.round_id,
    r.round_number,
    pk.is_eliminated as pick_eliminated,
    pk.is_winner,
    pk.team_picked
FROM players p
JOIN picks pk ON p.id = pk.player_id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.is_eliminated = TRUE
  AND p.status = 'active'
ORDER BY p.id, r.round_number;
-- Expected before fix: Shows players eliminated in Round 1 but still active

-- 4. How many tokens exist for Round 2?
SELECT COUNT(*) as round_2_token_count
FROM pick_tokens
WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2);
-- Expected before fix: 31 (all players got tokens)

-- 5. Show which players got Round 2 tokens (should only be Round 1 survivors)
SELECT
    pt.id as token_id,
    p.id as player_id,
    p.name,
    p.status,
    (SELECT COUNT(*)
     FROM picks pk
     WHERE pk.player_id = p.id
       AND pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
       AND pk.is_eliminated = TRUE
    ) as was_eliminated_in_round_1
FROM pick_tokens pt
JOIN players p ON pt.player_id = p.id
WHERE pt.round_id = (SELECT id FROM rounds WHERE round_number = 2)
ORDER BY p.name;
-- Expected before fix: All 31 players, including those with was_eliminated_in_round_1=1
```

---

### AFTER FIX - Verify Correct Behavior

**Deploy the fix, then manually delete Round 2 tokens to test:**

```sql
-- Clean up Round 2 tokens (if Round 2 was already created with wrong tokens)
DELETE FROM pick_tokens
WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2);

-- Manually trigger token generation job or wait for next run
```

**Then verify:**

```sql
-- 1. Check Round 2 tokens only go to eligible players
SELECT
    pt.id as token_id,
    p.id as player_id,
    p.name,
    p.status,
    (SELECT COUNT(*)
     FROM picks pk
     WHERE pk.player_id = p.id
       AND pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
       AND pk.is_eliminated = TRUE
    ) as was_eliminated_in_round_1
FROM pick_tokens pt
JOIN players p ON pt.player_id = p.id
WHERE pt.round_id = (SELECT id FROM rounds WHERE round_number = 2)
ORDER BY p.name;
-- Expected AFTER fix: Only shows players with was_eliminated_in_round_1=0

-- 2. Confirm eligible player count matches token count
SELECT
    (SELECT COUNT(DISTINCT player_id)
     FROM picks
     WHERE round_id = (SELECT id FROM rounds WHERE round_number = 1)
       AND is_eliminated = FALSE
    ) as eligible_players,
    (SELECT COUNT(*)
     FROM pick_tokens
     WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2)
    ) as tokens_created;
-- Expected AFTER fix: Both counts should match (e.g., 24 eligible, 24 tokens)

-- 3. List players who should be eligible for Round 2
SELECT
    p.id,
    p.name,
    p.status,
    COALESCE(pk.is_eliminated, FALSE) as eliminated_in_round_1,
    pk.team_picked,
    pk.is_winner
FROM players p
LEFT JOIN picks pk ON p.id = pk.player_id
    AND pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
WHERE p.status = 'active'
  AND (pk.is_eliminated IS NULL OR pk.is_eliminated = FALSE)
ORDER BY p.name;
-- Expected: Shows ~24 players (31 minus ~7 eliminated)

-- 4. List players who should NOT get Round 2 tokens
SELECT
    p.id,
    p.name,
    p.status,
    pk.is_eliminated,
    pk.team_picked,
    pk.is_winner
FROM players p
JOIN picks pk ON p.id = pk.player_id
WHERE pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
  AND pk.is_eliminated = TRUE
ORDER BY p.name;
-- Expected: Shows ~7 eliminated players
-- These players should NOT have tokens for Round 2
```

---

## Railway Log Verification

### Logs to Check After Deployment

#### 1. Token Generation Job (Runs Every 1 Hour)

**Look for:**
```
=== TOKEN GENERATION JOB START ===
Found X round(s) (pending/active) for token generation check
Eligibility check for Round 2: globally_active=31, eliminated_in_previous_rounds=7, eligible=24
Round 2: Processing 24 eligible player(s)
Created token for player 'John Doe' (id=3) - Round 2
...
Round 2: Created 24 new token(s)
=== TOKEN GENERATION COMPLETE: 24 token(s) created ===
```

**Red flags:**
- ❌ `eligible=31` (should be ~24, not 31)
- ❌ `Created 31 new token(s)` (means fix didn't work)
- ✅ `eligible=24` and `Created 24 tokens` (fix working!)

#### 2. Round Announcements Job (Runs Every 30 Minutes)

**Look for:**
```
=== ROUND ANNOUNCEMENT JOB START ===
Found 1 active round(s) for announcements
Eligibility check for Round 2: globally_active=31, eliminated_in_previous_rounds=7, eligible=24
Sent new round announcement to [survivor name]
...
Round 2 announcement summary: sent=24, skipped_missing_telegram=0
=== ROUND ANNOUNCEMENT JOB COMPLETE ===
```

**Red flags:**
- ❌ `sent=31` (should be ~24)
- ✅ `sent=24` (correct!)

#### 3. Elimination Processing Job (Runs Every 1 Hour)

**Look for:**
```
=== ELIMINATION PROCESSING JOB START ===
Found 1 active round(s) to check for elimination processing
Round 1: 10/10 fixtures completed
Round 1: Eliminated 7 player(s): Alice(id=1), Bob(id=2), ...
Round 1: Marked as COMPLETED
=== ELIMINATION PROCESSING COMPLETE: 1 round(s) processed, 7 player(s) eliminated ===
Current player status counts: active=24, eliminated=7
```

**What to verify:**
- Eliminated player count matches your expectation
- `active=24, eliminated=7` confirms `player.status` was updated
- Player names/IDs listed for transparency

#### 4. Orchestrator Job (Runs Every 10 Minutes)

**Look for:**
```
=== ROUND ORCHESTRATOR JOB START ===
Found 1 pending round(s)
Eligibility check for Round 2: globally_active=24, eliminated_in_previous_rounds=0, eligible=24
Round 2: 10 fixture(s), 24 eligible player(s), 24 token(s)
Round 2: ACTIVATED (has 24 tokens for 24 players)
=== ROUND ORCHESTRATOR JOB COMPLETE ===
```

**What to verify:**
- `eligible_players` count is correct (should be survivors from previous rounds)
- `globally_active` decreases as eliminations happen

---

## Manual Testing Steps

### Test 1: Clean Slate Test (If Possible)

1. **Reset Round 1 player statuses** (only if safe to do so):
   ```sql
   UPDATE players SET status = 'active' WHERE status = 'eliminated';
   ```

2. **Delete Round 2 tokens**:
   ```sql
   DELETE FROM pick_tokens WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2);
   ```

3. **Manually run elimination job** (via Railway console or wait for schedule):
   - Should see `Eliminated X player(s)` in logs
   - Verify `player.status='eliminated'` for losing players

4. **Manually run token generation job**:
   - Should create tokens ONLY for eligible players
   - Log should show `eligible=24` (not 31)

5. **Verify in database**:
   ```sql
   SELECT COUNT(*) FROM pick_tokens WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2);
   -- Should be ~24, not 31
   ```

### Test 2: Future Round Creation

1. **When Round 2 completes**, check elimination job logs
2. **Create Round 3 manually** via dashboard
3. **Wait for token generation job** (1 hour or trigger manually)
4. **Verify logs**:
   ```
   Eligibility check for Round 3: globally_active=X, eliminated_in_previous_rounds=Y, eligible=Z
   ```
   Where `Z = X - Y` (only survivors get tokens)

5. **Verify in database**:
   ```sql
   SELECT COUNT(*) FROM pick_tokens WHERE round_id = (SELECT id FROM rounds WHERE round_number = 3);
   -- Should match survivor count
   ```

---

## Edge Cases to Monitor

### Multiple Active Rounds

**Check for this log:**
```
Found 2 active round(s)
```

**If you see this:**
- ✅ Orchestrator will log both rounds separately
- ⚠️ Token generation will process both (but with correct eligibility for each)
- ⚠️ Announcements will send for both (but only to eligible players per round)

**Recommendation**: Only have ONE active round at a time. Mark previous round as `completed` before activating next.

### Round Created Before Previous Round Completes

**Scenario**: Admin creates Round 2 while Round 1 is still `active` (fixtures not all completed yet).

**What happens with fix:**
```
Eligibility check for Round 2: globally_active=31, eliminated_in_previous_rounds=0, eligible=31
```

**Why?**: No previous **completed** rounds exist yet, so no eliminations recorded.

**Solution**: Wait for Round 1 to complete (all fixtures `status='completed'`) before creating Round 2.

---

## Verification Checklist

### Immediate (After Deploy)

- [ ] Deployment succeeded in Railway
- [ ] No Python syntax errors in logs
- [ ] Scheduler started successfully

### Within 1 Hour (First Token Generation Run)

- [ ] Check logs for eligibility log: `eliminated_in_previous_rounds=X`
- [ ] Verify `X > 0` if Round 1 has eliminations
- [ ] Verify token count matches eligible count, not total player count

### Within 24 Hours (Full Round Cycle)

- [ ] Round 1 elimination job runs → logs show players eliminated
- [ ] Player status updated: `active=24, eliminated=7` (or similar)
- [ ] Round 2 token generation only creates tokens for survivors
- [ ] Announcements only sent to survivors

### SQL Verification

- [ ] Run "AFTER FIX" queries above
- [ ] Confirm Round 2 tokens only for eligible players
- [ ] Confirm eliminated players have `status='eliminated'` in DB

---

## Rollback Plan

If the fix causes issues:

1. **Revert `scheduler.py`**:
   ```bash
   git revert HEAD
   git push origin main
   ```

2. **Railway auto-deploys the rollback**

3. **Manually fix Round 2 tokens** (if needed):
   ```sql
   -- Delete tokens for eliminated players
   DELETE FROM pick_tokens pt
   WHERE pt.round_id = (SELECT id FROM rounds WHERE round_number = 2)
     AND pt.player_id IN (
       SELECT player_id FROM picks
       WHERE round_id = (SELECT id FROM rounds WHERE round_number = 1)
         AND is_eliminated = TRUE
     );
   ```

---

## Success Criteria

✅ **Immediate Success:**
- Token generation logs show `eligible=X` where `X < total_players`
- Eligibility check logs show `eliminated_in_previous_rounds > 0`

✅ **Short-Term Success (24 Hours):**
- Round 2 tokens = Round 1 survivors (not all players)
- Announcements only sent to survivors
- `player.status='eliminated'` persists in database

✅ **Long-Term Success (1 Week):**
- Each new round only messages eligible players
- No tokens created for eliminated players
- Picks Grid and player status stay in sync

---

**END OF VERIFICATION GUIDE**
