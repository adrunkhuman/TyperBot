# matchday-typer

100% vibecoded, no guarantees given but it seems to work.

Discord bot for weekly football prediction leagues. Admins create fixtures and enter results. Players submit score predictions by thread or DM. The bot stores picks, calculates points, and posts standings.

## Features
- Thread predictions on fixture announcement threads
- DM prediction flow via `/predict`
- Flexible score parsing: `2-1`, `2:1`, `2 : 1`
- Per-fixture deadlines with late-pick handling
- Standings, saved predictions, and weekly results posting
- SQLite persistence with automatic backups after successful score calculation

## Commands

### Player commands
- `/predict` - start the DM prediction flow
- `/fixtures` - show open fixtures and deadlines
- `/mypredictions` - show your saved predictions for open fixtures
- `/standings` - show the leaderboard and latest scored fixture

### Admin commands
- `/admin panel` - open the admin panel for deletion, overrides, waivers, and result correction
- `/admin fixture create` - create a fixture by DM and post its prediction thread
- `/admin fixture delete [week]` - delete an open fixture
- `/admin results enter [week]` - enter actual results by DM
- `/admin results calculate [week]` - calculate scores and post results
- `/admin results post` - repost results with optional mentions

Admins need a Discord role named `Admin` or `typer-admin`.

## Permissions
- `Send Messages`
- `Read Message History`
- `Add Reactions`
- `Create Public Threads`
- `Use Slash Commands`

## Prediction flow

Players can submit predictions in two ways:

1. Reply in the fixture thread with one line per match.
2. Run `/predict` or DM the bot and submit the same scores privately.

Example:

```text
Team A - Team B 2:1
Team C - Team D 0:0
Team E - Team F 3:2
```

Thread submissions are one-shot. To replace a saved prediction, use `/predict` or DM the bot again.

## Scoring
- Exact score: 3 points
- Correct outcome: 1 point
- Wrong outcome: 0 points
- Late predictions: 0 points unless an admin waives the penalty

## Deployment model
- Match data, predictions, results, and scores are stored in SQLite.
- Active DM workflows and short-lived cooldowns are kept in memory.
- This includes the thread-post rate limiter and the `/admin results calculate` cooldown.
- The bot is intentionally single-process for v1. If the process restarts, in-progress DM workflows are lost and in-memory cooldowns reset.

## Configuration

### Required
- `DISCORD_TOKEN` - Discord bot token

### Optional
- `ENVIRONMENT` - `production` to run the bot; default is `development`, which only smoke-tests config and exits
- `DATA_DIR` - base data directory; default `./data` locally, set `/app/data` on Railway
- `DB_PATH` - database path; default `{DATA_DIR}/typer.db`
- `BACKUP_DIR` - backup directory; default `{DATA_DIR}/backups`
- `TZ` - timezone for admin deadline input; default `UTC`
- `REMINDER_CHANNEL_ID` - reminder channel ID
- `LOG_LEVEL` - logging level; default `INFO`

## Railway deployment

1. Fork this repo.
2. New Project on Railway -> Deploy from GitHub.
3. Add a persistent volume mounted at `/app/data`.
4. Set Variables:
   - `DISCORD_TOKEN=<your token>`
   - `ENVIRONMENT=production`
   - `DATA_DIR=/app/data`
   - optional: `TZ=Europe/Warsaw`

## Running Locally

By default the bot runs in smoke-test mode. It validates config and exits without connecting to Discord. Local runs also default to `DATA_DIR=./data` and `TZ=UTC`.

```bash
git clone https://github.com/adrunkhuman/matchday-typer
cd matchday-typer
uv sync --group dev

export DISCORD_TOKEN="your_token"
uv run python -m typer_bot
```

For a real local bot session:

```bash
export DISCORD_TOKEN="your_token"
export ENVIRONMENT=production
uv run python -m typer_bot
```

Windows PowerShell live run:

```powershell
$env:DISCORD_TOKEN="your_token"
$env:ENVIRONMENT="production"
uv run python -m typer_bot
```

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```

Critical-path test suite:

```bash
uv run pytest tests/test_user_commands.py tests/test_admin_commands.py tests/test_dm_prediction_handler.py tests/test_thread_prediction_handler.py tests/test_results_handler.py tests/test_integration.py
```

Usernames are updated automatically when users submit predictions.

## Backup and Restore

- Automatic: the database is backed up after each successful `/admin results calculate`. The bot keeps the latest 10 backups in `BACKUP_DIR`.
- Manual restore: run from the Railway shell.

```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_*.sql
```

The restore script asks for confirmation, restores into a temporary SQLite file first, and only replaces the live database after a successful restore.

## License
MIT. Do whatever you want with it.
