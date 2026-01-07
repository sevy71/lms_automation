# Railway One-Off Scripts

These scripts are safe to run multiple times. They only insert missing rows or update fixtures by external ID.

## Seed players

Create Tony plus Player 1..Player N (defaults to 30).

```bash
SEED_COUNT=30 python scripts/seed_players.py
```

## Sync fixtures

Pull fixtures from the existing Football Data API and upsert them into the DB.

```bash
FOOTBALL_SEASON=2025 python scripts/sync_fixtures.py
```

Optional filters:

```bash
MATCHDAY=3 python scripts/sync_fixtures.py
ROUND_STATUSES=pending,active python scripts/sync_fixtures.py
```

## Railway commands

Run these as one-off commands in Railway:

- `python scripts/seed_players.py`
- `python scripts/sync_fixtures.py`

## Environment variables

- `FOOTBALL_API_TOKEN` (required): API token for football-data.org
- `FOOTBALL_SEASON` (optional): season year for the API (e.g., `2025`)
- `MATCHDAY` (optional): sync a single matchday
- `ROUND_STATUSES` (optional): comma list of round statuses to sync (default: `pending,active`)
