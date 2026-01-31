"""Prediction parsing utilities."""

import re


def parse_predictions(input_text: str, expected_count: int = 9) -> tuple[list[str], list[str]]:
    """Parse predictions from user input.

    Accepts formats like:
    - "2-1 1-0 3-3 0-2..."
    - "2:1, 1:0, 3:3..."
    - "2 - 1, 1- 0, 2-0..."

    Returns: (valid_predictions, errors)
    """
    # Normalize input: replace commas with spaces, normalize separators
    normalized = input_text.replace(",", " ")

    # Pattern to match scores like "2-1", "2:1", "2 - 1", "2- 1"
    # Matches optional whitespace, digit(s), separator, digit(s), optional whitespace
    pattern = r"\s*(\d+)\s*[-:]\s*(\d+)\s*"

    predictions = []
    errors = []

    # Find all score patterns in the text
    matches = list(re.finditer(pattern, normalized))

    for match in matches:
        home = match.group(1)
        away = match.group(2)
        predictions.append(f"{home}-{away}")

    # Check count
    if len(predictions) != expected_count:
        errors.append(f"Expected {expected_count} scores, found {len(predictions)}")

    return predictions, errors


def parse_line_predictions(lines: list[str], games: list[str]) -> tuple[list[str], list[str]]:
    """Parse predictions line-by-line with game context.

    Each line should contain a score at the end in format like "2:0" or "2-1".

    Args:
        lines: List of text lines, one per game
        games: List of game names for context

    Returns: (valid_predictions, errors)
    """
    predictions = []
    errors = []

    if len(lines) != len(games):
        errors.append(f"Expected {len(games)} lines, got {len(lines)}")
        return predictions, errors

    for i, line in enumerate(lines):
        match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", line.strip())
        if match:
            home_score = match.group(1)
            away_score = match.group(2)
            predictions.append(f"{home_score}-{away_score}")
        else:
            errors.append(f"Line {i + 1}: Could not find score (expected format: '2:0' or '2-1')")

    return predictions, errors


def format_standings(standings: list[dict], last_fixture: dict | None) -> str:
    """Format standings for display in Discord.

    Args:
        standings: List of user standings with total_points, etc.
        last_fixture: Optional dict with last week's scores
    """
    lines = []

    # Overall standings
    lines.append("## Overall Standings")
    lines.append("")

    if not standings:
        lines.append("No standings yet!")
    else:
        lines.append("| Rank | User | Points | Exact | Correct | Weeks |")
        lines.append("|------|------|--------|-------|---------|-------|")

        for i, user in enumerate(standings, 1):
            lines.append(
                f"| {i} | {user['user_name']} | {user['total_points']} | "
                f"{user['total_exact']} | {user['total_correct']} | {user['weeks_played']} |"
            )

    # Last week's results
    if last_fixture:
        lines.append("")
        lines.append(f"## Last Week (Week {last_fixture['week_number']})")
        lines.append("")
        lines.append("| Rank | User | Points | Exact | Correct |")
        lines.append("|------|------|--------|-------|---------|")

        for i, score in enumerate(last_fixture["scores"], 1):
            lines.append(
                f"| {i} | {score['user_name']} | {score['points']} | "
                f"{score['exact_scores']} | {score['correct_results']} |"
            )

    return "\n".join(lines)


def format_predictions_preview(games: list[str], predictions: list[str]) -> str:
    """Format predictions for confirmation display."""
    lines = ["### Your Predictions:", ""]

    for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
        lines.append(f"{i}. {game}: **{pred}**")

    return "\n".join(lines)
