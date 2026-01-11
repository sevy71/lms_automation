-- Check current player statuses
SELECT 
    status,
    COUNT(*) as count
FROM players
GROUP BY status;

-- Check Round 1 eliminations
SELECT 
    COUNT(DISTINCT player_id) as players_with_picks,
    COUNT(DISTINCT CASE WHEN is_eliminated = TRUE THEN player_id END) as eliminated_count,
    COUNT(DISTINCT CASE WHEN is_winner = FALSE THEN player_id END) as lost_count
FROM picks
WHERE round_id = (SELECT id FROM rounds WHERE round_number = 1);

-- Show specific eliminated players
SELECT 
    p.id,
    p.name,
    p.status,
    pk.team_picked,
    pk.is_winner,
    pk.is_eliminated
FROM players p
JOIN picks pk ON p.id = pk.player_id
WHERE pk.round_id = (SELECT id FROM rounds WHERE round_number = 1)
  AND pk.is_eliminated = TRUE
ORDER BY p.name
LIMIT 10;

-- Check Round 1 status
SELECT 
    id,
    round_number,
    status,
    (SELECT COUNT(*) FROM fixtures WHERE round_id = rounds.id AND status = 'completed') as completed_fixtures,
    (SELECT COUNT(*) FROM fixtures WHERE round_id = rounds.id) as total_fixtures
FROM rounds
WHERE round_number = 1;
