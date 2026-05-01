# TyperBot

Discord bot for weekly football prediction leagues. Admins create fixtures and enter results. Players submit score predictions in fixture threads or through `/predict`, which posts publicly into those threads. The bot stores picks, calculates points, and posts standings.

## Features
- Thread predictions
- `/predict`
- Flexible score parsing
- Deadlines and late-pick handling
- Standings and results
- SQLite backups

## Commands
### Player commands
- `/predict` - open a modal and post predictions publicly into the fixture thread
- `/fixtures` - show open fixtures and deadlines
- `/mypredictions` - show your saved predictions for open fixtures
- `/standings` - show the leaderboard and latest scored fixture

### Admin commands
- `/admin panel` - open the main admin surface, or start first-time setup when the server is not configured yet

The panel handles:
- create fixtures
- delete fixtures
- jump to older open weeks not shown in the quick list
- enter or correct results
- calculate scores
- re-post the latest completed results with optional mentions
- replace predictions
- review late partial predictions
- toggle late waivers

After setup, admins need the configured TyperBot admin role. Setup from `/admin panel` requires Discord `Administrator` or `Manage Server` permission.

## Permissions
- `Send Messages`
- `Send Messages in Threads`
- `Read Message History`
- `Add Reactions`
- `Create Public Threads`
- `Use Slash Commands`

## Privileged Intents
- Enable `Message Content Intent` in the Discord Developer Portal
- Enable `Server Members Intent` in the Discord Developer Portal

## Prediction flow
- Reply in the fixture thread with one line per match.
- Or run `/predict` anywhere in the server, choose the week if needed, fill the modal, and let the bot post it publicly in the fixture thread.
- To replace a saved prediction, use `/predict` again; the bot posts an updated public message in the fixture thread.
- Partial predictions are allowed. Each partial line must name the game it applies to.
- Missing games count as no prediction.
- Late predictions with missing games stay under admin review until an admin approves or rejects them.
- Approved late submissions count the submitted lines normally, and missing games still count as no prediction.
- Rejected late submissions are discarded.
- Public review status stays visible in the fixture thread.

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
- Late full predictions: 0 points unless an admin waives the penalty
- Late predictions with missing games: excluded from scoring until reviewed by an admin

## Operational constraints
- Match data, predictions, results, and scores are stored in SQLite.
- Short-lived cooldowns are kept in memory, including the thread-post rate limiter and the score-calculation cooldown.
- The bot is intentionally single-process. If the process restarts, in-memory cooldowns reset.

## Configuration

### Required
- `DISCORD_TOKEN` - Discord bot token

### Optional
- `ENVIRONMENT` - environment label; use `production` for production deploys, default is `development`
- `DATA_DIR` - base data directory; default `./data` locally, set `/app/data` on production deployments
- `DB_PATH` - database path; default `{DATA_DIR}/typer.db`
- `BACKUP_DIR` - backup directory; default `{DATA_DIR}/backups`
- `TZ` - timezone for admin deadline input; default `UTC`
- `LOG_LEVEL` - logging level; default `INFO`

Run `/admin panel` in each server to store or update the admin role and league channel. Fixture announcements and deadline reminders both use that league channel.

## Deployment

This bot runs anywhere you can deploy a persistent container.

### Coolify

1. Create a new Coolify worker/background service from this repo.
2. Use the included Dockerfile.
3. Disable HTTP/port health checks if Coolify enables them by default for the service.
4. Mount a persistent volume at `/app/data`.
5. Set variables:
   - `DISCORD_TOKEN=<your token>`
   - `ENVIRONMENT=production`
   - `DATA_DIR=/app/data`
   - optional: `TZ=Europe/Warsaw`

Use a separate token for previews and manual testing. Do not run multiple deployments against the same live token.

### Data

Routine host migration is a direct copy of the live SQLite file at `DB_PATH` (default: `{DATA_DIR}/typer.db`). The `scripts/restore_db.py` helper is for restoring SQL dump backups during recovery, not for the normal host-to-host move.

If you override `DB_PATH` or `BACKUP_DIR`, keep them on the persistent volume too.

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

Use a separate bot token in a private test guild. Point it at an isolated data directory, not your normal local or deployed database.

```powershell
$env:DISCORD_TOKEN="your_test_bot_token"
$env:ENVIRONMENT="development"
$env:DATA_DIR="./.local/manual-discord-test"
uv run python -m typer_bot.dev.seed_test_data --tester-user-id "your_discord_user_id" --guild-id "your_discord_server_id"
uv run python -m typer_bot
```

Enable Discord Developer Mode, right-click your test server, and copy the server ID for `--guild-id`.

The seed command resets that local test database and creates:
- one scored past fixture for standings/history
- one open fixture with saved predictions
- one late open fixture with a late prediction

Outside `./.local/manual-discord-test`, add `--force-reset`.

`--force-reset` deletes the target DB, its `-wal` and `-shm` files, and the configured backup directory before reseeding.

In a deployment shell, use the same command against that deployment's `DB_PATH` and `BACKUP_DIR`.
Those paths usually are not `./.local/manual-discord-test`, so add `--force-reset`.

Create a real fixture when you need to test posting, thread creation, reactions, or modal-to-thread prediction posting.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```

## Backup and Restore

- Automatic: the database is backed up after each successful score calculation. The bot keeps the latest 10 backups in `BACKUP_DIR`.
- Manual restore: run from the host or container shell where the live data volume is mounted.

```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_*.sql
```

The restore script asks for confirmation, restores into a temporary SQLite file first, and only replaces the live database after success.

## License
MIT.
