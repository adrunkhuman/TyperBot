"""Tests for scoring calculation utilities."""

from typer_bot.utils.scoring import build_fixture_scores, calculate_points


class TestCalculatePoints:
    """Test suite for calculate_points function."""

    def test_exact_score_match(self):
        """Exact prediction should award 3 points."""
        result = calculate_points(["2-1"], ["2-1"])
        assert result["points"] == 3
        assert result["exact_scores"] == 1
        assert result["correct_results"] == 0
        assert result["penalty"] is None

    def test_correct_outcome_home_win(self):
        """Correct outcome (home win) should award 1 point."""
        result = calculate_points(["3-1"], ["2-0"])
        assert result["points"] == 1
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 1

    def test_correct_outcome_away_win(self):
        """Correct outcome (away win) should award 1 point."""
        result = calculate_points(["1-3"], ["0-2"])
        assert result["points"] == 1
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 1

    def test_correct_outcome_draw(self):
        """Correct outcome (draw) should award 1 point."""
        result = calculate_points(["1-1"], ["2-2"])
        assert result["points"] == 1
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 1

    def test_wrong_prediction(self):
        """Wrong prediction should award 0 points."""
        result = calculate_points(["2-1"], ["1-2"])
        assert result["points"] == 0
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 0

    def test_custom_scoring_rules(self):
        result = calculate_points(
            ["2-1", "1-1", "0-2"],
            ["2-1", "2-2", "2-0"],
            scoring_rules={
                "exact_score_points": 5,
                "correct_outcome_points": 2,
                "wrong_outcome_points": 1,
                "late_prediction_points": 0,
            },
        )

        assert result["points"] == 8
        assert result["exact_scores"] == 1
        assert result["correct_results"] == 1

    def test_mixed_results_multiple_games(self):
        """Multiple games with exact, correct, and wrong outcomes."""
        predictions = ["2-1", "3-0", "1-1", "0-2"]
        actual = ["2-1", "2-1", "1-1", "2-0"]
        result = calculate_points(predictions, actual)

        assert result["points"] == 7  # 3 (exact) + 1 (correct outcome) + 3 (exact) + 0 (wrong)
        assert result["exact_scores"] == 2
        assert result["correct_results"] == 1

    def test_late_prediction_penalty(self):
        """Late prediction should receive 0 points with penalty flag."""
        result = calculate_points(["2-1"], ["2-1"], is_late=True)
        assert result["points"] == 0
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 0
        assert result["penalty"] == "Late prediction penalty applied"

    def test_custom_late_prediction_points(self):
        result = calculate_points(
            ["2-1"],
            ["2-1"],
            is_late=True,
            scoring_rules={"late_prediction_points": 1},
        )

        assert result["points"] == 1
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 0

    def test_empty_predictions(self):
        """Empty prediction lists should return 0 points."""
        result = calculate_points([], [])
        assert result["points"] == 0
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 0

    def test_double_digit_scores(self):
        """Scores like 10-0 should be handled correctly."""
        result = calculate_points(["10-0"], ["10-0"])
        assert result["points"] == 3
        assert result["exact_scores"] == 1

    def test_mismatched_list_lengths(self):
        """Different length lists should only compare available pairs."""
        # strict=False in zip means extra elements are ignored
        result = calculate_points(["2-1", "3-0", "1-1"], ["2-1", "3-0"])
        assert result["points"] == 6  # 3 + 3
        assert result["exact_scores"] == 2

    def test_colon_format_gracefully_handled(self):
        """calculate_points gracefully handles invalid formats by skipping them.

        Malformed data in DB is skipped rather than crashing the scoring.
        """
        # Normalized format from parser works
        result = calculate_points(["2-1"], ["2-1"])
        assert result["points"] == 3

        # Invalid formats are gracefully skipped (no crash)
        result = calculate_points(["2:1"], ["2:1"])
        assert result["points"] == 0  # Invalid format skipped

    def test_nullified_game_excluded_from_scoring(self):
        """Games marked with 'x' should be excluded from scoring calculations."""
        predictions = ["2-1", "3-0", "1-1"]
        actual = ["2-1", "x", "1-1"]
        result = calculate_points(predictions, actual)

        # Should only score 2 games (excluding the nullified middle one)
        assert result["points"] == 6  # 3 (exact) + 3 (exact)
        assert result["exact_scores"] == 2
        assert result["correct_results"] == 0

    def test_multiple_nullified_games(self):
        """Multiple nullified games should all be excluded."""
        predictions = ["2-1", "3-0", "1-1", "0-0", "2-2"]
        actual = ["2-1", "x", "1-1", "x", "2-2"]
        result = calculate_points(predictions, actual)

        # Should only score 3 games (positions 0, 2, 4)
        assert result["points"] == 9  # 3 + 3 + 3 (all exact)
        assert result["exact_scores"] == 3
        assert result["correct_results"] == 0

    def test_all_games_nullified(self):
        """When all games are nullified, everyone gets 0 points."""
        predictions = ["2-1", "3-0", "1-1"]
        actual = ["x", "x", "x"]
        result = calculate_points(predictions, actual)

        assert result["points"] == 0
        assert result["exact_scores"] == 0
        assert result["correct_results"] == 0

    def test_nullified_with_mixed_outcomes(self):
        """Nullified games mixed with exact, correct outcome, and wrong predictions."""
        predictions = ["2-1", "3-0", "1-1", "0-2", "2-0"]
        # Results: exact, nullified, exact, wrong (predicted draw, actual home win), correct outcome
        actual = ["2-1", "x", "1-1", "1-0", "3-1"]
        result = calculate_points(predictions, actual)

        # Points: 3 (exact) + 0 (nullified) + 3 (exact) + 0 (wrong) + 1 (correct outcome)
        assert result["points"] == 7
        assert result["exact_scores"] == 2
        assert result["correct_results"] == 1


