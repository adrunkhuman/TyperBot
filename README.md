# Typer Bot - Football Predictions Discord Bot

A Discord bot for managing weekly football prediction games with friends.

## Features

- **DM-Based Predictions**: Interactive workflow via DMs for entering predictions
- **Inline Format**: Users see fixtures while predicting (`Team A - Team B 2:0`)
- **Admin DM Workflow**: Create fixtures and enter results through DMs
- **Custom Deadlines**: Admins can set custom deadlines when creating fixtures
- **Flexible Input**: Accepts `2-1`, `2:1`, `2 - 1` for scores
- **Automatic Reminders**: Posts reminders Thursday 19:00 and Friday 17:00
- **Leaderboards**: View overall standings and last week's results
- **Late Penalty**: Predictions after deadline get -100% penalty
- **Delete Fixtures**: Remove fixtures and clean database
- **Historical Archive**: Auto-import historical data from SQL files
- **Persistent Storage**: Database survives restarts and deployments

## Commands

### User Commands
- `/predict` - Start prediction submission (bot DMs you with fixture list)
- `/fixtures` - View current week's games
- `/standings` - View overall leaderboard
- `/mypredictions` - View your current predictions

### Admin Commands
- `/admin fixture` - Create new fixture via DM workflow
- `/admin results` - Enter results via DM workflow
- `/admin calculate` - Calculate and post scores
- `/admin delete` - Delete current fixture and clean database

## Setup

### 1. Create Discord Bot
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create new application
3. Go to "Bot" section, enable these intents:
   - Message Content Intent
4. Copy the bot token

### 2. Invite Bot to Server
1. OAuth2 > URL Generator
2. Select scopes: `bot`, `applications.commands`
3. Bot permissions: `Send Messages`, `Read Message History`, `Use Slash Commands`
4. Copy and open the URL to invite bot

### 3. Deploy on Railway (Recommended)

**Option A: Via Railway Dashboard**
1. Fork this repo to your GitHub
2. Go to [Railway](https://railway.app)
3. New Project > Deploy from GitHub repo
4. **IMPORTANT**: Add a persistent volume:
   - Go to "Volumes" tab in your Railway project
   - Click "New Volume"
   - Mount path: `/app/data`
   - This ensures database persists between restarts and deployments
5. Add environment variables in Railway dashboard:
   - `DISCORD_TOKEN`: Your bot token
   - `REMINDER_CHANNEL_ID`: Channel ID for reminders (optional)
   - `DB_PATH`: `/app/data/typer.db` (critical for persistence)
6. Deploy!

**⚠️ Important**: The volume persists through code pushes. Only delete the volume if you want to wipe ALL data.

**Option B: Railway CLI**
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Add environment variables
railway variables set DISCORD_TOKEN=your_token_here

# Deploy
railway up
```

### 4. Local Development

```bash
# Clone repo
git clone <your-repo>
cd typer-bot

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -e ".[dev]"

# Create .env file
cp .env.example .env
# Edit .env and add your DISCORD_TOKEN

# Run bot
python -m typer_bot
```

## Admin Setup

1. Create a Discord role called `typer-admin` (or use existing `Admin` role)
2. Assign this role to prediction admins
3. Admins can now use `/admin` commands
4. **Important**: Admins must allow DMs from server members for the bot to work

## How It Works

### Weekly Flow

1. **Tuesday**: Admin creates fixture
   - Type `/admin` → select "fixture"
   - Bot DMs admin asking for fixture list
   - Send games (one per line):
     ```
     Lech - Legia
     Pogoń - Arka
     Wisła - Cracovia
     ...
     ```
   - Choose deadline (default: next Friday 18:00, or custom date)
   - Preview and confirm

2. **Wednesday-Thursday**: Users submit predictions
   - Type `/predict`
   - Bot DMs user with fixture list
   - Reply with predictions in format:
     ```
     Lech - Legia 2:1
     Pogoń - Arka 0:0
     Wisła - Cracovia 1:2
     ...
     ```
   - Preview and confirm

3. **Thursday 19:00**: Bot posts reminder

4. **Friday 17:00**: Bot posts final reminder

5. **After games**: Admin enters results
   - Type `/admin` → select "results"
   - Bot DMs admin with fixture list
   - Reply with actual scores:
     ```
     Lech - Legia 1:0
     Pogoń - Arka 2:2
     Wisła - Cracovia 0:1
     ...
     ```
   - Preview and confirm

6. **Admin calculates**: `/admin` → "calculate"
   - Bot calculates points
   - Posts results to channel
   - Updates leaderboard

### Input Formats

**Predictions/Results (in DMs):**
```
Team A - Team B 2:0
Team C - Team D 1:1
Team E - Team F 0:2
```

Score separators: `2:0`, `2-0`, `2 : 0`, `2 - 0` all work

**Fixtures (in DMs):**
```
Lech Poznań - Legia Warszawa
Pogoń Szczecin - Arka Gdynia
...
```

### Scoring

- **Exact score**: 3 points
- **Correct result** (win/loss/draw): 1 point
- **Wrong**: 0 points
- **Late prediction**: -100% penalty (0 points)

### Custom Deadlines

When creating a fixture, you can:
- Use default (next Friday 18:00)
- Set custom deadline with formats:
  - `2024-02-15 18:00`
  - `15.02.2024 18:00`
  - `15/02/2024 18:00`

### Historical Data / Archive

The `archive/` folder contains SQL files for importing historical fixtures. On first startup (empty database), the bot automatically imports any `.sql` files found there.

**To add historical data:**
1. Create SQL file with fixture and predictions (see `archive/week1_import.sql` for example)
2. Add to `archive/` folder
3. Only works on fresh/empty database

### Deleting Fixtures

To remove a test fixture or start over:
- Type `/admin` → "delete"
- Bot shows current fixture
- Confirm deletion
- **Warning**: This deletes the fixture, all predictions, results, and scores

**⚠️ NEVER delete the Railway volume unless you want to lose ALL data!**

## Database

Uses SQLite with persistent storage on Railway. Tables:
- `fixtures`: Weekly game fixtures
- `predictions`: User predictions  
- `results`: Actual game results
- `scores`: Calculated points per user per week

## Troubleshooting

**"I can't send you DMs" error:**
- User needs to enable DMs from server members
- Server Settings > Privacy Settings > Allow DMs from server members

**Commands not showing:**
- Bot needs "Use Slash Commands" permission
- May take up to 1 hour for commands to sync globally

**Bot not responding to DMs:**
- Make sure you're not blocking the bot
- Check that `message_content` intent is enabled in Discord Developer Portal

## Project Structure

```
typer-bot/
├── typer_bot/
│   ├── __init__.py
│   ├── __main__.py
│   ├── bot.py              # Main bot class
│   ├── database/
│   │   ├── __init__.py
│   │   └── database.py     # SQLite operations
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── user_commands.py
│   │   └── admin_commands.py
│   └── utils/
│       ├── __init__.py
│       ├── prediction_parser.py
│       └── scoring.py
├── pyproject.toml
├── railway.toml
├── .env.example
└── README.md
```

## License

MIT License - feel free to use and modify for your own prediction games!
