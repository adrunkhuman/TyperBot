# TyperBot

TyperBot is a Discord bot for football prediction leagues. One hosted TyperBot application can serve many Discord servers. Each server gets its own isolated league state: one configured league channel, one active season, fixtures, predictions, scoring rules, results, and standings.

Server admins invite the hosted bot and configure it in Discord. They do not need to host their own bot process.

## Features

- Public fixture threads for predictions
- `/predict` modal that posts predictions into the selected fixture thread
- Flexible score parsing
- Deadlines, late prediction handling, and admin review for late partial predictions
- One active season per server
- Per-season scoring rules
- Standings and latest scored fixture summaries
- SQLite persistence and score-calculation backups

## Server Admin Setup

Use this section if you administer a Discord server that has invited TyperBot.

### Invite The Bot

Ask the TyperBot operator for the invite link. The invite must grant the bot these permissions:

- `Send Messages`
- `Send Messages in Threads`
- `Read Message History`
- `Add Reactions`
- `Create Public Threads`
- `Use Slash Commands`

The hosted bot application also needs these privileged intents enabled by the operator:

- `Message Content Intent`
- `Server Members Intent`

### First Run

Run `/admin panel` in your server.

If the server is not configured yet, TyperBot prompts setup. Setup requires Discord `Administrator` or `Manage Server` permission. Choose:

- TyperBot admin role: members with this role can use league admin actions after setup
- League channel: fixture announcements, fixture threads, and deadline reminders use this channel

You can later run `/admin panel` and use `Setup TyperBot` to update those choices.

### Start A League

After setup, open `/admin panel` with the configured TyperBot admin role.

Use the panel to:

- create fixtures
- delete open fixtures
- jump to older open weeks not shown in the quick list
- enter or correct results
- calculate scores
- re-post latest completed results with optional mentions
- replace predictions
- review late partial predictions
- toggle late waivers
- edit scoring rules before scores exist for the active season
- start a new season after all active-season fixtures are closed

TyperBot creates the first active season automatically when league data is first needed. Starting a new season archives the previous active season and resets scoring rules to defaults.

## Player Commands

- `/predict` - open a modal and post predictions publicly into the fixture thread
- `/fixtures` - show open fixtures and deadlines
- `/mypredictions` - show your saved predictions for open fixtures
- `/standings` - show the active-season leaderboard and latest scored fixture

## Prediction Flow

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

## Seasons And Scoring

Each Discord server has one active season. Fixtures, predictions, scores, standings, and scoring rules are scoped to that server and active season.

Default scoring rules for a new season:

- Exact score: 3 points
- Correct outcome: 1 point
- Wrong outcome: 0 points
- Late full predictions: 0 points unless an admin waives the penalty

Rules are stored per season. Rule values must be whole numbers greater than or equal to zero. Changing rules is blocked after scores exist for that season, so stored scores do not silently go stale.

Late predictions with missing games are excluded from scoring until reviewed by an admin.

## Operator Deployment

Use this section if you run the hosted TyperBot application.

### Runtime Model

Run one bot process for one Discord bot token. That process can serve many Discord guilds because all league state is guild-scoped in the database.

Do not run multiple production deployments against the same live token. Discord will connect every deployment with a valid token, including non-production deployments.

### Configuration

Required:

- `DISCORD_TOKEN` - Discord bot token

Optional:

- `ENVIRONMENT` - environment label; use `production` for production deploys, default is `development`
- `DATA_DIR` - base data directory; default `./data` locally, set `/app/data` on production deployments
- `DB_PATH` - database path; default `{DATA_DIR}/typer.db`
- `BACKUP_DIR` - backup directory; default `{DATA_DIR}/backups`
- `TZ` - timezone for admin deadline input; default `UTC`
- `LOG_LEVEL` - logging level; default `INFO`

Production data paths must live on a persistent volume. The default production pattern is:

```text
DATA_DIR=/app/data
DB_PATH=/app/data/typer.db
BACKUP_DIR=/app/data/backups
```

### Coolify

1. Create a worker/background service from this repo.
2. Use the included Dockerfile.
3. Disable HTTP/port health checks if Coolify enables them by default for the service.
4. Mount a persistent volume at `/app/data`.
5. Set `DISCORD_TOKEN`, `ENVIRONMENT=production`, and `DATA_DIR=/app/data`.
6. Set `TZ` if the league uses a non-UTC local deadline timezone.

### Data And Backups

Match data, predictions, results, and scores are stored in SQLite. Short-lived cooldowns are kept in memory, including the thread-post rate limiter and score-calculation cooldown. If the process restarts, in-memory cooldowns reset.

Automatic backups run after each successful score calculation. The bot keeps the latest 10 backups in `BACKUP_DIR`.

Routine host migration is a direct copy of the live SQLite file at `DB_PATH`. If you override `DB_PATH` or `BACKUP_DIR`, keep them on the persistent volume too.

Manual restore runs from the host or container shell where the live data volume is mounted:

```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_20260510_120000.sql
```

The restore script asks for confirmation, restores into a temporary SQLite file first, and only replaces the live database after success.

### Non-Production And Manual Testing

Use a separate bot token in a private test guild. Never point a preview deployment at the live production token.

Local runs default to `ENVIRONMENT=development`, `DATA_DIR=./data`, and `TZ=UTC`.

```bash
git clone https://github.com/adrunkhuman/TyperBot
cd TyperBot
uv sync --group dev
export DISCORD_TOKEN="your_test_bot_token"
export ENVIRONMENT=development
uv run python -m typer_bot
```

Windows PowerShell:

```powershell
$env:DISCORD_TOKEN="your_test_bot_token"
$env:ENVIRONMENT="development"
uv run python -m typer_bot
```

Manual Discord testing with seeded data:

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

Outside `./.local/manual-discord-test`, add `--force-reset`. `--force-reset` deletes the target DB, its `-wal` and `-shm` files, and the configured backup directory before reseeding.

Create a real fixture when you need to test posting, thread creation, reactions, or modal-to-thread prediction posting.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check typer_bot
```

## License

MIT.
