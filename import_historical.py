"""Import historical fixture and predictions from data.txt into the database."""

import asyncio
import os
import re
from datetime import datetime

import aiohttp

DB_PATH = os.getenv("DB_PATH", "/app/data/typer.db")


async def fetch_discord_username(user_id: str, bot_token: str) -> str:
    """Fetch Discord username for a user ID."""
    url = f"https://discord.com/api/v10/users/{user_id}"
    headers = {"Authorization": f"Bot {bot_token}"}

    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            return data.get("username", f"User_{user_id[:8]}")
        else:
            return f"User_{user_id[:8]}"


def parse_prediction_line(line: str) -> tuple[str, str] | None:
    """Parse a prediction line and extract score.

    Handles formats:
    - Team A – Team B 2:0
    - Team A 2:0 Team B
    - Team A 2 – 2 Team B
    - Team: 2:3
    """
    line = line.strip()
    if not line:
        return None

    # Standard format: "Team A – Team B 2:0"
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", line)
    if match:
        return match.group(1), match.group(2)

    # Reversed: "Team A 2:0 Team B"
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s+\w", line)
    if match:
        return match.group(1), match.group(2)

    # En-dash format: "Team A 2 – 2 Team B"
    match = re.search(r"(\d+)\s*–\s*(\d+)", line)
    if match:
        return match.group(1), match.group(2)

    return None


def parse_data_file(filepath: str) -> tuple[list[str], dict]:
    """Parse data.txt and extract games and predictions."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    sections = content.split("\n\n")

    games_section = sections[0].replace("Games:\n", "").strip()
    games = [line.strip() for line in games_section.split("\n") if line.strip()]

    predictions = {}
    current_user = None
    current_predictions = []

    for section in sections[1:]:
        lines = section.strip().split("\n")
        if not lines:
            continue

        first_line = lines[0].strip()
        if first_line.isdigit():
            if current_user and current_predictions:
                predictions[current_user] = current_predictions

            current_user = first_line
            current_predictions = []
            lines = lines[1:]

        for line in lines:
            line = line.strip()
            if not line or line.startswith("Predictions:"):
                continue

            score = parse_prediction_line(line)
            if score:
                current_predictions.append(f"{score[0]}-{score[1]}")

    if current_user and current_predictions:
        predictions[current_user] = current_predictions

    return games, predictions


async def import_data(bot_token: str):
    """Import historical data into database."""
    from typer_bot.database.database import Database

    games, predictions = parse_data_file("data.txt")

    print(f"Found {len(games)} games")
    print(f"Found {len(predictions)} users with predictions")

    db = Database(DB_PATH)
    await db.initialize()

    deadline = datetime(2026, 1, 29, 18, 0, 0)
    fixture_id = await db.create_fixture(1, games, deadline)
    print(f"Created fixture with ID: {fixture_id}")

    for user_id, user_predictions in predictions.items():
        # Backfill missing predictions with 0-0
        while len(user_predictions) < 10:
            user_predictions.append("0-0")

        username = await fetch_discord_username(user_id, bot_token)

        await db.save_prediction(
            fixture_id=fixture_id,
            user_id=user_id,
            user_name=username,
            predictions=user_predictions,
            is_late=False,
        )
        print(f"Imported predictions for {username} ({user_id})")

    print("\nImport complete!")
    print(f"Fixture: Week 1 with {len(games)} games")
    print(f"Deadline: {deadline}")
    print(f"Predictions: {len(predictions)} users")


if __name__ == "__main__":
    import sys

    bot_token = os.getenv("DISCORD_TOKEN")
    if not bot_token:
        print("Error: DISCORD_TOKEN environment variable not set!")
        print("\nTo run locally:")
        print("  DISCORD_TOKEN=your_token python import_historical.py")
        print("\nTo run on Railway:")
        print("  railway run python import_historical.py")
        sys.exit(1)

    asyncio.run(import_data(bot_token))
