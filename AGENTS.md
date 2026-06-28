# Typer Bot System Context

**Target Audience:** AI Agents (LLMs) working on this codebase.

## 1. Identity & Purpose
You are working on `TyperBot`, a Discord bot for football prediction leagues.
- **Core Function:** Users predict scores -> Admins enter results -> Bot calculates points.
- **Vibe:** Functional, simple, reliable. No bloat.
- **Tech:** Python 3.13+, discord.py, aiosqlite, portable container hosting.

## 2. Critical Constraints
- **Product Model:** One hosted TyperBot application serves many Discord guilds. Server admins invite/configure the bot; they do not self-host it.
- **League Scope:** Keep v3 simple: one league, one active season, one configured league channel, and one active scoring-rule set per guild.
- **Persistence:** The database defaults to `./data/typer.db` locally. On production, set `DATA_DIR=/app/data` so the live DB stays on the mounted data volume.
- **Schema Changes:** Do not add broad startup compatibility migrations or backfills for historical schemas. Existing production DB changes should be verified and ported manually when needed; startup should validate/fail fast for unsafe current-schema violations.
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
- **Parsing:** Use `utils.prediction_parser.parse_prediction_lines` for all score parsing. Do NOT write ad-hoc regex.
- **Logging:** Use `typer_bot.utils.logger.setup_logging()` early. Do not use `print()`.
- **Timezones:** All datetime operations use timezone-aware objects. Use `utils.timezone.now()` instead of `datetime.now()`. Configure via `TZ` env var (default: `UTC`).
- **Permissions:** Bot requires `Send Messages`, `Send Messages in Threads`, `Read Message History`, `Add Reactions`, `Create Public Threads`, and `Use Slash Commands`.
- **Discord Application:** Production application needs `Message Content Intent` and `Server Members Intent` enabled in the Discord Developer Portal.

## 3. Database Schema
SQLite. Tables are initialized in `typer_bot/database/connection.py`.

```sql
seasons (
    id INTEGER PK,
    guild_id TEXT,
    name TEXT,
    status TEXT DEFAULT 'active',       -- 'active' or 'archived'
    exact_score_points INTEGER DEFAULT 3,
    correct_outcome_points INTEGER DEFAULT 1,
    wrong_outcome_points INTEGER DEFAULT 0,
    late_prediction_points INTEGER DEFAULT 0,
    created_at DATETIME,
    ended_at DATETIME
)

fixtures (
    id INTEGER PK,
    guild_id TEXT,                   -- Discord guild/server ID owning the league state
    season_id INTEGER FK,
    week_number INTEGER,
    games TEXT,                      -- Newline separated: "Team A - Team B\nTeam C - Team D"
    deadline DATETIME,
    status TEXT DEFAULT 'open',      -- 'open' or 'closed'
    message_id TEXT,                 -- Discord message ID (thread shares same snowflake ID)
    channel_id TEXT,
    created_at DATETIME
)

predictions (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,                    -- Discord ID
    user_name TEXT,
    predictions TEXT,                -- Newline separated: "2-1\n1-1"
    submitted_at DATETIME,
    is_late BOOLEAN,
    late_penalty_waived BOOLEAN,
    admin_edited_at DATETIME,
    admin_edited_by TEXT,
    predicted_game_indexes TEXT,     -- Sparse partial predictions map to fixture game indexes
    pending_partial_approval BOOLEAN,
    public_message_id TEXT,
    public_message_kind TEXT,
    UNIQUE(fixture_id, user_id)
)

results (
    id INTEGER PK,
    fixture_id INTEGER FK,
    results TEXT,                    -- Newline separated actual scores
    calculated_at DATETIME,
    updated_at DATETIME,
    UNIQUE(fixture_id)
)

scores (
    id INTEGER PK,
    fixture_id INTEGER FK,
    user_id TEXT,
    user_name TEXT,
    points INTEGER,                  -- Calculated from the fixture season's scoring rules
    exact_scores INTEGER,
    correct_results INTEGER,
    UNIQUE(fixture_id, user_id)
)

guild_config (
    guild_id TEXT PK,
    admin_role_id TEXT,
    league_channel_id TEXT,
    active_season_id INTEGER,
    created_at DATETIME,
    updated_at DATETIME
)
```

## 4. Codebase Map
- `typer_bot/bot.py`: Entry point, setup hook, startup/guild lifecycle logging, and guild permission checks.
- `typer_bot/commands/user_commands.py`: Public slash commands, including modal-driven `/predict` flow.
- `typer_bot/commands/admin_panel/`: Admin panel UI views, selects, and modals split out of `admin_commands.py`.
- `typer_bot/handlers/thread_prediction_handler.py`: Thread-based prediction processing (on_message) plus thread prediction cooldown state.
- `typer_bot/commands/admin_commands.py`: `/admin` command surface and orchestration for admin workflows, including admin calculation cooldown state.
- `typer_bot/services/calculation_posting.py`: Post-calculation side effects: best-effort DB backup, league-channel publishing, and admin interaction responses.
- `typer_bot/utils/config.py`: Centralized configuration (data paths via env vars).
- `typer_bot/utils/discord_messages.py`: Shared Discord 2000-character-safe message chunking helpers.
- `typer_bot/utils/prediction_parser.py`: Central logic for parsing "2-1" or "2:1" strings.
- `typer_bot/utils/scoring.py`: Point calculation using season scoring rules.
- `typer_bot/utils/logger.py`: plain stdout logging setup with contextual fields.
- `typer_bot/utils/db_backup.py`: Automatic database backup after successful score calculation.
- `scripts/restore_db.py`: Manual database restore from a host or container shell.

## 5. Common Tasks
- **Fixing Parsing:** Edit `prediction_parser.py`.
- **Admin Panel UI:** Edit `commands/admin_panel/`.
- **Workflow/Cooldown State:** Thread prediction cooldowns live in `handlers/thread_prediction_handler.py`; admin calculate cooldowns live in `commands/admin_commands.py`. Keep them process-local instead of introducing module-level globals.
- **New Commands:** Add Cog to `commands/` folder, load in `bot.py`.
- **Database Changes:** Edit `typer_bot/database/connection.py` `initialize()` and the focused repositories in `typer_bot/database/`. Keep fresh schema creation and startup validation explicit; handle production data ports manually when needed.
- **Debugging:** Check `utils/logger.py` for config. Set `LOG_LEVEL=DEBUG` in env.
- **Discord Message Lengths:** Use `utils/discord_messages.py` helpers instead of ad-hoc 2000-character splitting.
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
- Prefer behavior and contract assertions over exact UI layout, log wording, mock-call choreography, or source-string checks.
- Delete low-value tests that only verify Python/library behavior or fixture setup.

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
- **Production Data:** Set `DATA_DIR=/app/data` on the persistent volume. `DB_PATH` defaults to `{DATA_DIR}/typer.db`; `BACKUP_DIR` defaults to `{DATA_DIR}/backups`.
- **Backups:** Automatic SQL backups are named `backup_YYYY-MM-DD_HH-MM-SS-ms.sql`. `scripts/restore_db.py` accepts one explicit backup file path, not a wildcard.
- **Portability:** Works on any platform (Coolify, Railway, local, etc.) - just set the variable accordingly
