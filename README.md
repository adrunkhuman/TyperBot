# matchday-typer

100% vibecoded, no guarantees given but it seems to work.

A Discord bot for running weekly football prediction games with your friends. I built this because spreadsheets are annoying and other bots were too complicated.

## What it does
- **Predictions via DM**: You type `/predict`, bot slides into your DMs.
- **Easy Format**: Just reply with scores like `2-1` or `2:0`.
- **Points**: 3 points for exact score, 1 point for correct winner/draw.
- **Leaderboards**: `/standings` to see who knows ball.
- **Deadlines**: Set them per fixture. Late submissions get 0 points (brutal but fair).
- **Persistent**: Uses SQLite so your scores survive restarts/redeployments.

## How to use

### For Players
1. **Predict**: `/predict` -> Bot DMs you the games -> You reply with scores.
2. **Check**: `/mypredictions` to see what you sent.
3. **Flex**: `/standings` to see the table.

### For Admins
You need a Discord role named `Admin` or `typer-admin`.

**Fixture Management:**
- `/admin fixture create` - Create a new fixture (DM workflow with games + deadline)
- `/admin fixture delete` - Delete the current fixture

**Results Management:**
- `/admin results enter` - Enter actual game scores (DM workflow)
- `/admin results calculate` - Calculate scores and post results (no mentions by default)
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
   - `DATA_DIR`: (Optional) Base data directory. Default `/app/data`.
   - `DB_PATH`: (Optional) Database path. Defaults to `{DATA_DIR}/typer.db`.
   - `BACKUP_DIR`: (Optional) Backup storage. Defaults to `{DATA_DIR}/backups`.
   - `TZ`: (Optional) Timezone for deadlines. Default `Europe/Warsaw`. Examples: `America/New_York`, `Asia/Tokyo`.
   - `REMINDER_CHANNEL_ID`: (Optional) ID of channel to spam reminders in.
   - `LOG_LEVEL`: (Optional) Set to `DEBUG` for verbose logs. Default `INFO`.
   - `IMPORT_ARCHIVE`: (Optional) Set to `true` to import `.sql` files from `archive/` on first run. Default: disabled.

## Running Locally

If you know Python:

```bash
# Clone and setup
git clone https://github.com/adrunkhuman/matchday-typer
cd matchday-typer

# Install dependencies (requires [uv](https://docs.astral.sh/uv/))
uv sync

# Run
export DISCORD_TOKEN="your_token"
uv run python -m typer_bot
```

## Development Setup

To run tests and linting:

```bash
# Install with dev dependencies (pytest, ruff, etc.)
uv sync --group dev

# Run tests
uv run pytest tests/ -v

# Run linting
uv run ruff check .

# Format code
uv run ruff format .
```

## Importing History
If you have a bunch of old scores you want to keep:
1. Put `.sql` files in `archive/` folder (check `example_import.sql` for format).
2. Start bot with a fresh database.
3. It'll import them automatically.

## Username Management

Usernames are automatically refreshed on bot startup and updated when users submit predictions. No manual refresh needed.

## Backup and Restore

**Automatic:** Database backed up automatically after each `/admin calculate`. Stored in `/app/data/backups/`, last 10 kept.

**Manual Restore:** Run from Railway console (requires shell access):
```bash
ls /app/data/backups/
python scripts/restore_db.py /app/data/backups/backup_*.sql
```
Type "YES" to confirm. Current DB backed up to `.bak` file before restore.

## License
MIT. Do whatever you want with it.
