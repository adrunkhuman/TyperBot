from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest


class TestStandingsCommand:
    @pytest.mark.asyncio
    async def test_standings_sends_empty_state(self, user_commands, mock_interaction):
        await user_commands.standings.callback(user_commands, mock_interaction)

        assert "No standings yet" in mock_interaction.response_sent[0]["content"]
        assert mock_interaction.response_sent[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_standings_sends_formatted_leaderboard(self, user_commands, mock_interaction):
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 9,
                "total_exact": 3,
                "total_correct": 3,
            }
        ]
        last_fixture = {
            "week_number": 4,
            "games": ["A - B"],
            "results": ["2-1"],
            "scores": [
                {
                    "user_id": "123",
                    "user_name": "User1",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 1,
                }
            ],
        }
        user_commands.db.scores.get_standings = AsyncMock(return_value=standings)
        user_commands.db.scores.get_last_fixture_scores = AsyncMock(return_value=last_fixture)

        await user_commands.standings.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "User1" in content
        assert "9" in content

    @pytest.mark.asyncio
    async def test_standings_only_shows_current_guild_scores(
        self, user_commands, mock_interaction, database
    ):
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) - timedelta(days=1)
        current_fixture_id = await database.fixtures.create_fixture("111111", 1, games, deadline)
        other_fixture_id = await database.fixtures.create_fixture("guild-2", 2, games, deadline)
        await database.scores.save_scores(
            current_fixture_id,
            [
                {
                    "user_id": "current-user",
                    "user_name": "Current Guild",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )
        await database.scores.save_scores(
            other_fixture_id,
            [
                {
                    "user_id": "other-user",
                    "user_name": "Other Guild",
                    "points": 9,
                    "exact_scores": 3,
                    "correct_results": 3,
                }
            ],
        )

        await user_commands.standings.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Current Guild" in content
        assert "Other Guild" not in content
