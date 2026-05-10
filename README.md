# TyperBot

Discord bot for football prediction leagues. One hosted TyperBot application can be invited into multiple Discord servers; each server gets its own isolated league, active season, fixtures, predictions, scoring rules, and standings.

Server admins invite and configure the bot. They do not self-host it.

## What It Does

- Admins create fixture threads, enter results, calculate scores, and manage seasons.
- Players predict in fixture threads or with `/predict`, which posts publicly into the selected thread.
- Standings use the server's active season only.
- Scoring rules are stored per season and lock once scores exist.
- SQLite stores league data; backups run after successful score calculation.

## Server Admin Setup

Ask the bot operator for the invite link. The bot needs:

- `Send Messages`
- `Send Messages in Threads`
- `Read Message History`
- `Add Reactions`
- `Create Public Threads`
- `Use Slash Commands`

The bot application also needs `Message Content Intent` and `Server Members Intent` enabled by the operator.

After inviting TyperBot, run `/admin panel`. First-time setup requires Discord `Administrator` or `Manage Server` permission and stores:

- admin role: who can use league admin actions
- league channel: where fixture announcements, threads, and reminders go

After setup, members with the configured admin role use `/admin panel` to create fixtures, enter results, calculate scores, review late partial predictions, edit scoring rules, and start new seasons.

## Player Commands

- `/predict` - submit or replace predictions in a fixture thread
- `/fixtures` - show open fixtures
- `/mypredictions` - show your open-fixture predictions
- `/standings` - show active-season standings and latest scored fixture

Thread predictions use one line per match:

```text
Team A - Team B 2:1
Team C - Team D 0:0
Team E - Team F 3:2
```

Partial predictions are allowed if each line names the game. Missing games count as no prediction. Late partial predictions wait for admin approval.

## Seasons And Scoring

Each server has one active season. Starting a new season archives the old active season and resets scoring rules to defaults:

- exact score: 3
- correct outcome: 1
- wrong outcome: 0
- late full prediction: 0 unless waived

Admins can edit scoring rules before scores exist in the active season. Rule values must be whole numbers greater than or equal to zero.

## Operator Notes

Run one bot process per Discord bot token. One process can serve many servers because all league state is guild-scoped. Do not run production and preview deployments against the same token.

Required environment variable:

- `DISCORD_TOKEN`

Common optional variables:

- `ENVIRONMENT` - default `development`; use `production` for production deploys
- `DATA_DIR` - default `./data`; use `/app/data` in production
- `DB_PATH` - default `{DATA_DIR}/typer.db`
- `BACKUP_DIR` - default `{DATA_DIR}/backups`
- `TZ` - default `UTC`
- `LOG_LEVEL` - default `INFO`

Production data must live on a persistent volume. Typical production values:

```text
ENVIRONMENT=production
DATA_DIR=/app/data
```

For Coolify, use the Dockerfile as a worker/background service, mount `/app/data`, and disable HTTP health checks if Coolify enables them for the service.

Routine host migration is a direct copy of the SQLite file at `DB_PATH`. Manual restore uses one backup file:

```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_YYYY-MM-DD_HH-MM-SS-ms.sql
```

## Local Development

```bash
uv sync --group dev
export DISCORD_TOKEN="your_test_bot_token"
uv run python -m typer_bot
```

Manual Discord testing can seed an isolated local database:

```powershell
$env:DISCORD_TOKEN="your_test_bot_token"
$env:DATA_DIR="./.local/manual-discord-test"
uv run python -m typer_bot.dev.seed_test_data --tester-user-id "your_discord_user_id" --guild-id "your_discord_server_id"
uv run python -m typer_bot
```

Run checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```

## License

MIT.
