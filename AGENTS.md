# Typer Bot System Context

**Target Audience:** AI Agents (LLMs) working on this codebase.

## 1. Identity & Purpose
You are working on `matchday-typer`, a Discord bot for football prediction leagues.
- **Core Function:** Users predict scores -> Admins enter results -> Bot calculates points.
- **Vibe:** Functional, simple, reliable. No bloat.
- **Tech:** Python 3.10+, discord.py, aiosqlite, Railway hosting.

## 2. Critical Constraints
- **Persistence:** The database (`typer.db`) MUST live in `/app/data` on production (Railway volume).
- **DM Workflow:** All complex inputs (predictions, fixture creation) happen in DMs to keep channel clean.
- **Async:** All database ops must be async (`aiosqlite`).
- **Parsing:** Use `utils.prediction_parser.parse_line_predictions` for all score parsing. Do NOT write ad-hoc regex.

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
- `typer_bot/utils/prediction_parser.py`: Central logic for parsing "2-1" or "2:1" strings.
- `typer_bot/utils/scoring.py`: Point calculation rules.

## 5. Common Tasks
- **Fixing Parsing:** Edit `prediction_parser.py`.
- **New Commands:** Add Cog to `commands/` folder, load in `bot.py`.
- **Database Changes:** Edit `database.py` `initialize()` (Handle migrations manually if needed).

## 6. Known Quirks
- **Double Digits:** Scores like `10-0` are allowed.
- **Format:** Users provide flexible separators (`-`, `:`, `–`).
- **History:** `archive/` folder contains SQL files auto-imported on first run (empty DB).
