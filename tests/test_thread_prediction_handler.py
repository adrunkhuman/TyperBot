"""Tests for thread prediction handler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from typer_bot.handlers.thread_prediction_handler import _prediction_cooldowns


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limiting cooldowns before each test."""
    _prediction_cooldowns.clear()


class TestOnMessage:
    """Test suite for on_message handler."""

    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, handler, mock_message):
        """Should ignore messages from bots."""
        mock_message.author.bot = True

        result = await handler.on_message(mock_message)

        assert result is False

    @pytest.mark.asyncio
    async def test_ignores_dm_messages(self, handler, mock_message):
        """Should ignore messages in DMs (no guild)."""
        mock_message.guild = None

        result = await handler.on_message(mock_message)

        assert result is False

    @pytest.mark.asyncio
    async def test_ignores_non_thread_channels(self, handler, mock_message):
        """Should ignore messages not in threads."""
        mock_message.channel = object()  # Not a thread

        result = await handler.on_message(mock_message)

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("database")
    async def test_ignores_unknown_threads(self, handler, mock_message):
        """Should ignore threads not associated with fixtures."""
        mock_message.channel.id = 999999  # Unknown thread ID

        result = await handler.on_message(mock_message)

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_rejects_message_too_long(self, handler, mock_message):
        """Should reject messages exceeding max length."""
        mock_message.content = "x" * 5001
        mock_message.channel.id = 789012

        result = await handler.on_message(mock_message)

        assert result is True
        assert "❌" in mock_message.reactions_added
        assert len(mock_message.author.dm_sent) == 1
        assert "too long" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_ignores_chatty_messages_no_scores(self, handler, mock_message):
        """Should silently ignore messages with no valid scores (chatty thread fix)."""
        mock_message.content = "Hey everyone, what do you think about today's matches?"
        mock_message.channel.id = 789012

        result = await handler.on_message(mock_message)

        assert result is False
        assert len(mock_message.reactions_added) == 0
        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_handles_invalid_prediction_format(self, handler, mock_message):
        """Should DM user when predictions have format errors."""
        # Must provide exactly 3 lines for 3 games
        mock_message.content = "Team A - Team B invalid\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        mock_message.channel.id = 789012

        result = await handler.on_message(mock_message)

        assert result is True
        assert "❌" in mock_message.reactions_added
        assert len(mock_message.author.dm_sent) == 1
        assert "Invalid predictions" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_saves_valid_predictions(self, handler, fixture_with_thread, mock_message):
        """Should save valid predictions and add success reaction."""
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        mock_message.channel.id = 789012

        result = await handler.on_message(mock_message)

        assert result is True
        assert "✅" in mock_message.reactions_added

        # Verify prediction was saved
        predictions = await handler.db.get_all_predictions(fixture_with_thread["id"])
        assert len(predictions) == 1
        assert predictions[0]["user_id"] == "123456"
        assert not predictions[0]["is_late"]

    @pytest.mark.asyncio
    async def test_marks_late_predictions(self, handler, database, mock_message, sample_games):
        """Should mark predictions as late when past deadline."""
        # Create fixture with past deadline
        deadline = datetime.now(UTC) - timedelta(hours=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.update_fixture_announcement(fixture_id, message_id="789012")

        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        mock_message.channel.id = 789012

        result = await handler.on_message(mock_message)

        assert result is True
        assert "✅" in mock_message.reactions_added

        # Verify prediction was saved as late
        predictions = await handler.db.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["is_late"]
        assert "Late prediction" in mock_message.author.dm_sent[0]


class TestEdgeCases:
    """Test suite for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_handles_permission_error_on_reaction(self, handler, mock_message, monkeypatch):
        """Should handle Forbidden exception when adding reactions."""
        import discord

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(), "Missing permissions")

        mock_message.channel.id = 789012
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        monkeypatch.setattr(mock_message, "add_reaction", raise_forbidden)

        # Should not crash
        result = await handler.on_message(mock_message)
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_handles_dm_permission_error(self, handler, mock_message, monkeypatch):
        """Should handle Forbidden exception when sending DMs."""
        import discord

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(), "Cannot send DMs")

        mock_message.channel.id = 789012
        # Must provide exactly 3 lines for 3 games, with invalid format to trigger DM
        mock_message.content = "Team A - Team B invalid\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        monkeypatch.setattr(mock_message.author, "send", raise_forbidden)

        # Should not crash and should add error reaction
        result = await handler.on_message(mock_message)
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_handles_database_error_gracefully(self, handler, mock_message, monkeypatch):
        """Should handle database errors gracefully."""

        async def raise_error(*_args, **_kwargs):
            raise Exception("Database connection failed")

        mock_message.channel.id = 789012
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        monkeypatch.setattr(handler.db, "save_prediction", raise_error)

        # Should not crash and should notify user
        result = await handler.on_message(mock_message)
        assert result is True
        assert len(mock_message.author.dm_sent) == 1
        assert "Error" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_thread_exists_but_not_in_database(self, handler, mock_message):
        """Should handle threads created manually (not through bot)."""
        mock_message.channel.id = 999999  # Thread not in database

        result = await handler.on_message(mock_message)

        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_mixed_valid_and_chat_content(self, handler, mock_message):
        """Should error when some lines have chat text instead of scores."""
        mock_message.channel.id = 789012
        # 3 lines but only 1 has a valid score - will error on lines 1 and 3
        mock_message.content = "Let's go Team A!\nTeam A - Team B 2-1\nGood luck everyone!"

        result = await handler.on_message(mock_message)

        assert result is True
        assert "❌" in mock_message.reactions_added
        assert len(mock_message.author.dm_sent) == 1
        assert "Invalid predictions" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_partial_predictions_with_errors(self, handler, mock_message):
        """Should handle messages with some valid and some invalid predictions."""
        mock_message.channel.id = 789012
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D invalid\nTeam E - Team F 0-2"

        result = await handler.on_message(mock_message)

        assert result is True
        assert "❌" in mock_message.reactions_added
        assert "Invalid predictions" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_sends_fallback_dm_on_reaction_permission_error(
        self, handler, mock_message, monkeypatch
    ):
        """Should send DM when adding reaction fails (Fallback logic)."""
        import discord

        mock_message.channel.id = 789012
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(), "Missing permissions")

        monkeypatch.setattr(mock_message, "add_reaction", raise_forbidden)

        result = await handler.on_message(mock_message)

        assert result is True
        predictions = await handler.db.get_all_predictions(1)
        assert len(predictions) == 1
        assert len(mock_message.author.dm_sent) == 1
        assert "Prediction saved" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_thread")
    async def test_survives_double_permission_failure(self, handler, mock_message, monkeypatch):
        """Should not crash if both Reaction and DM fail (Degraded UX)."""
        import discord

        mock_message.channel.id = 789012
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        async def raise_forbidden(*_args, **_kwargs):
            raise discord.Forbidden(MagicMock(), "Missing permissions")

        monkeypatch.setattr(mock_message, "add_reaction", raise_forbidden)
        monkeypatch.setattr(mock_message.author, "send", raise_forbidden)

        result = await handler.on_message(mock_message)

        assert result is True
        predictions = await handler.db.get_all_predictions(1)
        assert len(predictions) == 1


class TestIntegration:
    """Integration tests for full workflow scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_users_same_thread(
        self, handler, database, sample_games, mock_thread, mock_guild
    ):
        """Test multiple users predicting in the same thread."""
        from tests.conftest import MockMessage, MockUser

        # Create fixture
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.update_fixture_announcement(fixture_id, message_id="789012")

        # User 1 predicts
        user1 = MockUser(user_id="111", name="User1")
        message1 = MockMessage(
            content="Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
            message_id="1",
            author=user1,
            channel=mock_thread,
            guild=mock_guild,
        )
        mock_thread.id = 789012
        result = await handler.on_message(message1)
        assert result is True

        # User 2 predicts
        user2 = MockUser(user_id="222", name="User2")
        message2 = MockMessage(
            content="Team A - Team B 1-0\nTeam C - Team D 2-2\nTeam E - Team F 1-1",
            message_id="2",
            author=user2,
            channel=mock_thread,
            guild=mock_guild,
        )
        result = await handler.on_message(message2)
        assert result is True

        # Verify both predictions exist
        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 2
        user_ids = {p["user_id"] for p in predictions}
        assert user_ids == {"111", "222"}
