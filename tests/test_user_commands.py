"""Tests for user commands DM prediction flow."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from typer_bot.commands.user_commands import UserCommands


@pytest.fixture
async def user_commands(mock_bot, database):
    mock_bot.db = database
    return UserCommands(mock_bot)


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, user_commands, mock_message):
        mock_message.author.bot = True
        mock_message.guild = None  # DM message

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_ignores_guild_messages(self, user_commands, mock_message):
        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_ignores_dms_during_results_entry(self, user_commands, mock_message):
        """Prevent admin's existing predictions being marked late during results entry."""
        from typer_bot.handlers.results_handler import _pending_results

        mock_message.guild = None  # DM message
        user_id = str(mock_message.author.id)

        # Simulate active results entry session for this user
        _pending_results[user_id] = {
            "fixture_id": 1,
            "guild_id": 123456,
            "created_at": datetime.now(UTC),
        }

        await user_commands.on_message(mock_message)

        # Should not send any response - message is handled by results handler
        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_rejects_message_too_long(self, user_commands, mock_message):
        mock_message.guild = None  # DM message
        mock_message.content = "x" * 5001

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 1
        assert "too long" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_no_fixture_shows_info_message(self, user_commands, mock_message):
        mock_message.guild = None  # DM message

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 1
        assert "No active fixture" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_saves_valid_predictions(self, user_commands, fixture_with_dm, mock_message):
        mock_message.guild = None  # DM message
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 2  # Processing + confirmation

        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["user_id"] == "123456"
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]
        assert not predictions[0]["is_late"]

    @pytest.mark.asyncio
    async def test_updates_existing_prediction(self, user_commands, fixture_with_dm, mock_message):
        mock_message.guild = None  # DM message
        # First prediction
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await user_commands.on_message(mock_message)

        # Second prediction - ON CONFLICT REPLACE updates existing row
        mock_message.content = "Team A - Team B 3-0\nTeam C - Team D 2-2\nTeam E - Team F 1-1"
        await user_commands.on_message(mock_message)

        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["3-0", "2-2", "1-1"]

    @pytest.mark.asyncio
    async def test_marks_late_predictions(
        self, user_commands, database, mock_message, sample_games
    ):
        mock_message.guild = None  # DM message
        deadline = datetime.now(UTC) - timedelta(hours=1)  # Past deadline
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await user_commands.on_message(mock_message)

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["is_late"]
        confirm_msg = mock_message.author.dm_sent[-1]
        assert "Late prediction" in confirm_msg

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_invalid_prediction_format(self, user_commands, mock_message):
        mock_message.guild = None  # DM message
        mock_message.content = "Team A - Team B invalid\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 2  # Processing + error
        error_msg = mock_message.author.dm_sent[-1]
        assert "Invalid predictions" in error_msg


class TestPredictCommand:
    @pytest.mark.asyncio
    async def test_no_fixture_shows_error(self, user_commands, mock_interaction):
        # Call the callback directly (not the app_commands.command wrapper)
        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.response_sent) == 1
        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_sends_dm_with_fixture_list(self, user_commands, mock_interaction):
        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.response_sent) == 1
        assert "Check your DMs" in mock_interaction.response_sent[0]["content"]

        assert len(mock_interaction.user.dm_sent) == 1
        dm_content = mock_interaction.user.dm_sent[0]
        assert "Week 1" in dm_content
        assert "Team A - Team B" in dm_content

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_dm_permission_error(self, user_commands, mock_interaction, monkeypatch):
        import discord

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(), "Cannot send DMs")

        monkeypatch.setattr(mock_interaction.user, "send", raise_forbidden)

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.followup_sent) == 1
        assert "can't send you DMs" in mock_interaction.followup_sent[0]["content"]


class TestEdgeCases:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_database_error(self, user_commands, mock_message, monkeypatch):
        mock_message.guild = None  # DM message

        async def raise_error(*_args, **_kwargs):
            raise Exception("Database connection failed")

        monkeypatch.setattr(user_commands.db, "save_prediction", raise_error)

        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await user_commands.on_message(mock_message)

        assert len(mock_message.author.dm_sent) == 2  # Processing + error
        error_msg = mock_message.author.dm_sent[-1]
        assert "Error processing predictions" in error_msg
