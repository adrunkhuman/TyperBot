"""Tests for prediction parsing utilities."""

from typer_bot.utils.prediction_parser import (
    ascii_username,
    format_predictions_preview,
    format_standings,
    parse_prediction_lines,
)


class TestParsePredictionLines:
    def test_full_prediction_maps_by_game_name(self):
        games = ["Team A vs Team B", "Team C vs Team D"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "Team A vs Team B 2-1\nTeam C vs Team D 1-0",
            games,
        )

        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert errors == []

    def test_partial_mapping_by_game_name(self):
        games = ["Team A - Team B", "Team C - Team D", "Team E - Team F"]
        input_text = "Team C - Team D 1-1\nTeam E - Team F 0-2"

        predictions, game_indexes, errors = parse_prediction_lines(
            input_text, games, allow_partial=True
        )

        assert predictions == ["1-1", "0-2"]
        assert game_indexes == [1, 2]
        assert errors == []

    def test_full_prediction_falls_back_to_positional_matching(self):
        games = ["Team A - Team B", "Team C - Team D"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "2-1\n1-0", games, allow_partial=False
        )

        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert errors == []

    def test_score_at_end_of_line(self):
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines("Team A 2-1\nTeam B 1-0", games)

        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert errors == []

    def test_trailing_text_fails(self):
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "Team A 2-1 some comment\nTeam B 1-0", games
        )

        assert predictions == []
        assert game_indexes == []
        assert len(errors) == 1
        assert "Could not find score" in errors[0]

    def test_partial_requires_game_names(self):
        games = ["Team A - Team B", "Team C - Team D"]
        predictions, game_indexes, errors = parse_prediction_lines("2-1", games, allow_partial=True)

        assert predictions == []
        assert game_indexes == []
        assert "Could not match that line" in errors[0]

    def test_partial_cancelled_games_map_by_name(self):
        games = ["Team A - Team B", "Team C - Team D", "Team E - Team F"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "Team E - Team F x", games, allow_partial=True
        )

        assert predictions == ["x"]
        assert game_indexes == [2]
        assert errors == []

    def test_partial_mapping_preserves_team_names_starting_with_number(self):
        games = ["1. FC Koln - Bayern", "Team C - Team D"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "1. FC Koln - Bayern 2:1",
            games,
            allow_partial=True,
        )

        assert predictions == ["2-1"]
        assert game_indexes == [0]
        assert errors == []

    def test_mixed_separators_in_lines(self):
        input_text = "Team A 2-1\nTeam B 1:0\nTeam C 2-2"
        games = ["Team A", "Team B", "Team C"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert game_indexes == [0, 1, 2]
        assert not errors

    def test_double_digit_scores(self):
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(
            "Team A 10-0\nTeam B 0:12", games
        )

        assert predictions == ["10-0", "0-12"]
        assert game_indexes == [0, 1]
        assert errors == []

    def test_missing_score_in_line(self):
        input_text = "Team A 2-1\nTeam B no score here"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == []
        assert game_indexes == []
        assert len(errors) == 1
        assert "Could not find score" in errors[0]

    def test_wrong_line_count_error(self):
        input_text = "Team A 2-1"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == []
        assert game_indexes == []
        assert len(errors) == 1
        assert "Expected 2 predictions, found 1" in errors[0]

    def test_extra_whitespace_in_lines(self):
        input_text = "Team A    2  -  1\nTeam B  1  :  0  "
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_nullified_game_lowercase_x(self):
        input_text = "Team A 2-1\nTeam B x"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "x"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_nullified_game_uppercase_x(self):
        input_text = "Team A 2-1\nTeam B X"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "x"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_mixed_scores_and_nullified(self):
        input_text = "Team A 2-1\nTeam B x\nTeam C 0-0\nTeam D X"
        games = ["Team A", "Team B", "Team C", "Team D"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "x", "0-0", "x"]
        assert game_indexes == [0, 1, 2, 3]
        assert not errors

    def test_nullified_with_whitespace(self):
        input_text = "Team A x   \nTeam B X   "
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["x", "x"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_comma_separated_predictions(self):
        input_text = "Team A 2-1, Team B 1-0"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_mixed_comma_and_newline(self):
        input_text = "Team A 2-1, Team B 1-0\nTeam C 2-2"
        games = ["Team A", "Team B", "Team C"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert game_indexes == [0, 1, 2]
        assert not errors

    def test_comma_with_extra_whitespace(self):
        input_text = "Team A 2-1 , Team B 1-0 , Team C 2-2"
        games = ["Team A", "Team B", "Team C"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0", "2-2"]
        assert game_indexes == [0, 1, 2]
        assert not errors

    def test_trailing_comma(self):
        input_text = "Team A 2-1, Team B 1-0,"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_multiple_commas(self):
        input_text = "Team A 2-1,, Team B 1-0"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
        assert not errors

    def test_comma_with_nullified_games(self):
        input_text = "Team A 2-1, Team B x, Team C 1-0"
        games = ["Team A", "Team B", "Team C"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "x", "1-0"]
        assert game_indexes == [0, 1, 2]
        assert not errors

    def test_comma_with_colon_separator(self):
        input_text = "Team A 2:1, Team B 1:0"
        games = ["Team A", "Team B"]
        predictions, game_indexes, errors = parse_prediction_lines(input_text, games)
        assert predictions == ["2-1", "1-0"]
        assert game_indexes == [0, 1]
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
        """Standings with data formatted as code block table."""
        standings = [
            {
                "user_id": "1",
                "user_name": "User1",
                "total_points": 10,
                "total_exact": 2,
                "total_correct": 4,
                "weeks_played": 3,
            },
            {
                "user_id": "2",
                "user_name": "User2",
                "total_points": 8,
                "total_exact": 1,
                "total_correct": 5,
                "weeks_played": 3,
            },
        ]
        result = format_standings(standings, None)
        assert "🏆 **Overall Standings**" in result
        assert "Rank  User" in result
        assert "User1" in result
        assert "User2" in result
        assert "```" in result

    def test_standings_with_last_fixture(self):
        """Standings including last fixture results."""
        standings = [
            {
                "user_id": "1",
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
                {
                    "user_id": "1",
                    "user_name": "User1",
                    "points": 5,
                    "exact_scores": 1,
                    "correct_results": 2,
                }
            ],
        }
        result = format_standings(standings, last_fixture)
        assert "📊 **Week 5 Results**" in result
        assert "User1" in result
        assert "```" in result

    def test_standings_delta_calculation(self):
        """Overall standings should show delta from last week."""
        standings = [
            {
                "user_id": "1",
                "user_name": "User1",
                "total_points": 15,
                "total_exact": 3,
                "total_correct": 6,
                "weeks_played": 3,
            },
            {
                "user_id": "2",
                "user_name": "User2",
                "total_points": 12,
                "total_exact": 2,
                "total_correct": 6,
                "weeks_played": 3,
            },
        ]
        last_fixture = {
            "week_number": 3,
            "scores": [
                {
                    "user_id": "1",
                    "user_name": "User1",
                    "points": 5,
                    "exact_scores": 1,
                    "correct_results": 2,
                },
                {
                    "user_id": "2",
                    "user_name": "User2",
                    "points": 3,
                    "exact_scores": 0,
                    "correct_results": 3,
                },
            ],
        }
        result = format_standings(standings, last_fixture)
        assert "(+5)" in result  # User1 got 5 points last week
        assert "(+3)" in result  # User2 got 3 points last week

    def test_standings_column_order(self):
        """Standings should have correct column order: Rank, User, Exact, Correct, Points."""
        standings = [
            {
                "user_id": "1",
                "user_name": "TestUser",
                "total_points": 10,
                "total_exact": 2,
                "total_correct": 4,
                "weeks_played": 2,
            }
        ]
        result = format_standings(standings, None)
        lines = result.split("\n")
        # Find header line
        header_line = None
        for line in lines:
            if "Rank" in line and "User" in line:
                header_line = line
                break
        assert header_line is not None
        # Check column order
        rank_pos = header_line.find("Rank")
        user_pos = header_line.find("User")
        exact_pos = header_line.find("Exact")
        correct_pos = header_line.find("Correct")
        points_pos = header_line.find("Points")
        assert rank_pos < user_pos < exact_pos < correct_pos < points_pos

    def test_standings_code_block_formatting(self):
        """Standings should be wrapped in code blocks for Discord."""
        standings = [
            {
                "user_id": "1",
                "user_name": "User1",
                "total_points": 10,
                "total_exact": 2,
                "total_correct": 4,
                "weeks_played": 2,
            }
        ]
        result = format_standings(standings, None)
        assert result.startswith("🏆 **Overall Standings**")
        assert "```" in result
        # Should have opening and closing code blocks
        assert result.count("```") >= 2

    def test_last_week_standings_column_order(self):
        """Last week standings should have correct column order."""
        standings = []
        last_fixture = {
            "week_number": 1,
            "scores": [
                {
                    "user_id": "1",
                    "user_name": "User1",
                    "points": 7,
                    "exact_scores": 2,
                    "correct_results": 1,
                }
            ],
        }
        result = format_standings(standings, last_fixture)
        lines = result.split("\n")
        # Find header line in last week section
        in_last_week = False
        header_line = None
        for line in lines:
            if "📊 **Week 1 Results**" in line:
                in_last_week = True
            if in_last_week and "Rank" in line and "User" in line:
                header_line = line
                break
        assert header_line is not None
        # Check column order: Rank, User, Exact, Correct, Points
        rank_pos = header_line.find("Rank")
        user_pos = header_line.find("User")
        exact_pos = header_line.find("Exact")
        correct_pos = header_line.find("Correct")
        points_pos = header_line.find("Points")
        assert rank_pos < user_pos < exact_pos < correct_pos < points_pos


class TestAsciiUsername:
    """Test suite for ascii_username function."""

    def test_basic_ascii_username(self):
        """Basic ASCII username should be unchanged."""
        result = ascii_username("User123")
        assert result.strip() == "User123"
        assert len(result) == 20

    def test_username_with_emojis(self):
        """Username with emojis should strip non-ASCII characters."""
        result = ascii_username("Piekny_Maryjan ✌🏐 🥈")
        assert "✌" not in result
        assert "🏐" not in result
        assert "🥈" not in result
        assert result.strip() == "Piekny_Maryjan"

    def test_username_with_unicode_bold(self):
        """Username with Unicode bold letters should strip them."""
        result = ascii_username("𝗛𝗼𝗿𝘂𝘀 ☀")
        assert "𝗛" not in result
        assert "☀" not in result
        assert result.strip() == ""

    def test_long_username_truncation(self):
        """Long usernames should be truncated to max_len."""
        long_name = "VeryLongUsernameThatExceedsTwentyChars"
        result = ascii_username(long_name, max_len=20)
        assert len(result) == 20
        assert result.strip() == long_name[:20]

    def test_username_padding(self):
        """Short usernames should be padded to max_len."""
        result = ascii_username("Bob", max_len=20)
        assert len(result) == 20
        assert result.strip() == "Bob"

    def test_empty_username(self):
        """Empty username should return padded string."""
        result = ascii_username("")
        assert len(result) == 20
        assert result.strip() == ""
