"""Prediction parsing utilities."""

import logging
import re

logger = logging.getLogger(__name__)


def parse_predictions(input_text: str, expected_count: int = 9) -> tuple[list[str], list[str]]:
    """Parse predictions from user input.

    Format agnostic: "2-1 1-0", "2:1, 1:0", "2 - 1".
    Returns: (valid_predictions, errors)
    """
    logger.debug(f"Parsing predictions: expected={expected_count}, input_length={len(input_text)}")

    normalized = input_text.replace(",", " ")
    pattern = r"\s*(\d+)\s*[-:]\s*(\d+)\s*"

    predictions = []
    errors = []

    matches = list(re.finditer(pattern, normalized))
    logger.debug(f"Found {len(matches)} score matches in input")

    for match in matches:
        home = match.group(1)
        away = match.group(2)
        predictions.append(f"{home}-{away}")

    if len(predictions) != expected_count:
        error_msg = f"Expected {expected_count} scores, found {len(predictions)}"
        logger.warning(f"Prediction count mismatch: {error_msg}")
        errors.append(error_msg)
    else:
        logger.debug(f"Successfully parsed {len(predictions)} predictions")

    return predictions, errors


def parse_line_predictions(input_text: str, games: list[str]) -> tuple[list[str], list[str]]:
    """Parse predictions from user input with game context.

    Accepts newline-separated or comma-separated predictions.
    Each segment should contain a score at the end in format like "2:0" or "2-1".

    Args:
        input_text: Raw input text (supports commas or newlines as delimiters)
        games: List of game names for context

    Returns: (valid_predictions, errors)
    """
    # Normalize: commas become newlines, then split and filter empty segments
    normalized = input_text.replace(",", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]

    logger.debug(f"Parsing line predictions: {len(lines)} lines, {len(games)} games")

    predictions = []
    errors = []

    if len(lines) != len(games):
        error_msg = f"Expected {len(games)} predictions, found {len(lines)}"
        logger.warning(f"Line count mismatch: {error_msg}")
        errors.append(error_msg)
        return predictions, errors

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Check for nullified game marker (x or X)
        if re.search(r"[xX]\s*$", stripped):
            predictions.append("x")
            logger.debug(f"Line {i + 1}: Parsed nullified game (x)")
            continue

        # Check for score pattern
        match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", stripped)
        if match:
            home_score = match.group(1)
            away_score = match.group(2)
            predictions.append(f"{home_score}-{away_score}")
            logger.debug(f"Line {i + 1}: Parsed {home_score}-{away_score}")
        else:
            error_msg = f"Line {i + 1}: Could not find score (expected format: '2:0' or '2-1', or 'x' for cancelled games)"
            logger.warning(f"Parse error on line {i + 1}: '{line[:50]}...'")
            errors.append(error_msg)

    if not errors:
        logger.debug(f"Successfully parsed all {len(predictions)} line predictions")

    return predictions, errors


def ascii_username(username: str, max_len: int = 20) -> str:
    """Filter username to ASCII-only for reliable alignment in Discord code blocks."""
    ascii_only = "".join(c for c in username if ord(c) < 128)
    return ascii_only[:max_len].ljust(max_len)


def format_fixture_results(games: list[str], results: list[str], week_number: int) -> str:
    """Format entered match results for the calculation announcement."""
    lines = [f"⚽ **Week {week_number} Results**", "```"]
    for game, result in zip(games, results, strict=True):
        lines.append(f"{game}  {result}")
    lines.append("```")
    return "\n".join(lines)


def format_standings(standings: list[dict], last_fixture: dict | None) -> str:
    """Format standings table for Discord using code blocks for proper alignment."""
    lines = []

    # Overall Standings
    lines.append("🏆 **Overall Standings**")
    lines.append("```")
    lines.append("Rank  User                    Exact  Correct  Points")
    lines.append("----  --------------------    -----  -------  ------")

    if not standings:
        lines.append("No standings yet!")
    else:
        # Create lookup for last week's points to calculate delta
        last_week_points = {}
        if last_fixture:
            for score in last_fixture["scores"]:
                last_week_points[score["user_id"]] = score["points"]

        for i, user in enumerate(standings, 1):
            user_name = ascii_username(user["user_name"])
            total_points = user["total_points"]

            # Calculate delta from last week
            delta = ""
            if user["user_id"] in last_week_points:
                delta = f" (+{last_week_points[user['user_id']]})"

            lines.append(
                f"{i:>4}  {user_name}  {user['total_exact']:>5}  {user['total_correct']:>7}  {total_points:>4}{delta}"
            )

    lines.append("```")

    # Last Week Results
    if last_fixture:
        lines.append("")
        lines.append(f"📊 **Week {last_fixture['week_number']} Results**")
        lines.append("```")
        lines.append("Rank  User                    Exact  Correct  Points")
        lines.append("----  --------------------    -----  -------  ------")

        for i, score in enumerate(last_fixture["scores"], 1):
            user_name = ascii_username(score["user_name"])
            lines.append(
                f"{i:>4}  {user_name}  {score['exact_scores']:>5}  {score['correct_results']:>7}  {score['points']:>4}"
            )

        lines.append("```")

    return "\n".join(lines)


def format_predictions_preview(games: list[str], predictions: list[str]) -> str:
    """Format predictions for confirmation display."""
    lines = ["### Your Predictions:", ""]

    for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
        lines.append(f"{i}. {game}: **{pred}**")

    return "\n".join(lines)
