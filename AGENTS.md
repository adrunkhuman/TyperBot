# AGENTS.md - Typer Bot Project Guide

## Project Overview

**Typer Bot** is a Discord bot for managing weekly football (soccer) prediction games. Users predict match scores, and the bot calculates points based on accuracy.

**Key Concepts:**
- **Fixture**: A weekly set of football matches (typically 9-10 games)
- **Prediction**: User's guessed scores for each game
- **Deadline**: When predictions close (typically Friday 18:00)
- **Results**: Actual match scores entered by admin after games finish
- **Points**: 3 for exact score, 1 for correct winner/draw, 0 otherwise

## Architecture

### Tech Stack
- **Python 3.10+** with `discord.py` v2.3+
- **SQLite** database via `aiosqlite` (async SQLite)
- **Railway** for hosting with persistent volume
- **Slash commands** for Discord interaction

### Project Structure
```
typer-bot/
├── typer_bot/
│   ├── __init__.py
│   ├── __main__.py              # Entry point
│   ├── bot.py                   # Main bot class, setup, events
│   ├── database/
│   │   ├── __init__.py
│   │   └── database.py          # All SQLite operations
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── user_commands.py     # /predict, /fixtures, /standings, /help
│   │   └── admin_commands.py    # /admin subcommands
│   └── utils/
│       ├── __init__.py
│       ├── prediction_parser.py # Input parsing utilities
│       └── scoring.py           # Point calculation logic
├── archive/                     # Historical data SQL files
│   └── week1_import.sql
├── data.txt                     # Raw historical data (temporary)
├── generate_sql.py              # Script to generate SQL from data.txt
├── import_historical.py         # One-time import script
├── pyproject.toml              # Dependencies
├── railway.toml                # Railway deployment config
├── .env.example                # Environment variables template
└── README.md                   # User documentation
```

## Database Schema

### Tables

**fixtures**
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
week_number INTEGER NOT NULL
games TEXT NOT NULL                    -- Newline-separated game list
deadline DATETIME NOT NULL
status TEXT DEFAULT 'open'             -- 'open' or 'closed'
created_at DATETIME DEFAULT CURRENT_TIMESTAMP
```

**predictions**
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
fixture_id INTEGER NOT NULL
user_id TEXT NOT NULL                  -- Discord user ID
user_name TEXT NOT NULL                -- Discord username
predictions TEXT NOT NULL              -- Newline-separated scores (e.g., "2-1\n0-0\n...")
submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
is_late BOOLEAN DEFAULT FALSE
UNIQUE(fixture_id, user_id)
```

**results**
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
fixture_id INTEGER NOT NULL
results TEXT NOT NULL                  -- Newline-separated actual scores
calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
```

**scores**
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
fixture_id INTEGER NOT NULL
user_id TEXT NOT NULL
user_name TEXT NOT NULL
points INTEGER NOT NULL
exact_scores INTEGER DEFAULT 0
correct_results INTEGER DEFAULT 0
UNIQUE(fixture_id, user_id)
```

### Data Format Notes

**Games are stored as single text field with newlines:**
```
"Lech - Legia\nPogoń - Arka\nWisła - Cracovia..."
```

**Predictions stored the same way:**
```
"2-1\n0-0\n1-2..."
```

When retrieving, use `.split("\n")` to get list.

## Command Structure

### User Commands

**`/predict`**
- Initiates DM workflow for submitting predictions
- Bot DMs user with current fixture
- User replies with scores in format: `Team A - Team B 2:0`
- Shows preview with confirmation buttons
- Only works if no prediction exists for user

**`/fixtures`**
- Displays current week's games
- Shows numbered list with deadline

**`/standings`**
- Shows overall leaderboard (all weeks combined)
- Shows last week's individual results
- Uses markdown tables

**`/mypredictions`**
- Shows user's current predictions for active fixture
- Shows status (on time/late) and submission time

**`/help`**
- Shows user help by default
- Detects admin role and shows admin help if applicable

### Admin Commands

**`/admin`** hub with choices:

1. **fixture** - Create new fixture via DM
   - Admin sends games (one per line)
   - Bot asks for deadline (default Friday 18:00 or custom)
   - Shows preview with confirmation
   - Announces in channel when created

