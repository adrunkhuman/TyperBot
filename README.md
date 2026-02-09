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
- **Use Slash Commands**

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
3. Bot reacts ✅ when saved. Edit your message anytime before the deadline to update.

**Method 2: DM Predictions**
1. Type `/predict` -> Bot DMs you the games
2. Reply with scores (same format as above)
3. Confirm via the button

**Check**: `/mypredictions` to see what you sent.
**Flex**: `/standings` to see the table.

### For Admins
You need a Discord role named `Admin` or `typer-admin`.

**Fixture Management:**
- `/admin fixture create` - Create a new fixture (DM workflow with games + deadline, auto-creates prediction thread)
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
   - `IMPORT_ARCHIVE`: (Optional) Set to `true` to import `.sql` files from `archive/` on first run (validated via sandbox transaction, INSERT-only). Default: disabled.
   - `ENVIRONMENT`: (Optional) Set to `production` for live bot operation. Other values run smoke-test mode (validates config then exits). Default: `development`.

## Running Locally

If you know Python:

```bash
# Clone and setup
git clone https://github.com/adrunkhuman/matchday-typer
cd matchday-typer
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install .

# Run
export DISCORD_TOKEN="your_token"
python -m typer_bot
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

## Importing History
If you have a bunch of old scores you want to keep:
1. Put `.sql` files in `archive/` folder (check `example_import.sql` for format).
2. Start bot with a fresh database.
3. It'll import them automatically.

## Username Management

Usernames are updated automatically when users submit predictions.

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
