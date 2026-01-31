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
1. **Create Fixture**: `/admin fixture` -> DM the list of games -> Set deadline.
2. **Game Over**: `/admin results` -> Enter the actual scores.
3. **Calc**: `/admin calculate` -> Bot does the math and posts the results.

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
   - `DB_PATH`: `/app/data/typer.db`
   - `REMINDER_CHANNEL_ID`: (Optional) ID of channel to spam reminders in.

## Running Locally

If you know Python:

```bash
# Clone and setup
git clone https://github.com/adrunkhuman/matchday-typer
cd matchday-typer
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .

# Run
export DISCORD_TOKEN="your_token"
python -m typer_bot
```

## Importing History
If you have a bunch of old scores you want to keep:
1. Put `.sql` files in the `archive/` folder (check `example_import.sql` for the format).
2. Start the bot with a fresh database.
3. It'll import them automatically.

## License
MIT. Do whatever you want with it.
