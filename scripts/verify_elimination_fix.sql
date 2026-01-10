-- LMS Elimination Fix Verification Queries
-- Run these queries against your database to verify the fixes

-- ============================================================
-- QUERY 1: Show Aston Villa vs Crystal Palace fixture (Jan 7 draw)
-- This should show the match as a draw with status='completed'
-- ============================================================
SELECT
    f.id as fixture_id,
    r.round_number,
    f.home_team,
    f.away_team,
    f.home_score,
    f.away_score,
    f.status,
    f.date,
    CASE WHEN f.home_score = f.away_score THEN 'DRAW - SHOULD ELIMINATE' ELSE 'NOT DRAW' END as result_type
FROM fixtures f
JOIN rounds r ON f.round_id = r.id
WHERE (
    LOWER(f.home_team) LIKE '%aston villa%'
    OR LOWER(f.away_team) LIKE '%aston villa%'
    OR LOWER(f.home_team) LIKE '%crystal palace%'
    OR LOWER(f.away_team) LIKE '%crystal palace%'
)
ORDER BY f.date DESC;

-- ============================================================
-- QUERY 2: Show all Aston Villa picks with their elimination status
-- After fix: All Villa picks from draw matches should have:
--   is_winner = FALSE
--   is_eliminated = TRUE
-- ============================================================
SELECT
    pk.id as pick_id,
    p.id as player_id,
    p.name as player_name,
    r.round_number,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated,
    p.status as player_status,
    pk.auto_assigned,
    pk.auto_reason
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE LOWER(pk.team_picked) LIKE '%villa%'
   OR LOWER(pk.team_picked) LIKE '%aston%'
ORDER BY r.round_number, p.name;

-- ============================================================
-- QUERY 3: Show Crystal Palace picks (other team in draw)
-- After fix: All Palace picks from draw matches should have:
--   is_winner = FALSE
--   is_eliminated = TRUE
-- ============================================================
SELECT
    pk.id as pick_id,
    p.id as player_id,
    p.name as player_name,
    r.round_number,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated,
    p.status as player_status
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE LOWER(pk.team_picked) LIKE '%palace%'
   OR LOWER(pk.team_picked) LIKE '%crystal%'
ORDER BY r.round_number, p.name;

-- ============================================================
-- QUERY 4: Pick result distribution summary
-- This shows overall state of picks
-- ============================================================
SELECT
    CASE
        WHEN is_winner = true THEN 'Winner'
        WHEN is_winner = false THEN 'Loser'
        ELSE 'Pending'
    END as result,
    COUNT(*) as total_picks,
    SUM(CASE WHEN is_eliminated = true THEN 1 ELSE 0 END) as eliminated_count,
    SUM(CASE WHEN is_eliminated = false OR is_eliminated IS NULL THEN 1 ELSE 0 END) as not_eliminated_count
FROM picks
GROUP BY is_winner
ORDER BY result;

-- ============================================================
-- QUERY 5: Player status distribution
-- Shows how many players are in each status
-- ============================================================
SELECT
    status,
    COUNT(*) as player_count
FROM players
GROUP BY status
ORDER BY
    CASE status
        WHEN 'active' THEN 1
        WHEN 'eliminated' THEN 2
        WHEN 'winner' THEN 3
        ELSE 4
    END;

-- ============================================================
-- QUERY 6: CRITICAL - Find inconsistent data
-- These picks lost but are not marked as eliminated
-- After fix: This should return ZERO rows
-- ============================================================
SELECT
    'INCONSISTENT: Loser but not eliminated' as issue,
    pk.id as pick_id,
    p.name as player_name,
    r.round_number,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated,
    p.status as player_status
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.is_winner = false AND (pk.is_eliminated = false OR pk.is_eliminated IS NULL);

-- ============================================================
-- QUERY 7: Find picks that couldn't be matched to fixtures
-- These are picks where is_winner is still NULL after fixtures completed
-- After fix: Should be ZERO for completed rounds
-- ============================================================
SELECT
    'UNMATCHED: Pick not evaluated' as issue,
    pk.id as pick_id,
    p.name as player_name,
    r.round_number,
    r.status as round_status,
    pk.team_picked,
    pk.is_winner
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.is_winner IS NULL
  AND r.status = 'completed';

-- ============================================================
-- QUERY 8: Show all draws and affected picks
-- Lists all draw fixtures and counts how many picks were affected
-- ============================================================
SELECT
    f.id as fixture_id,
    r.round_number,
    f.home_team || ' ' || f.home_score || '-' || f.away_score || ' ' || f.away_team as match_result,
    f.date,
    (SELECT COUNT(*) FROM picks WHERE round_id = f.round_id
     AND (LOWER(team_picked) LIKE '%' || LOWER(SUBSTRING(f.home_team, 1, 5)) || '%'
          OR LOWER(team_picked) LIKE '%' || LOWER(SUBSTRING(f.away_team, 1, 5)) || '%')
    ) as estimated_affected_picks
FROM fixtures f
JOIN rounds r ON f.round_id = r.id
WHERE f.status = 'completed'
  AND f.home_score IS NOT NULL
  AND f.home_score = f.away_score
ORDER BY r.round_number, f.date;

-- ============================================================
-- QUERY 9: Auto-pick distribution check
-- Shows if auto-picks are distributed or all picking the same team
-- After fix: Should show variety, not all "AFC Bournemouth"
-- ============================================================
SELECT
    r.round_number,
    pk.team_picked,
    COUNT(*) as pick_count,
    STRING_AGG(p.name, ', ') as players
FROM picks pk
JOIN players p ON pk.player_id = p.id
JOIN rounds r ON pk.round_id = r.id
WHERE pk.auto_assigned = true
GROUP BY r.round_number, pk.team_picked
ORDER BY r.round_number, pick_count DESC;

-- ============================================================
-- QUERY 10: Round-by-round elimination summary
-- Shows eliminations per round
-- ============================================================
SELECT
    r.round_number,
    r.status as round_status,
    COUNT(pk.id) as total_picks,
    SUM(CASE WHEN pk.is_winner = true THEN 1 ELSE 0 END) as winners,
    SUM(CASE WHEN pk.is_winner = false THEN 1 ELSE 0 END) as losers,
    SUM(CASE WHEN pk.is_eliminated = true THEN 1 ELSE 0 END) as eliminated,
    SUM(CASE WHEN pk.is_winner IS NULL THEN 1 ELSE 0 END) as pending
FROM rounds r
LEFT JOIN picks pk ON r.id = pk.round_id
GROUP BY r.id, r.round_number, r.status
ORDER BY r.round_number;
