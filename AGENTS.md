# Typer Bot System Context

**Target Audience:** AI Agents (LLMs) working on this codebase.

## 1. Identity & Purpose
You are working on `matchday-typer`, a Discord bot for football prediction leagues.
- **Core Function:** Users predict scores -> Admins enter results -> Bot calculates points.
- **Vibe:** Functional, simple, reliable. No bloat.
- **Tech:** Python 3.10+, discord.py, aiosqlite, Railway hosting.

## 2. Critical Constraints
- **Persistence:** The database (`typer.db`) MUST live in `/app/data` on production (Railway volume).
- **Configuration:** All data paths configurable via env vars in `utils/config.py`:
  - `DATA_DIR`: Base directory (default: `/app/data`)
  - `DB_PATH`: Full database path (default: `{DATA_DIR}/typer.db`)
  - `BACKUP_DIR`: Backup storage (default: `{DATA_DIR}/backups`)
- **DM Workflow:** All complex inputs (predictions, fixture creation) happen in DMs to keep channel clean.
- **Async:** All database ops must be async (`aiosqlite`).
- **Parsing:** Use `utils.prediction_parser.parse_line_predictions` for all score parsing. Do NOT write ad-hoc regex.
- **Logging:** Use `typer_bot.utils.logger.setup_logging()` early. Do not use `print()`.
- **Timezones:** All datetime operations use timezone-aware objects. Use `utils.timezone.now()` instead of `datetime.now()`. Configure via `TZ` env var (default: Europe/Warsaw).

## 3. Database Schema
SQLite. Tables are initialized in `database.py`.

```sql
fixtures (
    id INTEGER PK,
    week_number INTEGER,
    games TEXT,       -- Newline separated: "Team A - Team B\nTeam C - Team D"
    deadline DATETIME,
    status TEXT       -- 'open' or 'closed'
)

predictions (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,     -- Discord ID
    predictions TEXT, -- Newline separated: "2-1\n1-1"
    is_late BOOLEAN
)

results (
    id INTEGER PK,
    fixture_id INTEGER FK,
    results TEXT      -- Newline separated actual scores
)

scores (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,
    points INTEGER,   -- 3 (exact), 1 (outcome), 0 (miss)
    exact_scores INTEGER,
    correct_results INTEGER
)
```

## 4. Codebase Map
- `typer_bot/bot.py`: Entry point, setup hook, archive import logic.
- `typer_bot/commands/user_commands.py`: `/predict`, `/standings` (Public).
- `typer_bot/commands/admin_commands.py`: `/admin` hub (Protected).
- `typer_bot/utils/config.py`: Centralized configuration (data paths via env vars).
- `typer_bot/utils/prediction_parser.py`: Central logic for parsing "2-1" or "2:1" strings.
- `typer_bot/utils/scoring.py`: Point calculation rules.
- `typer_bot/utils/logger.py`: structured logging configuration for Railway.
- `typer_bot/utils/db_backup.py`: Automatic database backup after fixture completion.
- `scripts/restore_db.py`: Manual database restore via Railway console.

## 5. Common Tasks
- **Fixing Parsing:** Edit `prediction_parser.py`.
- **New Commands:** Add Cog to `commands/` folder, load in `bot.py`.
- **Database Changes:** Edit `database.py` `initialize()` (Handle migrations manually if needed).
- **Debugging:** Check `utils/logger.py` for config. Set `LOG_LEVEL=DEBUG` in env.
- **Archive Import:** Set `IMPORT_ARCHIVE=true` to enable automatic import of historical data on fresh database.
- **Database Restore:** Use `scripts/restore_db.py` from Railway console for manual database restoration from backups.

## 6. Known Quirks
- **Double Digits:** Scores like `10-0` are allowed.
- **Format:** Users provide flexible separators (`-`, `:`, `–`).
- **History:** `archive/` folder contains SQL files auto-imported on first run (empty DB).
