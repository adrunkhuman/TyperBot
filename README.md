# matchday-typer

100% vibecoded, no guarantees given but it seems to work.

A Discord bot for running weekly football prediction games. I built this because spreadsheets are annoying and other bots were too complicated.

## What it does
- **Predictions via Thread**: Post directly in the fixture announcement thread.
- **Predictions via DM**: Type `/predict` to submit privately.
- **Easy Format**: Just reply with scores like `2-1` or `2:0`.
- **Points**: 3 points for exact score, 1 point for correct winner/draw.
- **Leaderboards**: `/standings` to see the table.
- **Deadlines**: Set per fixture. Late submissions get 0 points.
- **Persistent**: Uses SQLite.

## Permissions
Bot requires:
- **Send Messages** & **Read Message History**
- **Add Reactions**: To confirm predictions.
- **Create Public Threads**: Auto-creates prediction threads on fixture announcements.
- **Use Slash Commands**

## Workflow State
- Match data, predictions, results, and scores are persisted in SQLite.
- Active DM workflows and short-lived cooldowns are intentionally kept in memory.
- This includes the thread-post rate limiter and the `/admin results calculate` cooldown, so both reset if the bot process restarts.
- This bot assumes a single-process deployment. If the process restarts, any in-progress fixture/results/prediction DM flow is lost and users need to start again.
- That tradeoff is deliberate for a small personal bot: simpler code, fewer moving parts, and no extra operational state to migrate or debug.

## How to use

### For Players

**Method 1: Thread Predictions (Recommended)**
1. Look for the fixture announcement with a thread attached
2. Reply in the thread with your predictions:
   ```
   Team A - Team B 2:1
   Team C - Team D 0:0
   Team E - Team F 3:2
   ...
   ```
 3. Bot reacts ✅ when saved. Thread submissions are one-shot; use `/predict` or DM the bot to replace an existing prediction.

**Method 2: DM Predictions**
1. Type `/predict` (or DM the bot directly) -> Bot DMs you the games
2. Reply with scores (same format as above) -> Predictions saved immediately!
3. If multiple fixtures are open, bot asks which week first and can guide you through the rest
4. To change, just send a new message

**Check**: `/mypredictions` to see what you sent.
**Flex**: `/standings` to see the table.

### For Admins
You need a Discord role named `Admin` or `typer-admin`.

**Fixture Management:**
- `/admin panel` - Open the admin hub for fixture deletion, prediction overrides, waivers, and result correction
- `/admin fixture create` - Create a new fixture (DM workflow with games + deadline, auto-creates prediction thread)
- `/admin fixture delete [week]` - Delete an open fixture (week required if multiple are open)

**Results Management:**
- `/admin results enter [week]` - Enter actual game scores (DM workflow)
- `/admin results calculate [week]` - Calculate scores and post results (no mentions by default)
- `/admin results post` - Re-post results with option to mention users

## Hosting (The Easy Way)

I recommend **Railway** because it's cheap/free and supports persistent storage easily.

1. Fork this repo.
2. New Project on Railway -> Deploy from GitHub.
3. **CRITICAL STEP**: Add a Volume.
   - Go to "Volumes", click New.
   - Mount path: `/app/data`
   - If you skip this, your database will vanish every time you deploy.
4. Set Variables:
    - `DISCORD_TOKEN`: Get this from Discord Developer Portal.
    - `DATA_DIR`: (Optional) Base data directory. Code default is `./data`. On Railway, set it to `/app/data` so the database lives on the mounted volume.
    - `DB_PATH`: (Optional) Database path. Code default is `{DATA_DIR}/typer.db`.
    - `BACKUP_DIR`: (Optional) Backup storage. Code default is `{DATA_DIR}/backups`.
   - `TZ`: (Optional) Timezone for deadline inputs in the admin DM workflow. Default `UTC`. Examples: `Europe/Warsaw`, `America/New_York`, `Asia/Tokyo`.
   - `REMINDER_CHANNEL_ID`: (Optional) ID of channel to spam reminders in.
   - `LOG_LEVEL`: (Optional) Set to `DEBUG` for verbose logs. Default `INFO`.
   - `ENVIRONMENT`: (Optional) Set to `production` for live bot operation. Other values run smoke-test mode (validates config then exits). Default: `development`.

## Running Locally

By default the bot runs in smoke-test mode: it validates config and exits without connecting to Discord. That is intentional so preview deployments do not fight production for the same token.

If you want a real local bot session, set `ENVIRONMENT=production`.

```bash
# Clone and setup
git clone https://github.com/adrunkhuman/matchday-typer
cd matchday-typer
uv sync --group dev

# Smoke test config only (default behavior)
export DISCORD_TOKEN="your_token"
uv run python -m typer_bot
```

Unix/macOS live run:

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

This project uses [uv](https://github.com/astral-sh/uv) for development.

```bash
# Install dependencies
uv sync --group dev

# Run tests
uv run pytest

# Lint
uv run ruff check .
```

## Testing

The project has comprehensive test coverage (200+ tests) covering admin commands, core bot logic, handlers, and integration workflows.

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=typer_bot

# Run specific test file
uv run pytest tests/test_admin_commands.py -v
```

Tests run automatically on pull requests via GitHub Actions.

## Username Management

Usernames are updated automatically when users submit predictions.

## Backup and Restore

**Automatic:** Database is backed up after each successful `/admin results calculate`. Backups are stored in `BACKUP_DIR` and the bot keeps the latest 10.

**Manual Restore:** Run from Railway console (requires shell access):
```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_*.sql
```
Type `YES` to confirm. The script rejects obviously dangerous SQL, restores into a temporary SQLite file, then atomically replaces the live DB only if the restore succeeds. If a live DB already exists, it is copied to a timestamped `.db.bak.*` file first.

## License
MIT. Do whatever you want with it.
