# Typer Bot System Context

**Target Audience:** AI Agents (LLMs) working on this codebase.

## 1. Identity & Purpose
You are working on `matchday-typer`, a Discord bot for football prediction leagues.
- **Core Function:** Users predict scores -> Admins enter results -> Bot calculates points.
- **Vibe:** Functional, simple, reliable. No bloat.
- **Tech:** Python 3.10+, discord.py, aiosqlite, Railway hosting.

## 2. Critical Constraints
- **Persistence:** The database (`typer.db`) MUST live in `/app/data` on production (Railway volume).
- **Transaction Safety:** Critical operations use atomic transactions (BEGIN/COMMIT/ROLLBACK) to ensure data consistency. Never modify transaction logic without understanding rollback implications.
- **Race Condition Prevention:** Both DM and thread prediction handlers check for existing predictions before saving to prevent duplicates when users submit via both methods simultaneously.
- **Archive Import Security:** SQL files in `archive/` are validated using sandbox transactions before execution. Only INSERT statements allowed; ATTACH/DETACH/VACUUM/PRAGMA blocked.
- **Configuration:** All data paths configurable via env vars in `utils/config.py`:
  - `DATA_DIR`: Base directory (default: `/app/data`)
  - `DB_PATH`: Full database path (default: `{DATA_DIR}/typer.db`)
  - `BACKUP_DIR`: Backup storage (default: `{DATA_DIR}/backups`)
- **DM Workflow:** Complex inputs (fixture creation, results entry) happen in DMs to keep channel clean.
- **Thread Predictions:** Users can post predictions in public threads under fixture announcements (NEW - see handlers/thread_prediction_handler.py).
- **Rate Limiting:** Thread predictions are rate-limited to 1 per second per user. Cooldown entries auto-expire after 1 hour.
- **Async:** All database ops must be async (`aiosqlite`).
- **Parsing:** Use `utils.prediction_parser.parse_line_predictions` for all score parsing. Do NOT write ad-hoc regex.
- **Logging:** Use `typer_bot.utils.logger.setup_logging()` early. Do not use `print()`.
- **Timezones:** All datetime operations use timezone-aware objects. Use `utils.timezone.now()` instead of `datetime.now()`. Configure via `TZ` env var (default: Europe/Warsaw).
- **Permissions:** Bot requires `Send Messages`, `Read Message History`, `Add Reactions`, and `Create Public Threads`.

## 3. Database Schema
SQLite. Tables are initialized in `database.py`.

```sql
fixtures (
    id INTEGER PK,
    week_number INTEGER,
    games TEXT,                      -- Newline separated: "Team A - Team B\nTeam C - Team D"
    deadline DATETIME,
    status TEXT DEFAULT 'open',      -- 'open' or 'closed'
    message_id TEXT                  -- Discord message ID (thread shares same snowflake ID)
)

predictions (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,                    -- Discord ID
    predictions TEXT,                -- Newline separated: "2-1\n1-1"
    is_late BOOLEAN
)

results (
    id INTEGER PK,
    fixture_id INTEGER FK,
    results TEXT                     -- Newline separated actual scores
)

scores (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,
    points INTEGER,                  -- 3 (exact), 1 (outcome), 0 (miss)
    exact_scores INTEGER,
    correct_results INTEGER
)
```

## 4. Codebase Map
- `typer_bot/bot.py`: Entry point, setup hook, archive import logic.
- `typer_bot/commands/user_commands.py`: `/predict`, `/standings` (Public).
- `typer_bot/commands/admin_commands.py`: `/admin` hub (Protected).
- `typer_bot/handlers/thread_prediction_handler.py`: Thread-based prediction processing (on_message, on_edit).
- `typer_bot/handlers/fixture_handler.py`: DM workflow for fixture creation.
- `typer_bot/handlers/results_handler.py`: DM workflow for results entry.
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

## 5.5 Testing Guidelines

When modifying code, ensure tests pass and add tests for new functionality.

**Test Organization:**
- `tests/test_*.py` - Unit tests mirroring source structure
- `tests/test_integration.py` - End-to-end workflows (fixture → predictions → results → scores)
- `tests/conftest.py` - Shared fixtures (mock Discord objects, temp database)

**Key Patterns:**
- **Async tests:** Use `@pytest.mark.asyncio` decorator
- **Discord mocking:** Use fixtures from `conftest.py` (`mock_interaction`, `mock_user`, `mock_thread`, etc.)
- **Database:** The `database` fixture provides isolated temp database per test
- **Time:** Use `freezegun` for time-sensitive tests

**Adding Tests:**
- Mirror the source file structure (e.g., `commands/admin_commands.py` → `tests/test_admin_commands.py`)
- Use descriptive test names: `test_rejects_non_admin_users` not `test_admin_1`
- Group related tests in classes (e.g., `class TestAdminOnlyDecorator`)
- Mock external dependencies (Discord API, time) - never hit real services

**Running Tests:**
```bash
uv run pytest                    # All tests
uv run pytest -x                 # Stop on first failure
uv run pytest -v -k "admin"      # Run tests matching "admin"
uv run pytest --tb=short         # Shorter traceback output
```

## 6. Known Quirks
- **Double Digits:** Scores like `10-0` are allowed.
- **Format:** Users provide flexible separators (`-`, `:`, `–`).
- **History:** `archive/` folder contains SQL files auto-imported on first run (empty DB).
- **Rate Limiting:** Thread predictions limited to 1/second per user. DM predictions have no rate limit.
- **Session Timeouts:** Fixture creation and results entry DM flows auto-expire after 1 hour of inactivity.
- **Token Safety:** Bot validates DISCORD_TOKEN at startup (rejects placeholders like "your_bot_token_here"). Token values are never logged.

## 7. Code Quality & Pre-commit Hooks

**Setup (one-time):**
```bash
# Install dependencies (includes ty as dev dependency)
uv sync --group dev

# Install prek (Rust-based pre-commit hooks, 10-100x faster than pre-commit)
uv tool install prek

# Install the git hooks
prek install

# Verify hooks are active
ls .git/hooks/pre-commit  # Should exist (not .sample)
```

**Pre-commit Hooks:**
Configured in `.pre-commit-config.yaml`:
- **ruff check --fix** - Linting with auto-fix
- **ruff format** - Code formatting
- **ty check** - Type checking (blocking in CI)

**Running manually:**
```bash
prek run --all-files     # Run all hooks on all files
prek run ruff            # Run specific hook
```

**Type Checking:**
- Tool: `ty` (Astral's type checker, 10-100x faster than mypy)
- Current status: **0 errors** (complete)
- Run: `ty check typer_bot`

## 8. Deployment Environment
- **Configuration:** The `ENVIRONMENT` variable controls bot behavior:
  - `production`: Bot connects to Discord and runs normally
  - Not set/`development`: Bot runs in "smoke test" mode - validates config then exits
- **Purpose:** Prevents race conditions when multiple deployments (e.g., PR previews) share the same Discord token
- **Railway:** Set `ENVIRONMENT=production` in Railway variables for production deployments
- **Portability:** Works on any platform (Railway, Coolify, local, etc.) - just set the variable accordingly
