"""Scoring calculation utilities."""

from collections.abc import Sequence

DEFAULT_SCORING_RULES = {
    "exact_score_points": 3,
    "correct_outcome_points": 1,
    "wrong_outcome_points": 0,
    "late_prediction_points": 0,
}


def normalize_scoring_rules(rules: dict | None = None) -> dict:
    """Return scoring rules with defaults for omitted fields."""
    normalized = DEFAULT_SCORING_RULES.copy()
    if rules:
        normalized.update({key: int(value) for key, value in rules.items() if key in normalized})
    return normalized


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
    scoring_rules: dict | None = None,
) -> dict:
    """Calculate points from prediction rows and season scoring rules."""
    rules = normalize_scoring_rules(scoring_rules)
    if is_late and not late_penalty_waived:
        return {
            "points": rules["late_prediction_points"],
            "exact_scores": 0,
            "correct_results": 0,
            "penalty": "Late prediction penalty applied",
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
            total_points += rules["exact_score_points"]
            exact_count += 1
        elif (
            (pred_home > pred_away and actual_home > actual_away)
            or (pred_home < pred_away and actual_home < actual_away)
            or (pred_home == pred_away and actual_home == actual_away)
        ):
            total_points += rules["correct_outcome_points"]
            correct_count += 1
        else:
            total_points += rules["wrong_outcome_points"]

    return {
        "points": total_points,
        "exact_scores": exact_count,
        "correct_results": correct_count,
        "penalty": None,
    }


def build_fixture_scores(
    predictions: Sequence[dict],
    results: Sequence[str],
    scoring_rules: dict | None = None,
) -> list[dict]:
    """Build sorted fixture score rows from stored prediction payloads."""
    rules = normalize_scoring_rules(scoring_rules)
    scores = []
    for prediction in predictions:
        aligned_predictions = align_predictions_to_fixture(
            prediction["predictions"],
            prediction["predicted_game_indexes"],
            len(results),
        )
        score_data = calculate_points(
            aligned_predictions,
            results,
            prediction["is_late"],
            prediction["late_penalty_waived"],
            rules,
        )
        scores.append(
            {
                "user_id": prediction["user_id"],
                "user_name": prediction["user_name"],
                "points": score_data["points"],
                "exact_scores": score_data["exact_scores"],
                "correct_results": score_data["correct_results"],
            }
        )

    scores.sort(
        key=lambda score: (
            -score["points"],
            -score["exact_scores"],
            -score["correct_results"],
            score["user_name"].lower(),
        )
    )
    return scores


def parse_result(result_str: str) -> tuple[int, int] | None:
    """Parse a result string into home and away scores."""
    try:
        home, away = result_str.split("-")
        return int(home), int(away)
    except (ValueError, AttributeError):
        return None
