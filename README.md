# Typer Bot - Football Predictions Discord Bot

A Discord bot for managing weekly football prediction games with friends.

## Features

- **Easy Predictions**: Users submit predictions via simple `/predict 2-1 1-0 3-3...` command
- **Flexible Input**: Accepts `2-1`, `2:1`, `2 - 1`, `2- 1` formats
- **Admin Controls**: Create fixtures, enter results, calculate scores
- **Automatic Reminders**: Posts reminders Thursday 19:00 and Friday 17:00
- **Leaderboards**: View overall standings and last week's results
- **Late Penalty**: Predictions after deadline get -100% penalty

## Commands

### User Commands
- `/predict <scores>` - Submit predictions (e.g., `/predict 2-1 1-0 3-3...`)
- `/fixtures` - View current week's games
- `/standings` - View overall leaderboard
- `/mypredictions` - View your current predictions

### Admin Commands
- `/admin fixture <games>` - Create new fixture (paste 9-12 games, one per line)
- `/admin results <scores>` - Enter actual results
- `/admin calculate` - Calculate and post scores

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
4. Add environment variables in Railway dashboard:
   - `DISCORD_TOKEN`: Your bot token
   - `REMINDER_CHANNEL_ID`: Channel ID for reminders (optional)
5. Deploy!

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

## How It Works

### Weekly Flow

1. **Tuesday**: Admin creates fixture with `/admin fixture`
   ```
   /admin fixture Lech - Legia
   Pogoń - Arka
   Wisła - Cracovia
   ...
   ```

2. **Wednesday-Thursday**: Users submit predictions
   ```
   /predict 2-1 1-0 3-3 0-2 1-1 2-0 1-2 0-1 2-2
   ```

3. **Thursday 19:00**: Bot posts reminder

4. **Friday 17:00**: Bot posts final reminder

5. **After games**: Admin enters results
   ```
   /admin results 2-1 0-0 3-1 1-2 1-1 2-0 0-0 2-1 1-0
   ```

6. **Admin calculates**: `/admin calculate`
   - Bot calculates points
   - Posts results to channel
   - Updates leaderboard

### Scoring

- **Exact score**: 3 points
- **Correct result** (win/loss/draw): 1 point
- **Wrong**: 0 points
- **Late prediction**: -100% penalty (0 points)

## Database

Uses SQLite with persistent storage on Railway. Tables:
- `fixtures`: Weekly game fixtures
- `predictions`: User predictions
- `results`: Actual game results
- `scores`: Calculated points per user per week

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