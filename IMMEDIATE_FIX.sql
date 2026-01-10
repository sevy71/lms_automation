-- ============================================================================
-- IMMEDIATE FIX: Update player.status based on picks.is_eliminated
-- ============================================================================
-- Run this in Railway Postgres console to immediately fix player statuses
--
-- This updates all players who have is_eliminated=TRUE picks to status='eliminated'
--
-- SAFE TO RUN: This only updates players who should already be eliminated
-- ============================================================================

-- Step 1: See current state BEFORE fix
SELECT
    'BEFORE FIX' as step,
    status,
    COUNT(*) as count
FROM players
GROUP BY status
ORDER BY status;

-- Step 2: Show players who SHOULD be eliminated but aren't
SELECT
    'SHOULD BE ELIMINATED' as step,
    p.id,
    p.name,
    p.status as current_status,
    COUNT(DISTINCT pk.round_id) as rounds_eliminated_in
FROM players p
JOIN picks pk ON p.id = pk.player_id
WHERE pk.is_eliminated = TRUE
  AND p.status != 'eliminated'
GROUP BY p.id, p.name, p.status
ORDER BY p.name;

-- Step 3: UPDATE player statuses (THE FIX)
UPDATE players
SET status = 'eliminated'
WHERE id IN (
    SELECT DISTINCT player_id
    FROM picks
    WHERE is_eliminated = TRUE
)
AND status != 'eliminated';

-- Step 4: See current state AFTER fix
SELECT
    'AFTER FIX' as step,
    status,
    COUNT(*) as count
FROM players
GROUP BY status
ORDER BY status;

-- Step 5: Verify - show eliminated players
SELECT
    'ELIMINATED PLAYERS' as step,
    p.id,
    p.name,
    p.status,
    MIN(r.round_number) as first_eliminated_in_round,
    STRING_AGG(pk.team_picked || ' (R' || r.round_number || ')', ', ' ORDER BY r.round_number) as elimination_details
FROM players p
JOIN picks pk ON p.id = pk.player_id
JOIN rounds r ON pk.round_id = r.id
WHERE p.status = 'eliminated'
GROUP BY p.id, p.name, p.status
ORDER BY first_eliminated_in_round, p.name;

-- ============================================================================
-- EXPECTED RESULTS:
-- - BEFORE FIX: All players show status='active'
-- - AFTER FIX: Eliminated players show status='eliminated'
-- - Picks Grid will immediately show correct player statuses
-- ============================================================================
