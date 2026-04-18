# Typer Bot System Context

**Target Audience:** AI Agents (LLMs) working on this codebase.

## 1. Identity & Purpose
You are working on `TyperBot`, a Discord bot for football prediction leagues.
- **Core Function:** Users predict scores -> Admins enter results -> Bot calculates points.
- **Vibe:** Functional, simple, reliable. No bloat.
- **Tech:** Python 3.10+, discord.py, aiosqlite, portable container hosting.

## 2. Critical Constraints
- **Persistence:** The database defaults to `./data/typer.db` locally. On production, set `DATA_DIR=/app/data` so the live DB stays on the mounted data volume.
- **Transaction Safety:** Critical operations use atomic transactions (BEGIN/COMMIT/ROLLBACK) to ensure data consistency. Never modify transaction logic without understanding rollback implications.
- **Prediction Contract:** Fixture threads are the public source of truth. `/predict` is a structured composer that posts publicly into the selected fixture thread.
- **Configuration:** All data paths configurable via env vars in `utils/config.py`:
  - `DATA_DIR`: Base directory (default: `./data`)
  - `DB_PATH`: Full database path (default: `{DATA_DIR}/typer.db`)
  - `BACKUP_DIR`: Backup storage (default: `{DATA_DIR}/backups`)
- **Modal Workflow:** Complex inputs (fixture creation, results entry, `/predict`) use Discord modals instead of DM sessions.
- **Thread Predictions:** Users can post predictions in public threads under fixture announcements.
- **Rate Limiting:** Thread predictions are rate-limited to 1 per second per user. Cooldown entries auto-expire after 1 hour.
- **Async:** All database ops must be async (`aiosqlite`).
- **Parsing:** Use `utils.prediction_parser.parse_line_predictions` for all score parsing. Do NOT write ad-hoc regex.
- **Logging:** Use `typer_bot.utils.logger.setup_logging()` early. Do not use `print()`.
- **Timezones:** All datetime operations use timezone-aware objects. Use `utils.timezone.now()` instead of `datetime.now()`. Configure via `TZ` env var (default: `UTC`).
- **Permissions:** Bot requires `Send Messages`, `Read Message History`, `Add Reactions`, and `Create Public Threads`.

## 3. Database Schema
SQLite. Tables are initialized in `typer_bot/database/connection.py`.

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
- `typer_bot/bot.py`: Entry point and setup hook.
- `typer_bot/commands/user_commands.py`: Public slash commands, including modal-driven `/predict` flow.
- `typer_bot/commands/admin_panel/`: Admin panel UI views, selects, and modals split out of `admin_commands.py`.
- `typer_bot/handlers/thread_prediction_handler.py`: Thread-based prediction processing (on_message) plus thread prediction cooldown state.
- `typer_bot/commands/admin_commands.py`: `/admin` command surface and orchestration for admin workflows, including admin calculation cooldown state.
- `typer_bot/utils/config.py`: Centralized configuration (data paths via env vars).
- `typer_bot/utils/prediction_parser.py`: Central logic for parsing "2-1" or "2:1" strings.
- `typer_bot/utils/scoring.py`: Point calculation rules.
- `typer_bot/utils/logger.py`: structured logging configuration for local and deployed environments.
- `typer_bot/utils/db_backup.py`: Automatic database backup after successful score calculation.
- `scripts/restore_db.py`: Manual database restore from a host or container shell.

## 5. Common Tasks
- **Fixing Parsing:** Edit `prediction_parser.py`.
- **Admin Panel UI:** Edit `commands/admin_panel/`.
- **Workflow/Cooldown State:** Thread prediction cooldowns live in `handlers/thread_prediction_handler.py`; admin calculate cooldowns live in `commands/admin_commands.py`. Keep them process-local instead of introducing module-level globals.
- **New Commands:** Add Cog to `commands/` folder, load in `bot.py`.
- **Database Changes:** Edit `typer_bot/database/connection.py` `initialize()` and the focused repositories in `typer_bot/database/` (handle migrations manually if needed).
- **Debugging:** Check `utils/logger.py` for config. Set `LOG_LEVEL=DEBUG` in env.
- **Database Restore:** Use `scripts/restore_db.py` from the host or container shell for manual database restoration from backups. The script restores into a temporary SQLite file first, then atomically replaces the live DB only after success.

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
- **Rate Limiting:** Thread predictions limited to 1/second per user.
- **Workflow State Ownership:** Process-local thread/admin cooldowns live with their owning handlers/commands; they are not persisted and reset on process restart.
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
- Tool: `ty`
- Run: `ty check typer_bot`

## 8. Deployment Environment
- **Configuration:** The `ENVIRONMENT` variable controls bot behavior:
  - `production`: Production deployment label
  - Not set/`development`: Non-production environment label
- **Runtime:** The bot still connects to Discord in non-production environments; use a separate token for manual testing and previews
- **Token Safety:** Any deployment with a valid token will connect; never run multiple environments against the same live token
- **Production:** Set `ENVIRONMENT=production` in deployment variables for production deployments
- **Portability:** Works on any platform (Coolify, Railway, local, etc.) - just set the variable accordingly
