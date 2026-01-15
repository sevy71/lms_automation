-- Verification SQL for Cycle-Scoped Queries
-- Run these queries to verify the fix for cycle-scoped picks grid and used teams
--
-- TEST SCENARIO:
-- 1. Cycle 1 has rounds 1-6 with picks
-- 2. Winner declared, Start New Game creates Cycle 2 Round 1
-- 3. Verify used teams are empty for cycle 2
-- 4. Verify picks grid shows only cycle 2 data by default

-- ==============================================================
-- STEP 1: Verify current cycle state
-- ==============================================================

-- Check all cycles and their rounds
SELECT
    r.cycle_number,
    r.id AS round_id,
    r.round_number,
    r.status,
    r.pl_matchday,
    COUNT(p.id) AS pick_count
FROM rounds r
LEFT JOIN picks p ON p.round_id = r.id
GROUP BY r.cycle_number, r.id, r.round_number, r.status, r.pl_matchday
ORDER BY r.cycle_number, r.round_number;

-- ==============================================================
-- STEP 2: Verify cycle-scoped picks for a specific player
-- ==============================================================

-- Example: Check picks for player_id=1 in cycle 1 vs cycle 2
-- Replace player_id as needed

-- Cycle 1 picks for player 1
SELECT
    'Cycle 1' AS cycle,
    p.id AS pick_id,
    r.round_number,
    p.team_picked
FROM picks p
JOIN rounds r ON p.round_id = r.id
WHERE p.player_id = 1
  AND r.cycle_number = 1
ORDER BY r.round_number;

-- Cycle 2 picks for player 1 (should be empty or only new picks)
SELECT
    'Cycle 2' AS cycle,
    p.id AS pick_id,
    r.round_number,
    p.team_picked
FROM picks p
JOIN rounds r ON p.round_id = r.id
WHERE p.player_id = 1
  AND r.cycle_number = 2
ORDER BY r.round_number;

-- ==============================================================
-- STEP 3: Simulate "used teams" query (cycle-scoped)
-- ==============================================================

-- This is what the application now computes for the pick form
-- Given a player and a target round, compute used teams in THAT cycle only

-- Example: Player 1, target round in cycle 2
-- Should return 0 rows if player has no picks in cycle 2 yet
SELECT
    p.team_picked
FROM picks p
JOIN rounds r ON p.round_id = r.id
WHERE p.player_id = 1
  AND r.cycle_number = 2;

-- ==============================================================
-- STEP 4: Verify available cycles for picks grid
-- ==============================================================

SELECT DISTINCT cycle_number
FROM rounds
WHERE cycle_number IS NOT NULL
ORDER BY cycle_number;

-- ==============================================================
-- STEP 5: Verify current cycle detection logic
-- ==============================================================

-- Current cycle = cycle of active/pending round, or max cycle
SELECT
    CASE
        WHEN EXISTS (SELECT 1 FROM rounds WHERE status IN ('active', 'pending'))
        THEN (SELECT cycle_number FROM rounds WHERE status IN ('active', 'pending') ORDER BY id DESC LIMIT 1)
        ELSE (SELECT MAX(cycle_number) FROM rounds)
    END AS current_cycle;

-- ==============================================================
-- STEP 6: Data integrity check - no orphan picks
-- ==============================================================

-- All picks should have valid round associations
SELECT COUNT(*) AS orphan_picks
FROM picks p
WHERE NOT EXISTS (SELECT 1 FROM rounds r WHERE r.id = p.round_id);

-- ==============================================================
-- STEP 7: Round number check per cycle
-- ==============================================================

-- Verify each cycle starts with round_number=1
SELECT
    cycle_number,
    MIN(round_number) AS first_round,
    MAX(round_number) AS last_round,
    COUNT(*) AS total_rounds
FROM rounds
WHERE cycle_number IS NOT NULL
GROUP BY cycle_number
ORDER BY cycle_number;

-- Expected: Each cycle should have first_round=1
