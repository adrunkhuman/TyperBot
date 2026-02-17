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

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_confirmation_includes_edit_hint(self, user_commands, mock_message):
        mock_message.guild = None  # DM message
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await user_commands.on_message(mock_message)

        confirm_msg = mock_message.author.dm_sent[-1]
        assert "edit" in confirm_msg.lower()


class TestOnMessageEdit:
    @pytest.mark.asyncio
    async def test_edit_updates_prediction(self, user_commands, fixture_with_dm, mock_message):
        """Test that editing a DM message updates the prediction."""
        from tests.conftest import MockMessage

        mock_message.guild = None  # DM message

        # First prediction
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await user_commands.on_message(mock_message)

        # Verify first prediction saved
        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]

        # Create "after" message with same author but different content
        after_msg = MockMessage(
            content="Team A - Team B 3-0\nTeam C - Team D 2-2\nTeam E - Team F 1-1",
            author=mock_message.author,
            guild=None,
        )

        await user_commands.on_message_edit(mock_message, after_msg)

        # Verify prediction was updated
        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["3-0", "2-2", "1-1"]

    @pytest.mark.asyncio
    async def test_edit_with_invalid_format_keeps_old_prediction(
        self, user_commands, fixture_with_dm, mock_message
    ):
        """Test that invalid edit keeps old prediction and shows error."""
        from tests.conftest import MockMessage

        mock_message.guild = None  # DM message

        # First prediction
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await user_commands.on_message(mock_message)

        # Verify first prediction saved
        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]

        # Create "after" message with invalid format
        after_msg = MockMessage(
            content="Team A - Team B invalid\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
            author=mock_message.author,
            guild=None,
        )

        await user_commands.on_message_edit(mock_message, after_msg)

        # Verify old prediction is still in database
        predictions = await user_commands.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]

        # Verify error message mentions keeping old predictions
        error_msg = mock_message.author.dm_sent[-1]
        assert "previous predictions are kept" in error_msg
        assert "Invalid predictions" in error_msg

    @pytest.mark.asyncio
    async def test_edit_ignored_if_content_unchanged(
        self, user_commands, _fixture_with_dm, mock_message
    ):
        """Test that edit with same content is ignored."""
        mock_message.guild = None  # DM message

        # First prediction
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await user_commands.on_message(mock_message)

        # Count initial DMs
        initial_dm_count = len(mock_message.author.dm_sent)

        # Edit with same content - create new message with same content
        from tests.conftest import MockMessage

        after_msg = MockMessage(
            content="Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
            author=mock_message.author,
            guild=None,
        )

        await user_commands.on_message_edit(mock_message, after_msg)

        # Should not have sent any new DMs (early return)
        assert len(mock_message.author.dm_sent) == initial_dm_count
