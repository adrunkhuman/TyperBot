"""Scoring calculation utilities."""


def calculate_points(
    predictions: list[str], 
    actual_results: list[str],
    is_late: bool = False
) -> dict:
    """Calculate points for a set of predictions.
    
    Scoring:
    - Exact score: 3 points
    - Correct result (win/loss/draw): 1 point
    - Wrong: 0 points
    
    Late predictions get -100% penalty (0 points regardless).
    
    Returns dict with total points and breakdown.
    """
    if is_late:
        return {
            "points": 0,
            "exact_scores": 0,
            "correct_results": 0,
            "penalty": "Late prediction - 100% penalty applied"
        }
    
    total_points = 0
    exact_count = 0
    correct_count = 0
    
    for pred, actual in zip(predictions, actual_results, strict=False):
        pred_home, pred_away = map(int, pred.split("-"))
        actual_home, actual_away = map(int, actual.split("-"))
        
        # Check exact score
        if pred_home == actual_home and pred_away == actual_away:
            total_points += 3
            exact_count += 1
        # Check correct result (home win, away win, or draw)
        elif (pred_home > pred_away and actual_home > actual_away) or \
             (pred_home < pred_away and actual_home < actual_away) or \
             (pred_home == pred_away and actual_home == actual_away):
            total_points += 1
            correct_count += 1
        # Wrong result: 0 points
    
    return {
        "points": total_points,
        "exact_scores": exact_count,
        "correct_results": correct_count,
        "penalty": None
    }


def parse_result(result_str: str) -> tuple[int, int] | None:
    """Parse a result string into home and away scores."""
    try:
        home, away = result_str.split("-")
        return int(home), int(away)
    except (ValueError, AttributeError):
        return None