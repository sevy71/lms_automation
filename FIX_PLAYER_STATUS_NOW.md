# Fix Player Status Display Issue - IMMEDIATE ACTION

## Problem
Picks Grid shows all players as "active" even though eliminations are visible on individual picks.

## Root Cause
The **elimination processing job hasn't run yet** OR **Round 1 isn't marked as 'completed'**, so `player.status` hasn't been updated from 'active' to 'eliminated'.

The Picks Grid shows:
- **Individual pick eliminations**: ✅ Correct (from `picks.is_eliminated`)
- **Player status column**: ❌ Wrong (from `players.status` = 'active' for everyone)

---

## IMMEDIATE FIX (Choose One Method)

### Method 1: SQL Fix (Fastest - 30 seconds)

**Run this in Railway Postgres console:**

```sql
-- Update player statuses based on picks.is_eliminated
UPDATE players
SET status = 'eliminated'
WHERE id IN (
    SELECT DISTINCT player_id
    FROM picks
    WHERE is_eliminated = TRUE
)
AND status != 'eliminated';

-- Verify the fix
SELECT
    status,
    COUNT(*) as count
FROM players
GROUP BY status;
```

**Expected output:**
```
status      | count
------------|------
active      | 24    (or however many survived Round 1)
eliminated  | 7     (or however many lost Round 1)
```

**Then refresh the Picks Grid** - player statuses should now be correct.

---

### Method 2: Python Script (More Detailed Logging)

**Run this in Railway console:**

```bash
python manual_fix_player_statuses.py
```

**OR if Railway uses Python 3:**
```bash
python3 manual_fix_player_statuses.py
```

This will show:
- Current player status distribution
- Which players are being updated
- Final status distribution
- List of eliminated players

---

### Method 3: Full SQL Script with Diagnostics

**Use `IMMEDIATE_FIX.sql`** (just pushed to repo)

This shows:
- Before/after status counts
- Which players should be eliminated
- Verification queries

```bash
# In Railway, copy contents of IMMEDIATE_FIX.sql and paste into Postgres console
```

---

## Why This Happened

The elimination job (`process_eliminations` in scheduler.py) runs every **1 hour** and only processes rounds with:
1. `status = 'active'`
2. ALL fixtures `status = 'completed'`

If Round 1 isn't marked 'completed' yet, or the job hasn't run since fixtures completed, player statuses won't be updated.

---

## After Running the Fix

1. **Refresh Picks Grid** - Should show correct player statuses
2. **Check Round 1 status**:
   ```sql
   SELECT id, round_number, status FROM rounds WHERE round_number = 1;
   ```
   - If `status != 'completed'`, the elimination job hasn't run yet
   - Consider manually marking it completed if all fixtures are done

3. **Verify no Round 2 tokens for eliminated players**:
   ```sql
   SELECT COUNT(*)
   FROM pick_tokens pt
   JOIN picks pk ON pt.player_id = pk.player_id
   WHERE pt.round_id = (SELECT id FROM rounds WHERE round_number = 2)
     AND pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
     AND pk.is_eliminated = TRUE;
   ```
   - Should return **0** (no tokens for eliminated players)
   - If > 0, the earlier fix prevented this for future rounds, but Round 2 tokens exist from before the fix

---

## Delete Round 2 Tokens for Eliminated Players (If Needed)

If Round 2 already has tokens for eliminated players:

```sql
-- Delete tokens for players eliminated in Round 1
DELETE FROM pick_tokens
WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2)
  AND player_id IN (
    SELECT player_id
    FROM picks
    WHERE round_id = (SELECT id FROM rounds WHERE round_number = 1)
      AND is_eliminated = TRUE
  );

-- Verify
SELECT COUNT(*) FROM pick_tokens WHERE round_id = (SELECT id FROM rounds WHERE round_number = 2);
-- Should show only survivor count (e.g., 24)
```

---

## Verify Elimination Job Configuration

Check Railway logs for:

```
=== ELIMINATION PROCESSING JOB START ===
Round 1: X/Y fixtures completed
```

If you see `Not all fixtures completed yet, skipping`, that's why player statuses weren't updated.

**To trigger manually** (if all Round 1 fixtures are completed):

```sql
-- Mark Round 1 as completed (if fixtures are done)
UPDATE rounds
SET status = 'completed'
WHERE round_number = 1
  AND (SELECT COUNT(*) FROM fixtures WHERE round_id = rounds.id AND status = 'completed') =
      (SELECT COUNT(*) FROM fixtures WHERE round_id = rounds.id);
```

Then wait for the next elimination job run (runs every 1 hour) or restart the scheduler.

---

## Going Forward

The earlier fix (`_get_eligible_players_for_round()` helper) ensures:
- ✅ Round 3+ will ONLY send tokens/messages to survivors
- ✅ Works even if `player.status` isn't updated yet
- ✅ Checks per-round eliminations from `picks.is_eliminated`

But for the **Picks Grid player status column** to be correct, you need:
- ✅ This immediate fix (one-time)
- ✅ Elimination job to run on future rounds

---

## Summary of Actions

### Immediate (Do Now):
1. Run **Method 1 SQL fix** above in Railway Postgres console
2. Refresh Picks Grid → verify player statuses are correct

### Optional (If Round 2 Already Has Wrong Tokens):
3. Run "Delete Round 2 tokens" SQL above
4. Wait for token generation job (1 hour) or trigger manually to recreate correct tokens

### Verify (Within 1 Hour):
5. Check elimination job logs show it's running
6. Check Round 1 is marked `status='completed'`

---

## Quick Verification SQL

```sql
-- 1. Player status counts
SELECT status, COUNT(*) FROM players GROUP BY status;

-- 2. Round statuses
SELECT round_number, status FROM rounds ORDER BY round_number;

-- 3. Eliminated players with their rounds
SELECT
    p.name,
    p.status,
    r.round_number,
    pk.team_picked,
    pk.is_eliminated
FROM players p
JOIN picks pk ON p.id = pk.player_id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.is_eliminated = TRUE
ORDER BY p.name, r.round_number;
```

---

**Run the SQL fix now, then check the Picks Grid. Should be fixed immediately!**
