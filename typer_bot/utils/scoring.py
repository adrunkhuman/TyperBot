"""Scoring calculation utilities."""

from collections.abc import Sequence


def align_predictions_to_fixture(
    predictions: list[str],
    predicted_game_indexes: list[int],
    fixture_length: int,
) -> list[str | None]:
    """Expand sparse predictions to fixture length with missing rows as ``None``."""
    aligned: list[str | None] = [None] * fixture_length
    for game_index, prediction in zip(predicted_game_indexes, predictions, strict=False):
        if 0 <= game_index < fixture_length:
            aligned[game_index] = prediction
    return aligned


def calculate_points(
    predictions: Sequence[str | None],
    actual_results: Sequence[str],
    is_late: bool = False,
    late_penalty_waived: bool = False,
) -> dict:
    """Calculate points.

    Exact: 3pts
    Outcome: 1pt
    Late: -100% penalty (0pts)

    Returns: dict with points, exact_scores, correct_results, penalty
    """
    if is_late and not late_penalty_waived:
        return {
            "points": 0,
            "exact_scores": 0,
            "correct_results": 0,
            "penalty": "Late prediction - 100% penalty applied",
        }

    total_points = 0
    exact_count = 0
    correct_count = 0

    for pred, actual in zip(predictions, actual_results, strict=False):
        # Skip nullified games (marked with 'x')
        if actual == "x":
            continue
        if pred is None:
            continue

        parsed_pred = parse_result(pred)
        parsed_actual = parse_result(actual)

        if parsed_pred is None or parsed_actual is None:
            continue

        pred_home, pred_away = parsed_pred
        actual_home, actual_away = parsed_actual

        if pred_home == actual_home and pred_away == actual_away:
            total_points += 3
            exact_count += 1
        elif (
            (pred_home > pred_away and actual_home > actual_away)
            or (pred_home < pred_away and actual_home < actual_away)
            or (pred_home == pred_away and actual_home == actual_away)
        ):
            total_points += 1
            correct_count += 1

    return {
        "points": total_points,
        "exact_scores": exact_count,
        "correct_results": correct_count,
        "penalty": None,
    }


def parse_result(result_str: str) -> tuple[int, int] | None:
    """Parse a result string into home and away scores."""
    try:
        home, away = result_str.split("-")
        return int(home), int(away)
    except (ValueError, AttributeError):
        return None