class TestBuildFixtureScores:
    def test_scores_sparse_predictions_and_applies_fixture_ordering(self):
        scores = build_fixture_scores(
            [
                {
                    "user_id": "partial",
                    "user_name": "Partial User",
                    "predictions": ["1-1", "0-2"],
                    "predicted_game_indexes": [1, 2],
                    "is_late": False,
                    "late_penalty_waived": False,
                },
                {
                    "user_id": "full",
                    "user_name": "Full User",
                    "predictions": ["2-1", "1-1", "0-2"],
                    "predicted_game_indexes": [0, 1, 2],
                    "is_late": False,
                    "late_penalty_waived": False,
                },
            ],
            ["2-1", "1-1", "0-2"],
        )

        assert [score["user_id"] for score in scores] == ["full", "partial"]
        assert scores[0]["points"] == 9
        assert scores[1]["points"] == 6

    def test_late_penalty_is_applied_once_for_all_score_recalculation_paths(self):
        scores = build_fixture_scores(
            [
                {
                    "user_id": "late",
                    "user_name": "Late User",
                    "predictions": ["2-1"],
                    "predicted_game_indexes": [0],
                    "is_late": True,
                    "late_penalty_waived": False,
                },
                {
                    "user_id": "waived",
                    "user_name": "Waived User",
                    "predictions": ["2-1"],
                    "predicted_game_indexes": [0],
                    "is_late": True,
                    "late_penalty_waived": True,
                },
            ],
            ["2-1"],
        )

        assert [(score["user_id"], score["points"]) for score in scores] == [
            ("waived", 3),
            ("late", 0),
        ]

    def test_scores_with_custom_rules(self):
        scores = build_fixture_scores(
            [
                {
                    "user_id": "exact",
                    "user_name": "Exact User",
                    "predictions": ["2-1"],
                    "predicted_game_indexes": [0],
                    "is_late": False,
                    "late_penalty_waived": False,
                },
                {
                    "user_id": "late",
                    "user_name": "Late User",
                    "predictions": ["2-1"],
                    "predicted_game_indexes": [0],
                    "is_late": True,
                    "late_penalty_waived": False,
                },
            ],
            ["2-1"],
            {"exact_score_points": 5, "late_prediction_points": 1},
        )

        assert [(score["user_id"], score["points"]) for score in scores] == [
            ("exact", 5),
            ("late", 1),
        ]
