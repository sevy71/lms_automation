-- ============================================================================
-- LEAN & CLEAN MESSAGING POLICY - SQL Verification Queries
-- ============================================================================
-- Run these queries on your production database (Railway) to verify messaging policy

-- ============================================================================
-- 1. PRE-DEPLOYMENT: Current State Snapshot
-- ============================================================================

-- 1a. Count players by status
SELECT
    status,
    COUNT(*) as count
FROM players
GROUP BY status
ORDER BY status;

-- 1b. Count players with telegram_id by status
SELECT
    status,
    COUNT(*) as total,
    SUM(CASE WHEN telegram_id IS NOT NULL AND telegram_id != '' THEN 1 ELSE 0 END) as has_telegram
FROM players
GROUP BY status
ORDER BY status;

-- 1c. List active rounds
SELECT
    id as round_id,
    round_number,
    status,
    first_kickoff_at,
    special_note
FROM rounds
WHERE status = 'active'
ORDER BY round_number;

-- 1d. Count picks per round with auto-pick breakdown
SELECT
    r.round_number,
    r.status as round_status,
    COUNT(p.id) as total_picks,
    SUM(CASE WHEN p.auto_assigned = TRUE THEN 1 ELSE 0 END) as auto_picks,
    SUM(CASE WHEN p.is_winner = TRUE THEN 1 ELSE 0 END) as winners,
    SUM(CASE WHEN p.is_winner = FALSE THEN 1 ELSE 0 END) as losers,
    SUM(CASE WHEN p.is_winner IS NULL THEN 1 ELSE 0 END) as pending
FROM rounds r
LEFT JOIN picks p ON r.id = p.round_id
GROUP BY r.id, r.round_number, r.status
ORDER BY r.round_number DESC
LIMIT 10;

-- ============================================================================
-- 2. VERIFY: Who would receive "picks locked" message (ACTIVE only)
-- ============================================================================

-- 2a. Players who SHOULD receive "picks locked" (ACTIVE with telegram_id)
SELECT
    id as player_id,
    name,
    status,
    telegram_id
FROM players
WHERE status = 'active'
  AND telegram_id IS NOT NULL
  AND telegram_id != ''
ORDER BY name;

-- 2b. Players who SHOULD NOT receive "picks locked" (eliminated/winner)
SELECT
    id as player_id,
    name,
    status,
    telegram_id
FROM players
WHERE status != 'active'
  AND telegram_id IS NOT NULL
  AND telegram_id != ''
ORDER BY status, name;

-- ============================================================================
-- 3. VERIFY: Who would receive round results (PARTICIPANTS only)
-- ============================================================================

-- 3a. For a specific round (replace ROUND_ID):
-- Participants who should receive results notification
SELECT
    pl.id as player_id,
    pl.name,
    pl.status,
    pl.telegram_id,
    pk.team_picked,
    pk.is_winner
FROM picks pk
JOIN players pl ON pk.player_id = pl.id
WHERE pk.round_id = 1  -- Replace with actual round_id
ORDER BY pl.name;

-- 3b. Count participants by status for latest completed round
SELECT
    pl.status,
    COUNT(*) as count
FROM picks pk
JOIN players pl ON pk.player_id = pl.id
JOIN rounds r ON pk.round_id = r.id
WHERE r.status = 'completed'
  AND r.round_number = (SELECT MAX(round_number) FROM rounds WHERE status = 'completed')
GROUP BY pl.status;

-- ============================================================================
-- 4. VERIFY: Reminders should only go to ACTIVE + NO PICK
-- ============================================================================

-- 4a. Due reminders for active players without picks (should be sent)
SELECT
    rs.id as reminder_id,
    rs.round_id,
    rs.reminder_type,
    rs.scheduled_time,
    pl.name,
    pl.status,
    CASE WHEN pk.id IS NULL THEN 'NO PICK' ELSE 'HAS PICK' END as pick_status
FROM reminder_schedules rs
JOIN players pl ON rs.player_id = pl.id
LEFT JOIN picks pk ON pk.player_id = pl.id AND pk.round_id = rs.round_id
WHERE rs.is_sent = FALSE
  AND rs.scheduled_time <= NOW()
  AND pl.status = 'active'
  AND pk.id IS NULL
ORDER BY rs.scheduled_time;

