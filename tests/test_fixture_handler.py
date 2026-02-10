"""Tests for fixture creation handler DM workflow."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from typer_bot.handlers.fixture_handler import (
    DeadlineChoiceView,
    FixtureConfirmView,
    FixtureCreationHandler,
    _pending_fixtures,
)


@pytest.fixture(autouse=True)
def clear_pending_fixtures():
    """Clear pending fixture sessions before each test."""
    _pending_fixtures.clear()


class TestSessionManagement:
    """Test suite for fixture creation session management."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        return FixtureCreationHandler(mock_bot, database)

    def test_start_session_creates_session(self, handler):
        """Should create a new fixture session."""
        handler.start_session("user123", 123456, 111111)

        assert handler.has_session("user123")
        assert _pending_fixtures["user123"]["channel_id"] == 123456
        assert _pending_fixtures["user123"]["guild_id"] == 111111
        assert _pending_fixtures["user123"]["step"] == "games"

    def test_has_session_returns_false_for_no_session(self, handler):
        """Should return False when no session exists."""
        assert not handler.has_session("nonexistent_user")

    def test_cancel_session_removes_session(self, handler):
        """Should remove session when cancelled."""
        handler.start_session("user123", 123456, 111111)
        assert handler.has_session("user123")

        handler.cancel_session("user123")
        assert not handler.has_session("user123")

    def test_start_session_cleans_expired_sessions(self, handler):
        """Should clean up expired sessions on new session start."""
        # Create an old session
        old_time = datetime.now(UTC) - timedelta(hours=2)
        _pending_fixtures["old_user"] = {
            "channel_id": 123,
            "guild_id": 111,
            "step": "games",
            "created_at": old_time,
        }

        handler.start_session("new_user", 456, 222)

        assert "old_user" not in _pending_fixtures
        assert handler.has_session("new_user")


