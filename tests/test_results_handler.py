"""Tests for results entry handler DM workflow."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.handlers.results_handler import ResultsEntryHandler


class TestSessionManagement:
    """Test suite for results entry session management."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return ResultsEntryHandler(mock_bot, database, workflow_state)

    def test_start_session_creates_session(self, handler):
        handler.start_session("123456", 1, 111111, week_number=1)
        assert handler.has_session("123456")
        assert handler.get_session("123456").fixture_id == 1

    def test_has_session_returns_false_for_no_session(self, handler):
        assert not handler.has_session("nonexistent_user")

    def test_cancel_session_removes_session(self, handler):
        handler.start_session("123456", 1, 111111, week_number=1)
        assert handler.has_session("123456")
        handler.cancel_session("123456")
        assert not handler.has_session("123456")


class TestAdminVerification:
    """Test suite for admin permission verification."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return ResultsEntryHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_verify_admin_no_guild_id(self, handler):
        """Permission checks require server context."""
        session = handler.workflow_state.start_results_session("123456", 1, 111111)
        session.guild_id = None
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()
        result = await handler._verify_admin(mock_message, "123456", None, lambda _: True)
        assert result is False
        assert handler.get_session("123456") is None

    @pytest.mark.asyncio
    async def test_verify_admin_guild_not_found(self, handler):
        handler.bot.get_guild.return_value = None
        handler.workflow_state.start_results_session("123456", 1, 111111)
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()
        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: True)
        assert result is False


class TestHandleDM:
    """Test suite for handle_dm method."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return ResultsEntryHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_handle_dm_no_session(self, handler):
        mock_message = MagicMock()
        result = await handler.handle_dm(mock_message, "123456", lambda _: True)
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_dm_message_too_long(self, handler):
        """Message length limits prevent resource exhaustion."""
        handler.start_session("123456", 1, 111111, week_number=1)
        mock_message = MagicMock()
        mock_message.content = "x" * 5001
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()
        result = await handler.handle_dm(mock_message, "123456", lambda _: True)
        assert result is True
        mock_message.author.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_dm_fixture_not_found(self, handler):
        """Mid-session fixture deletion is detected."""
        handler.start_session("123456", 999, 111111, week_number=1)
        mock_message = MagicMock()
        mock_message.content = "Game 1 2-1"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()
        result = await handler.handle_dm(mock_message, "123456", lambda _: True)
        assert result is True
        assert handler.get_session("123456") is None


class TestSaveResults:
    """Test suite for save_results method."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return ResultsEntryHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_save_results_saves_to_database(self, handler, database, sample_games):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        handler.workflow_state.start_results_session("123456", fixture_id, 111111)
        await handler.save_results("123456", fixture_id, 1, ["2-1", "1-1", "0-2"])
        results = await database.get_results(fixture_id)
        assert results is not None
        assert handler.get_session("123456") is None


class TestViewBehavioral:
    """Behavioral tests for Discord view handling in results entry.

    These tests verify that the correct view types are sent to users
    without instantiating the views directly (which requires event loop).
    """

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return ResultsEntryHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_valid_results_sends_confirm_view(self, handler, database, sample_games):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        handler.start_session("123456", fixture_id, 111111, week_number=1)

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        mock_message.author = MagicMock()

        captured_views = []

        async def capture_send(_content=None, view=None, **_):
            if view:
                captured_views.append(type(view).__name__)
            mock_msg = MagicMock()
            mock_msg.edit = AsyncMock()
            return mock_msg

        mock_message.author.send = capture_send

        await handler.handle_dm(mock_message, "123456", lambda _: True)