2. **results** - Enter actual scores via DM
   - Bot DMs admin with fixture list
   - Admin replies with actual scores
   - Shows preview with confirmation
   - Does NOT calculate scores yet

3. **calculate** - Calculate and post results
   - Must have results entered first
   - Calculates points for all predictions
   - Posts results to channel
   - Updates leaderboard
   - Closes the fixture automatically

4. **delete** - Delete current fixture
   - Shows preview before deletion
   - Deletes fixture + all predictions + results + scores
   - Use with caution!

## Core Workflows

### Creating a Fixture (Admin)

1. Admin types `/admin` → selects "fixture"
2. Bot stores `pending_fixtures[user_id]` with channel_id
3. Bot DMs admin: "Send me the list of games..."
4. Admin replies with games (multiline)
5. Bot asks for deadline choice
6. Admin selects default or sends custom date
7. Bot shows preview with Confirm/Cancel buttons
8. On confirm:
   - Creates fixture in DB
   - Clears `pending_fixtures[user_id]`
   - Announces in original channel

**Key:** `pending_fixtures` dict stores state between messages

### Submitting Predictions (User)

1. User types `/predict`
2. Bot checks: no existing prediction, fixture exists
3. Bot stores `pending_predictions[user_id] = (fixture_id, games)`
4. Bot DMs user with fixture list and format example
5. User replies with predictions
6. Bot parses each line, extracts scores
7. Shows preview with Confirm/Cancel buttons
8. On confirm:
   - Saves to DB
   - Clears `pending_predictions[user_id]`

**Key:** Uses regex to parse various formats: `2:0`, `2-0`, `2 – 2`, etc.

### Entering Results (Admin)

Same flow as predictions but stores in `pending_results[user_id]`

### Calculating Scores

1. Admin types `/admin` → "calculate"
2. Bot verifies: fixture exists, results exist, predictions exist
3. For each prediction:
   - Compare each game prediction vs actual result
   - Exact match: 3 points
   - Correct outcome (win/loss/draw): 1 point
   - Wrong: 0 points
   - Track exact_scores and correct_results counts
4. Sort by total points descending
5. Save to scores table
6. Update fixture status to 'closed'
7. Post results to channel

## State Management

### Pending State Dictionaries

Located in command files, store temporary state for DM workflows:

```python
# admin_commands.py
pending_fixtures = {}  # user_id -> {"channel_id": int, "games": list, ...}
pending_results = {}   # user_id -> fixture_id

# user_commands.py  
pending_predictions = {}  # user_id -> (fixture_id, games_list)
```

**Important:** These must be cleared after confirm/cancel to prevent state leakage!

## Key Implementation Details

### DM Handling

Both user_commands and admin_commands have `on_message` listeners:

```python
@commands.Cog.listener()
async def on_message(self, message):
    if message.author.bot or message.guild is not None:
        return  # Ignore non-DMs
    
    user_id = str(message.author.id)
    
    # Check admin_commands first
    if user_id in pending_fixtures:
        await self._handle_fixture_dm(message, user_id)
        return
    if user_id in pending_results:
        await self._handle_results_dm(message, user_id)
        return
        
    # Then user_commands
    if user_id in pending_predictions:
        # handle prediction...
```

**Order matters!** Admin commands checked first.

### Score Parsing

Flexible regex handles multiple formats:
```python
# Pattern: captures score at end of line
match = re.search(r'(\d+)\s*[-:]\s*(\d+)\s*$', line)
if match:
    home, away = match.group(1), match.group(2)
```

Supports: `2:0`, `2-0`, `2 : 0`, `2- 0`, etc.

### Deadline Handling

Default deadline: Next Friday 18:00
```python
days_until_friday = (4 - now.weekday()) % 7
if days_until_friday == 0 and now.hour >= 18:
    days_until_friday = 7
deadline = now + timedelta(days=days_until_friday)
deadline = deadline.replace(hour=18, minute=0, second=0, microsecond=0)
```

Custom deadlines accepted in formats:
- `2024-02-15 18:00`
- `15.02.2024 18:00`
- `15/02/2024 18:00`

### Late Predictions

Checked at submission time:
```python
is_late = datetime.now() > fixture["deadline"]
```

Late predictions are stored with `is_late=True` and get 0 points regardless of accuracy.

## Environment Variables

Required:
- `DISCORD_TOKEN` - Bot token from Discord Developer Portal

