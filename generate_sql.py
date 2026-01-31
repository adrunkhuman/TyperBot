"""Generate SQL INSERT statements from data.txt for manual import."""

import re
from datetime import datetime


def parse_prediction_line(line: str) -> tuple[str, str] | None:
    """Parse a prediction line and extract score."""
    line = line.strip()
    if not line:
        return None

    # Pattern 1: Standard format "Team A – Team B 2:0"
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", line)
    if match:
        return match.group(1), match.group(2)

    # Pattern 2: Reversed "Team A 2:0 Team B"
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s+\w", line)
    if match:
        return match.group(1), match.group(2)

    # Pattern 3: En-dash format "Team A 2 – 2 Team B"
    match = re.search(r"(\d+)\s*–\s*(\d+)", line)
    if match:
        return match.group(1), match.group(2)

    return None


def parse_data_file(filepath: str) -> tuple[list[str], dict]:
    """Parse data.txt and extract games and predictions."""
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Split into sections
    sections = content.split("\n\n")

    # Parse games - skip "Games:" header and get actual games
    games_section = sections[0].strip()
    games_lines = [line.strip() for line in games_section.split("\n") if line.strip()]
    # Remove "Games:" header if present
    if games_lines and games_lines[0] == "Games:":
        games_lines = games_lines[1:]
    games = games_lines

    # Parse predictions
    predictions = {}
    current_user = None
    current_predictions = []

    for section in sections[1:]:
        lines = section.strip().split("\n")
        if not lines:
            continue

        # Check if first line is a user ID (all digits)
        first_line = lines[0].strip()
        if first_line.isdigit():
            # Save previous user's predictions
            if current_user and current_predictions:
                predictions[current_user] = current_predictions

            # Start new user
            current_user = first_line
            current_predictions = []
            lines = lines[1:]  # Remove user ID from lines

        # Parse prediction lines for current user
        for line in lines:
            line = line.strip()
            if not line or line.startswith("Predictions:"):
                continue

            score = parse_prediction_line(line)
            if score:
                current_predictions.append(f"{score[0]}-{score[1]}")

    # Save last user
    if current_user and current_predictions:
        predictions[current_user] = current_predictions

    return games, predictions


def generate_sql():
    """Generate SQL INSERT statements."""
    games, predictions = parse_data_file("data.txt")

    sql_lines = []
    sql_lines.append("-- Historical data import for Week 1")
    sql_lines.append(f"-- Generated: {datetime.now().isoformat()}")
    sql_lines.append("")
    sql_lines.append("-- Games list:")
    for i, game in enumerate(games, 1):
        sql_lines.append(f"-- {i}. {game}")
    sql_lines.append("")

    # Create fixture
    deadline = "2026-01-29 18:00:00"
    games_str = "\\n".join(games)
    sql_lines.append("-- Insert fixture")
    sql_lines.append("INSERT INTO fixtures (week_number, games, deadline, status, created_at)")
    sql_lines.append(
        "VALUES (1, '{}', '{}', 'open', '{}');".format(
            games_str.replace("'", "''"), deadline, datetime.now().isoformat()
        )
    )
    sql_lines.append("")

    # Note: We need to get the fixture_id after insert
    sql_lines.append("-- Get fixture_id (will be 1 if first fixture)")
    sql_lines.append("-- Use fixture_id = 1 for predictions below")
    sql_lines.append("")

    # Insert predictions
    submitted_at = "2026-01-29 17:00:00"
    sql_lines.append("-- Insert predictions")

    for user_id, user_predictions in predictions.items():
        # Ensure we have exactly 10 predictions
        while len(user_predictions) < 10:
            user_predictions.append("0-0")

        pred_str = "\\n".join(user_predictions)
        sql_lines.append(
            "INSERT INTO predictions (fixture_id, user_id, user_name, predictions, submitted_at, is_late)"
        )
        sql_lines.append(
            f"VALUES (1, '{user_id}', 'User_{user_id[:8]}', '{pred_str}', '{submitted_at}', 0);"
        )
        sql_lines.append("")

    sql_lines.append("-- Import complete")
    sql_lines.append(f"-- Total users imported: {len(predictions)}")

    return "\n".join(sql_lines)


if __name__ == "__main__":
    sql = generate_sql()
    print(sql)

    # Save to file
    with open("archive/week1_import.sql", "w", encoding="utf-8") as f:
        f.write(sql)

    print("\n" + "=" * 50)
    print("SQL saved to: archive/week1_import.sql")
    print("=" * 50)
