"""Tests for scoring calculation utilities."""

import pytest

from typer_bot.utils.scoring import calculate_points


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
        assert result["penalty"] == "Late prediction - 100% penalty applied"

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

    def test_colon_format_not_supported_directly(self):
        """calculate_points expects normalized X-Y format from parser.

        Raw X:Y format would fail - normalization happens in prediction_parser.
        This test documents the expected input contract.
        """
        # Normalized format from parser works
        result = calculate_points(["2-1"], ["2-1"])
        assert result["points"] == 3

        # Raw colon format would raise ValueError (int("2:1") fails)
        with pytest.raises(ValueError):
            calculate_points(["2:1"], ["2:1"])

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
