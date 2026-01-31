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


def parse_line_predictions(lines: list[str], games: list[str]) -> tuple[list[str], list[str]]:
    """Parse predictions line-by-line with game context.

    Each line should contain a score at the end in format like "2:0" or "2-1".

    Args:
        lines: List of text lines, one per game
        games: List of game names for context

    Returns: (valid_predictions, errors)
    """
    logger.debug(f"Parsing line predictions: {len(lines)} lines, {len(games)} games")

    predictions = []
    errors = []

    if len(lines) != len(games):
        error_msg = f"Expected {len(games)} lines, got {len(lines)}"
        logger.warning(f"Line count mismatch: {error_msg}")
        errors.append(error_msg)
        return predictions, errors

    for i, line in enumerate(lines):
        match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", line.strip())
        if match:
            home_score = match.group(1)
            away_score = match.group(2)
            predictions.append(f"{home_score}-{away_score}")
            logger.debug(f"Line {i + 1}: Parsed {home_score}-{away_score}")
        else:
            error_msg = f"Line {i + 1}: Could not find score (expected format: '2:0' or '2-1')"
            logger.warning(f"Parse error on line {i + 1}: '{line[:50]}...'")
            errors.append(error_msg)

    if not errors:
        logger.debug(f"Successfully parsed all {len(predictions)} line predictions")

    return predictions, errors


def format_standings(standings: list[dict], last_fixture: dict | None) -> str:
    """Format standings table for Discord."""
    lines = []

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
