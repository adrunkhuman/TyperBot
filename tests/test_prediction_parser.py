"""Tests for prediction parsing utilities."""

from typer_bot.utils.prediction_parser import (
    format_predictions_preview,
    format_standings,
    parse_line_predictions,
    parse_predictions,
)


class TestParsePredictions:
    """Test suite for parse_predictions function."""

    def test_basic_scores(self):
        """Basic hyphen-separated scores."""
        predictions, errors = parse_predictions("2-1 1-0 2-2", expected_count=3)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_colon_separators(self):
        """Scores with colon separators."""
        predictions, errors = parse_predictions("2:1 1:0 2:2", expected_count=3)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_mixed_separators(self):
        """Mixed hyphen and colon separators."""
        predictions, errors = parse_predictions("2-1 1:0 2-2", expected_count=3)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_extra_spaces(self):
        """Scores with extra spaces around separators."""
        predictions, errors = parse_predictions("2  -  1    1  :  0", expected_count=2)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_comma_separation(self):
        """Comma-separated scores."""
        predictions, errors = parse_predictions("2-1, 1-0, 2-2", expected_count=3)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_random_newlines(self):
        """Scores with random newlines mixed in."""
        predictions, errors = parse_predictions("2-1\n\n1-0\n2-2\n", expected_count=3)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_leading_trailing_whitespace(self):
        """Input with leading/trailing whitespace and indentation."""
        predictions, errors = parse_predictions("   2-1   1-0   ", expected_count=2)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_double_digit_scores(self):
        """Double-digit scores like 10-0."""
        predictions, errors = parse_predictions("10-0 0-10 12-12", expected_count=3)
        assert predictions == ["10-0", "0-10", "12-12"]
        assert not errors

    def test_wrong_count_error(self):
        """Error when count doesn't match expected."""
        predictions, errors = parse_predictions("2-1 1-0", expected_count=3)
        assert predictions == ["2-1", "1-0"]
        assert len(errors) == 1
        assert "Expected 3 scores, found 2" in errors[0]

    def test_no_valid_scores(self):
        """Input with no valid score patterns."""
        predictions, errors = parse_predictions("hello world test", expected_count=3)
        assert predictions == []
        assert len(errors) == 1


class TestParseLinePredictions:
    """Test suite for parse_line_predictions function."""

    def test_basic_line_parsing(self):
        """Basic line-by-line parsing with game context."""
        lines = ["Team A vs Team B 2-1", "Team C vs Team D 1-0"]
        games = ["Team A vs Team B", "Team C vs Team D"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_score_at_end_of_line(self):
        """Score must be at end of line - trailing text causes parse failure."""
        # Score at end works
        lines = ["Team A vs Team B 2-1", "Team B 1-0"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_trailing_text_fails(self):
        """Text after score fails - parser expects score at line end."""
        lines = ["Team A 2-1 some comment", "Team B 1-0"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["1-0"]  # Only second line parses
        assert len(errors) == 1
        assert "Could not find score" in errors[0]

    def test_leading_indentation(self):
        """Lines with leading whitespace/indentation."""
        lines = ["   Team A 2-1", "    Team B 1-0"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_colon_separators_in_lines(self):
        """Lines with colon separators."""
        lines = ["Team A 2:1", "Team B 1:0"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_mixed_separators_in_lines(self):
        """Lines with mixed separators."""
        lines = ["Team A 2-1", "Team B 1:0", "Team C 2-2"]
        games = ["Team A", "Team B", "Team C"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert not errors

    def test_missing_score_in_line(self):
        """Line without a valid score at the end."""
        lines = ["Team A 2-1", "Team B no score here"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1"]
        assert len(errors) == 1
        assert "Could not find score" in errors[0]

    def test_wrong_line_count_error(self):
        """Error when line count doesn't match game count."""
        lines = ["Team A 2-1"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == []
        assert len(errors) == 1
        assert "Expected 2 lines, got 1" in errors[0]

    def test_extra_whitespace_in_lines(self):
        """Lines with extra internal whitespace."""
        lines = ["Team A    2  -  1", "Team B  1  :  0  "]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "1-0"]
        assert not errors

    def test_nullified_game_lowercase_x(self):
        """Parse 'x' as nullified game marker."""
        lines = ["Team A 2-1", "Team B x"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "x"]
        assert not errors

    def test_nullified_game_uppercase_x(self):
        """Parse 'X' as nullified game marker (case insensitive)."""
        lines = ["Team A 2-1", "Team B X"]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "x"]
        assert not errors

    def test_mixed_scores_and_nullified(self):
        """Mix of regular scores and nullified games."""
        lines = ["Team A 2-1", "Team B x", "Team C 0-0", "Team D X"]
        games = ["Team A", "Team B", "Team C", "Team D"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["2-1", "x", "0-0", "x"]
        assert not errors

    def test_nullified_with_whitespace(self):
        """Nullified marker with trailing whitespace."""
        lines = ["Team A x   ", "Team B X   "]
        games = ["Team A", "Team B"]
        predictions, errors = parse_line_predictions(lines, games)
        assert predictions == ["x", "x"]
        assert not errors


class TestFormatPredictionsPreview:
    """Test suite for format_predictions_preview function."""

    def test_basic_preview(self):
        """Basic preview formatting."""
        games = ["Team A vs Team B", "Team C vs Team D"]
        predictions = ["2-1", "1-0"]
        result = format_predictions_preview(games, predictions)
        assert "Team A vs Team B: **2-1**" in result
        assert "Team C vs Team D: **1-0**" in result


class TestFormatStandings:
    """Test suite for format_standings function."""

    def test_empty_standings(self):
        """Empty standings should show appropriate message."""
        result = format_standings([], None)
        assert "No standings yet!" in result

    def test_standings_with_data(self):
        """Standings with data formatted as markdown table."""
        standings = [
            {
                "user_name": "User1",
                "total_points": 10,
                "total_exact": 2,
                "total_correct": 4,
                "weeks_played": 3,
            },
            {
                "user_name": "User2",
                "total_points": 8,
                "total_exact": 1,
                "total_correct": 5,
                "weeks_played": 3,
            },
        ]
        result = format_standings(standings, None)
        assert "| Rank | User | Points |" in result
        assert "| 1 | User1 | 10 |" in result
        assert "| 2 | User2 | 8 |" in result

    def test_standings_with_last_fixture(self):
        """Standings including last fixture results."""
        standings = [
            {
                "user_name": "User1",
                "total_points": 10,
                "total_exact": 2,
                "total_correct": 4,
                "weeks_played": 3,
            }
        ]
        last_fixture = {
            "week_number": 5,
            "scores": [
                {"user_name": "User1", "points": 5, "exact_scores": 1, "correct_results": 2}
            ],
        }
        result = format_standings(standings, last_fixture)
        assert "Last Week (Week 5)" in result
        assert "| 1 | User1 | 5 |" in result
