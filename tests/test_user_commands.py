"""Tests for user command wiring."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from typer_bot.commands.user_commands import UserCommands
from typer_bot.utils import format_standings


@pytest.fixture
async def user_commands(mock_bot, database):
    mock_bot.db = database
    return UserCommands(mock_bot)


class TestPredictCommand:
    @pytest.mark.asyncio
    async def test_no_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.response_sent) == 1
        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_starts_dm_prediction_flow(self, user_commands, mock_interaction):
        starter = AsyncMock()
        user_commands.prediction_handler.start_flow = starter

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.response_sent) == 1
        assert "Check your DMs" in mock_interaction.response_sent[0]["content"]
        starter.assert_awaited_once()
        called_user, called_fixtures = starter.await_args.args
        assert called_user is mock_interaction.user
        assert len(called_fixtures) == 1

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_dm_permission_error(self, user_commands, mock_interaction):
        user_commands.prediction_handler.start_flow = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "Cannot send DMs")
        )

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.followup_sent) == 1
        assert "can't send you DMs" in mock_interaction.followup_sent[0]["content"]


class TestFixturesCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.fixtures.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == "❌ No active fixture found!"

    @pytest.mark.asyncio
    async def test_single_open_fixture_lists_games_and_deadline(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(1, sample_games, datetime.now(UTC) + timedelta(days=1))

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Week 1 Fixtures" in content
        assert sample_games[0] in content
        assert "Deadline:" in content
        assert mock_interaction.response_sent[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_list_each_week(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Open Fixtures" in content
        assert "Week 1" in content
        assert "Week 2" in content


class TestStandingsCommand:
    @pytest.mark.asyncio
    async def test_standings_sends_empty_state(self, user_commands, mock_interaction):
        await user_commands.standings.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == format_standings([], None)
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
        user_commands.db.get_standings = AsyncMock(return_value=standings)
        user_commands.db.get_last_fixture_scores = AsyncMock(return_value=last_fixture)

        await user_commands.standings.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == format_standings(
            standings, last_fixture
        )


class TestMyPredictionsCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == "❌ No active fixture found!"

    @pytest.mark.asyncio
    async def test_single_fixture_without_prediction_shows_prompt(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(1, sample_games, datetime.now(UTC) + timedelta(days=1))

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "haven't submitted predictions" in content
        assert "Use `/predict`" in content

    @pytest.mark.asyncio
    async def test_single_fixture_prediction_shows_saved_scores(
        self, user_commands, mock_interaction, database, sample_games
    ):
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_prediction(
            fixture_id,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions:" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "Status:" in content
        assert "Submitted:" in content

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_show_mixed_prediction_state(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_week_1 = await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)
        await database.save_prediction(
            fixture_week_1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions (Open Fixtures):" in content
        assert "Week 1" in content
        assert "Week 2" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "No prediction submitted yet." in content
