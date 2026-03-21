"""Tests for user command wiring."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from typer_bot.commands.user_commands import UserCommands


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