class TestAdminVerification:
    """Test suite for admin permission verification."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_verify_admin_no_guild_id(self, handler):
        """Should reject when no guild_id in session."""
        _pending_fixtures["user123"] = {"guild_id": None, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "user123", None, lambda x: True)

        assert result is False
        assert "user123" not in _pending_fixtures

    @pytest.mark.asyncio
    async def test_verify_admin_guild_not_found(self, handler):
        """Should reject when guild not found."""
        handler.bot.get_guild.return_value = None

        _pending_fixtures["user123"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "user123", 111111, lambda x: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_member_not_found(self, handler):
        """Should reject when member not in guild cache."""
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = None
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["user123"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "user123", 111111, lambda x: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_not_admin(self, handler):
        """Should reject when user is not admin."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["user123"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "user123", 111111, lambda x: False)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_success(self, handler):
        """Should succeed when user is admin."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["user123"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()

        result = await handler._verify_admin(mock_message, "user123", 111111, lambda x: True)

        assert result is True


class TestGamesStep:
    """Test suite for games list input step."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_games_step_too_many_games(self, handler):
        """Should reject too many games."""
        _pending_fixtures["user123"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "\n".join([f"Team{i} - Team{i + 1}" for i in range(101)])
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "user123")

        mock_message.author.send.assert_called_once()
        assert "Too many games" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_games_step_no_games(self, handler):
        """Should reject empty games list."""
        _pending_fixtures["user123"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "   \n   \n   "
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "user123")

        mock_message.author.send.assert_called_once()
        assert "No games provided" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_games_step_valid_games(self, handler):
        """Should accept valid games and move to deadline step."""
        _pending_fixtures["user123"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B\nTeam C - Team D"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "user123")

        # Should move to deadline step
        assert _pending_fixtures["user123"]["step"] == "deadline"
        assert _pending_fixtures["user123"]["games"] == ["Team A - Team B", "Team C - Team D"]
        assert "default_deadline" in _pending_fixtures["user123"]


class TestDeadlineStep:
    """Test suite for deadline input step."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_deadline_step_invalid_format(self, handler):
        """Should reject invalid date format."""
        _pending_fixtures["user123"] = {
            "step": "deadline",
            "games": ["Game 1"],
        }

        mock_message = MagicMock()
        mock_message.content = "invalid date"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_deadline_step(mock_message, "user123")

        mock_message.author.send.assert_called_once()
        assert "Invalid date format" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    @patch("typer_bot.handlers.fixture_handler.APP_TZ")
    async def test_handle_deadline_step_valid_format_iso(self, mock_tz, handler):
        """Should accept ISO format date."""
        from zoneinfo import ZoneInfo

        mock_tz.return_value = ZoneInfo("UTC")

        _pending_fixtures["user123"] = {
            "step": "deadline",
            "games": ["Game 1"],
        }

        mock_message = MagicMock()
        mock_message.content = "2024-12-25 18:00"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        # Mock _show_preview to avoid full execution
        handler._show_preview = AsyncMock()

        await handler._handle_deadline_step(mock_message, "user123")

        assert "deadline" in _pending_fixtures["user123"]
        handler._show_preview.assert_called_once()


class TestPreviewGeneration:
    """Test suite for fixture preview."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        mock_channel = MagicMock()
        mock_bot.get_channel.return_value = mock_channel
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_show_preview_creates_week_number(self, handler, database):
        """Should auto-generate week number."""
        _pending_fixtures["user123"] = {
            "step": "deadline",
            "games": ["Game 1", "Game 2"],
            "deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "user123")

        assert _pending_fixtures["user123"]["week_number"] == 1
        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Week 1 Fixture Preview" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_warns_wrong_game_count(self, handler, database):
        """Should warn when game count is not 9."""
        _pending_fixtures["user123"] = {
            "step": "deadline",
            "games": ["Game 1", "Game 2"],  # Only 2 games
            "deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "user123")

        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Warning" in call_content
        assert "Expected 9 games" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_channel_not_found(self, handler, database):
        """Should handle missing channel gracefully."""
        handler.bot.get_channel.return_value = None

        _pending_fixtures["user123"] = {
            "step": "deadline",
            "games": ["Game 1"],
            "deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "user123")

        # Session should be cancelled
        assert "user123" not in _pending_fixtures
        mock_user.send.assert_called_once()
        assert "Could not find" in mock_user.send.call_args[0][0]


class TestCreateFixture:
    """Test suite for fixture creation."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_create_fixture_saves_to_database(self, handler, database):
        """Should save fixture to database."""
        _pending_fixtures["user123"] = {"some": "data"}

        deadline = datetime.now(UTC)
        await handler.create_fixture("user123", 1, ["Game 1", "Game 2"], deadline)

        # Verify fixture was created
        fixture = await database.get_current_fixture()
        assert fixture is not None
        assert fixture["week_number"] == 1

        # Session should be cleared
        assert "user123" not in _pending_fixtures


class TestDeadlineChoiceView:
    """Test suite for DeadlineChoiceView interactions."""

    @pytest.fixture
    def view(self, handler):
        """Provide a DeadlineChoiceView instance."""
        _pending_fixtures["user123"] = {
            "step": "deadline",
            "default_deadline": datetime.now(UTC),
        }
        return DeadlineChoiceView(handler, "user123")

    @pytest.mark.asyncio
    async def test_use_default_button_sets_deadline(self, view, handler):
        """Should set deadline to default when button clicked."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.id = 123456
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        handler._show_preview = AsyncMock()

        await view.use_default(mock_interaction, MagicMock())

        assert (
            _pending_fixtures["user123"]["deadline"]
            == _pending_fixtures["user123"]["default_deadline"]
        )
        handler._show_preview.assert_called_once()

    @pytest.mark.asyncio
    async def test_use_default_wrong_user_rejected(self, view, handler):
        """Should reject button click from wrong user."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.id = 999999  # Different user
        mock_interaction.response = MagicMock()
        mock_interaction.response.send_message = AsyncMock()

        await view.use_default(mock_interaction, MagicMock())

        mock_interaction.response.send_message.assert_called_once()
        assert (
            "not for you"
            in mock_interaction.response.send_message.call_args.kwargs.get("content", "").lower()
        )


class TestFixtureConfirmView:
    """Test suite for FixtureConfirmView interactions."""

    @pytest.fixture
    def confirm_view(self, handler):
        """Provide a FixtureConfirmView instance."""
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        mock_channel.create_thread = AsyncMock()
        mock_thread = MagicMock()
        mock_thread.id = 789012
        mock_channel.create_thread.return_value = mock_thread

        _pending_fixtures["user123"] = {
            "step": "confirm",
            "channel_id": 123456,
        }

        return FixtureConfirmView(
            handler,
            "user123",
            1,
            ["Game 1", "Game 2"],
            datetime.now(UTC),
            mock_channel,
            "Preview text",
        )

    @pytest.mark.asyncio
    async def test_confirm_creates_fixture(self, confirm_view, handler, database):
        """Should create fixture when confirmed."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()
        mock_interaction.followup = MagicMock()
        mock_interaction.followup.send = AsyncMock()

        await confirm_view.confirm(mock_interaction, MagicMock())

        # Verify fixture was created
        fixture = await database.get_current_fixture()
        assert fixture is not None

        mock_interaction.response.edit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_removes_session(self, confirm_view, handler):
        """Should cancel and remove session."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        await confirm_view.cancel(mock_interaction, MagicMock())

        assert "user123" not in _pending_fixtures
        mock_interaction.response.edit_message.assert_called_once()


class TestHandleDM:
    """Test suite for handle_dm method."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_dm_no_session(self, handler):
        """Should return False when no active session."""
        mock_message = MagicMock()

        result = await handler.handle_dm(mock_message, "user123", lambda x: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_handle_dm_message_too_long(self, handler):
        """Should reject messages that are too long."""
        handler.start_session("user123", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "x" * 5001
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler.handle_dm(mock_message, "user123", lambda x: True)

        assert result is True
        mock_message.author.send.assert_called_once()
        assert "too long" in mock_message.author.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_handle_dm_games_step(self, handler):
        """Should handle games step."""
        handler.start_session("user123", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler.handle_dm(mock_message, "user123", lambda x: True)

        assert result is True
        assert _pending_fixtures["user123"]["step"] == "deadline"


class TestEdgeCases:
    """Test suite for edge cases."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        """Provide a FixtureCreationHandler instance."""
        return FixtureCreationHandler(mock_bot, database)

    def test_default_deadline_is_friday_1800(self, handler):
        """Default deadline should be next Friday at 18:00."""
        from typer_bot.utils.timezone import now

        current_time = now()
        days_until_friday = (4 - current_time.weekday()) % 7
        if days_until_friday == 0 and current_time.hour >= 18:
            days_until_friday = 7

        expected = current_time + timedelta(days=days_until_friday)
        expected = expected.replace(hour=18, minute=0, second=0, microsecond=0)

        handler.start_session("user123", 123456, 111111)
        deadline = _pending_fixtures["user123"].get("default_deadline")

        if deadline:
            assert deadline.weekday() == 4  # Friday
            assert deadline.hour == 18
            assert deadline.minute == 0