-- 4b. Due reminders that should be skipped (not active OR has pick)
SELECT
    rs.id as reminder_id,
    rs.round_id,
    rs.reminder_type,
    pl.name,
    pl.status,
    CASE WHEN pk.id IS NULL THEN 'NO PICK' ELSE 'HAS PICK (' || pk.team_picked || ')' END as pick_status,
    CASE
        WHEN pl.status != 'active' THEN 'SKIP: not active'
        WHEN pk.id IS NOT NULL THEN 'SKIP: already picked'
        ELSE 'SEND'
    END as action
FROM reminder_schedules rs
JOIN players pl ON rs.player_id = pl.id
LEFT JOIN picks pk ON pk.player_id = pl.id AND pk.round_id = rs.round_id
WHERE rs.is_sent = FALSE
  AND rs.scheduled_time <= NOW()
  AND (pl.status != 'active' OR pk.id IS NOT NULL)
ORDER BY pl.status, pl.name;

-- ============================================================================
-- 5. VERIFY: Idempotency - Check special_note flags
-- ============================================================================

-- 5a. Rounds with picks_published flag (won't send again)
SELECT
    id as round_id,
    round_number,
    status,
    special_note
FROM rounds
WHERE special_note LIKE '%picks_published%'
ORDER BY round_number DESC;

-- 5b. Rounds with results_sent flag (won't send again)
SELECT
    id as round_id,
    round_number,
    status,
    special_note
FROM rounds
WHERE special_note LIKE '%results_sent%'
ORDER BY round_number DESC;

-- ============================================================================
-- 6. POST-ROUND: Verify who received messages (check logs for these IDs)
-- ============================================================================

-- 6a. Expected recipients of "picks locked" for round N
-- (ACTIVE players with telegram_id)
SELECT
    'PICKS_LOCKED' as message_type,
    pl.id as player_id,
    pl.name,
    pl.telegram_id,
    pl.status
FROM players pl
WHERE pl.status = 'active'
  AND pl.telegram_id IS NOT NULL
  AND pl.telegram_id != ''
ORDER BY pl.name;

-- 6b. Expected recipients of "round results" for round N
-- (All participants regardless of status)
SELECT
    'ROUND_RESULTS' as message_type,
    pl.id as player_id,
    pl.name,
    pl.telegram_id,
    pl.status,
    pk.team_picked,
    pk.is_winner
FROM picks pk
JOIN players pl ON pk.player_id = pl.id
WHERE pk.round_id = 1  -- Replace with actual round_id
  AND pl.telegram_id IS NOT NULL
  AND pl.telegram_id != ''
ORDER BY pl.name;

-- ============================================================================
-- 7. DEBUG: Find the bug scenario (eliminated player got picks locked msg)
-- ============================================================================

-- 7a. Find players who were eliminated BEFORE current active round
-- These players should NOT receive "picks locked" messages
SELECT
    pl.id as player_id,
    pl.name,
    pl.status,
    pl.telegram_id,
    pk.round_id,
    r.round_number as eliminated_in_round,
    pk.team_picked,
    pk.is_eliminated
FROM players pl
JOIN picks pk ON pl.id = pk.player_id
JOIN rounds r ON pk.round_id = r.id
WHERE pl.status = 'eliminated'
  AND pk.is_eliminated = TRUE
ORDER BY r.round_number DESC, pl.name;

-- 7b. Check if there are any active rounds where eliminated players have picks
-- (This would indicate a data inconsistency)
SELECT
    pl.id as player_id,
    pl.name,
    pl.status as player_status,
    r.round_number,
    r.status as round_status,
    pk.team_picked
FROM picks pk
JOIN players pl ON pk.player_id = pl.id
JOIN rounds r ON pk.round_id = r.id
WHERE pl.status = 'eliminated'
  AND r.status = 'active'
ORDER BY r.round_number, pl.name;

-- ============================================================================
-- 8. SUMMARY: Quick health check
-- ============================================================================
SELECT
    'Total Players' as metric,
    COUNT(*)::text as value
FROM players
UNION ALL
SELECT
    'Active Players',
    COUNT(*)::text
FROM players WHERE status = 'active'
UNION ALL
SELECT
    'Eliminated Players',
    COUNT(*)::text
FROM players WHERE status = 'eliminated'
UNION ALL
SELECT
    'Active Rounds',
    COUNT(*)::text
FROM rounds WHERE status = 'active'
UNION ALL
SELECT
    'Players with Telegram',
    COUNT(*)::text
FROM players WHERE telegram_id IS NOT NULL AND telegram_id != '';
