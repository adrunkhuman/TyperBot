"""Scoring calculation utilities."""


def calculate_points(
    predictions: list[str], actual_results: list[str], is_late: bool = False
) -> dict:
    """Calculate points.

    Exact: 3pts
    Outcome: 1pt
    Late: -100% penalty (0pts)

    Returns: dict with points, exact_scores, correct_results, penalty
    """
    if is_late:
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

        pred_home, pred_away = map(int, pred.split("-"))
        actual_home, actual_away = map(int, actual.split("-"))

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
