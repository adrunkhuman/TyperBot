# TyperBot

100% vibecoded, no guarantees given but it seems to work.

Discord bot for weekly football prediction leagues. Admins create fixtures and enter results. Players submit score predictions in fixture threads or through `/predict`, which posts publicly into those threads. The bot stores picks, calculates points, and posts standings.

## Features
- Thread predictions on fixture announcement threads
- `/predict` modal flow that posts publicly into fixture threads
- Flexible score parsing: `2-1`, `2:1`, `2 : 1`
- Per-fixture deadlines with late-pick handling
- Standings, saved predictions, and weekly results posting
- SQLite persistence with automatic backups after successful score calculation

## Commands
### Player commands
- `/predict` - open a modal and post predictions publicly into the fixture thread
- `/fixtures` - show open fixtures and deadlines
- `/mypredictions` - show your saved predictions for open fixtures
- `/standings` - show the leaderboard and latest scored fixture

### Admin commands
- `/admin panel` - open the admin panel for deletion, overrides, waivers, and result correction
- `/admin fixture create` - create a fixture by modal and post its prediction thread
- `/admin fixture delete [week]` - delete an open fixture
- `/admin results enter [week]` - enter actual results by modal
- `/admin results calculate [week]` - calculate scores and post results
- `/admin results post` - repost results with optional mentions

Admins need a Discord role named `Admin` or `typer-admin`.

## Permissions
- `Send Messages`
- `Read Message History`
- `Add Reactions`
- `Create Public Threads`
- `Use Slash Commands`

## Privileged Intents
- Enable `Message Content Intent` in the Discord Developer Portal
- Enable `Server Members Intent` in the Discord Developer Portal

## Prediction flow
- Reply in the fixture thread with one line per match.
- Run `/predict` anywhere in the server to fill a modal, then have the bot post your prediction publicly into the fixture thread.
- To replace a saved prediction, use `/predict` again; the bot posts a new public `Updated prediction` message in the thread.

Example:

```text
Team A - Team B 2:1
Team C - Team D 0:0
Team E - Team F 3:2
```

## Scoring
- Exact score: 3 points
- Correct outcome: 1 point
- Wrong outcome: 0 points
- Late predictions: 0 points unless an admin waives the penalty

## Operational constraints
- Match data, predictions, results, and scores are stored in SQLite.
- Short-lived cooldowns are kept in memory.
- This includes the thread-post rate limiter and the `/admin results calculate` cooldown.
- The bot is intentionally single-process for v1. If the process restarts, in-memory cooldowns reset.

## Configuration

### Required
- `DISCORD_TOKEN` - Discord bot token

### Optional
- `ENVIRONMENT` - environment label; use `production` for production deploys, default is `development`
- `DATA_DIR` - base data directory; default `./data` locally, set `/app/data` on Railway
- `DB_PATH` - database path; default `{DATA_DIR}/typer.db`
- `BACKUP_DIR` - backup directory; default `{DATA_DIR}/backups`
- `TZ` - timezone for admin deadline input; default `UTC`
- `REMINDER_CHANNEL_ID` - reminder channel ID
- `LOG_LEVEL` - logging level; default `INFO`

## Deployment

### Railway

1. Fork this repo.
2. New Project on Railway -> Deploy from GitHub.
3. Add a persistent volume mounted at `/app/data`.
4. Set Variables:
   - `DISCORD_TOKEN=<your token>`
   - `ENVIRONMENT=production`
   - `DATA_DIR=/app/data`
   - optional: `TZ=Europe/Warsaw`

`ENVIRONMENT` is labeling only. Any deployment with a valid `DISCORD_TOKEN` will connect to Discord and process events. Use a separate token for previews and manual testing. If `DISCORD_TOKEN` is unset, startup fails instead of connecting. Do not run multiple deployments against the same live token.

## Running locally

Local runs default to `ENVIRONMENT=development`, `DATA_DIR=./data`, and `TZ=UTC`.

```bash
git clone https://github.com/adrunkhuman/TyperBot
cd TyperBot
uv sync --group dev

export DISCORD_TOKEN="your_token"
export ENVIRONMENT=development
uv run python -m typer_bot
```

Windows PowerShell:

```powershell
$env:DISCORD_TOKEN="your_token"
$env:ENVIRONMENT="development"
uv run python -m typer_bot
```

## Manual Discord Testing

Use a separate bot token in a private test guild. Point it at an isolated data directory, not your normal local or Railway database.

```powershell
$env:DISCORD_TOKEN="your_test_bot_token"
$env:ENVIRONMENT="development"
$env:DATA_DIR="./.local/manual-discord-test"
uv run python -m typer_bot.dev.seed_test_data --tester-user-id "your_discord_user_id"
uv run python -m typer_bot
```

The seed command resets that local test database and creates one mixed scenario:
- one scored past fixture for standings/history
- one open fixture with saved predictions
- one late open fixture with a late prediction

Outside `./.local/manual-discord-test`, add `--force-reset`.

`--force-reset` deletes the target DB, its `-wal` and `-shm` files, and the configured backup directory before reseeding.

In a PR deployment shell, use the same command against that deployment's `DB_PATH` and `BACKUP_DIR`.
Those paths usually are not `./.local/manual-discord-test`, so add `--force-reset`.

Create a real fixture when you need to test announcement posting, thread creation, reactions, or modal-to-thread prediction posting. The seed data is only there to save setup time.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```

## Backup and Restore

- Automatic: the database is backed up after each successful `/admin results calculate`. The bot keeps the latest 10 backups in `BACKUP_DIR`.
- Manual restore: run from the Railway shell.

```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_*.sql
```

The restore script asks for confirmation, restores into a temporary SQLite file first, and only replaces the live database after a successful restore.

## License
MIT.