Optional:
- `REMINDER_CHANNEL_ID` - Channel for automatic reminders
- `DB_PATH` - Database file path (default: `typer.db`)
- `TZ` - Timezone (default: `Europe/Warsaw`)

For Railway production:
```
DB_PATH=/app/data/typer.db
```

## Common Tasks for AI Agents

### Adding a New Command

1. Determine if user or admin command
2. Add to appropriate file in `commands/`
3. Use `@app_commands.command()` decorator
4. Use `async def` with `interaction: discord.Interaction`
5. Access database via `self.db`
6. Respond with `interaction.response.send_message()`
7. Run `ruff check` and `ruff format`
8. Test!

### Modifying Database Schema

1. Edit `database/database.py`
2. Update `initialize()` method with new CREATE TABLE or ALTER
3. Consider migration strategy for existing data
4. Test on fresh database first

### Fixing a Bug

1. Check logs in Railway dashboard
2. Look for error messages and stack traces
3. Common issues:
   - State not cleared from pending dicts
   - Discord permissions missing
   - Database path wrong (ephemeral storage)
   - Slash commands not synced

### Adding Historical Data

1. Create SQL file in `archive/` folder
2. Follow format from `week1_import.sql`
3. Use actual newlines in games/predictions fields
4. Bot auto-imports on first startup if DB is empty
5. Or run SQL manually via Railway shell

## Testing Checklist

Before pushing changes:
- [ ] `ruff check` passes
- [ ] `ruff format` passes
- [ ] Test locally if possible
- [ ] Check Railway logs after deploy
- [ ] Verify database persistence (check volume)

## Important Notes

### Railway Volume Persistence

- **Survives:** Code pushes, restarts, environment variable changes
- **Does NOT survive:** Volume deletion, project deletion
- **Path:** Must match `DB_PATH` env var (`/app/data/typer.db`)
- **Backup:** Download via Railway dashboard periodically

### Discord Intents

Required intents in Discord Developer Portal:
- **Message Content Intent** - For reading DM content
- Without this, DMs won't work!

### Command Syncing

Slash commands may take up to 1 hour to appear globally.
For faster testing:
- Use guild-specific sync (faster)
- Or restart bot after code changes

### Double-Digit Scores

Current implementation supports any score (including troll 99-99).
Original requirement was single-digit only, but historical data needed support for doubles.

### Admin Role Check

Two ways to be admin:
- Discord role named "Admin"
- Discord role named "typer-admin"

Check function:
```python
def is_admin(self, member: discord.Member) -> bool:
    admin_roles = {"Admin", "typer-admin"}
    return any(role.name in admin_roles for role in member.roles)
```

## Troubleshooting

**"I can't send you DMs" error:**
- User has DMs disabled from server members
- Server Settings > Privacy Settings > Allow DMs

**Bot not responding to commands:**
- Check `DISCORD_TOKEN` is correct
- Check bot has "Use Slash Commands" permission
- Check Message Content Intent is enabled

**Database resets on restart:**
- `DB_PATH` not set correctly
- Volume not mounted at `/app/data`
- Using default `typer.db` (local file, not persistent)

**Import not working:**
- Database not empty (import only runs on empty DB)
- SQL syntax error in archive file
- Wrong newline format in SQL (use actual newlines, not `\n`)

## Future Enhancements (Ideas)

- Web dashboard for viewing standings
- Export to CSV/Excel
- Support for multiple leagues/competitions
- Automated score fetching from football APIs
- Reminder customization per user
- Statistics (best predictor, streaks, etc.)

## File Reference

Quick guide to what each file does:

- **bot.py**: Bot initialization, event handlers, archive import logic
- **database.py**: All SQL queries and database operations
- **user_commands.py**: User-facing slash commands
- **admin_commands.py**: Admin slash commands, DM handlers
- **prediction_parser.py**: Input parsing utilities
- **scoring.py**: Point calculation logic
- **week1_import.sql**: Example historical data format

## Questions?

If you're an AI agent working on this project:
1. Read the relevant section in this file
2. Check the code in the corresponding module
3. Look at existing examples (similar commands/workflows)
4. Test thoroughly before pushing
5. Update this file if you add significant new features

**Project Owner:** @adrunkhuman (GitHub)
**Created:** January 2026
**Status:** Production Ready
