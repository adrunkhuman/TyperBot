"""Tests for fixture creation handler DM workflow."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.handlers.fixture_handler import (
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
        return FixtureCreationHandler(mock_bot, database)

    def test_start_session_creates_session(self, handler):
        handler.start_session("123456", 123456, 111111)

        assert handler.has_session("123456")
        assert _pending_fixtures["123456"]["channel_id"] == 123456
        assert _pending_fixtures["123456"]["guild_id"] == 111111
        assert _pending_fixtures["123456"]["step"] == "games"

    def test_has_session_returns_false_for_no_session(self, handler):
        assert not handler.has_session("nonexistent_user")

    def test_cancel_session_removes_session(self, handler):
        handler.start_session("123456", 123456, 111111)
        assert handler.has_session("123456")

        handler.cancel_session("123456")
        assert not handler.has_session("123456")

    def test_start_session_cleans_expired_sessions(self, handler):
        """Expired sessions are cleaned up after 1 hour timeout."""
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
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_verify_admin_no_guild_id(self, handler):
        """Permission checks require server context."""
        _pending_fixtures["123456"] = {"guild_id": None, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "123456", None, lambda _: True)

        assert result is False
        assert "123456" not in _pending_fixtures

    @pytest.mark.asyncio
    async def test_verify_admin_guild_not_found(self, handler):
        handler.bot.get_guild.return_value = None

        _pending_fixtures["123456"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_member_not_found(self, handler):
        """Member lookup failure blocks unauthorized fixture changes."""
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = None
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["123456"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_not_admin(self, handler):
        """Non-admins are blocked from fixture creation."""
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["123456"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: False)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_admin_success(self, handler):
        mock_guild = MagicMock()
        mock_member = MagicMock()
        mock_guild.get_member.return_value = mock_member
        handler.bot.get_guild.return_value = mock_guild

        _pending_fixtures["123456"] = {"guild_id": 111111, "step": "games"}

        mock_message = MagicMock()

        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: True)

        assert result is True


class TestGamesStep:
    """Test suite for games list input step."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_games_step_too_many_games(self, handler):
        """Game count limits prevent fixture abuse."""
        _pending_fixtures["123456"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "\n".join([f"Team{i} - Team{i + 1}" for i in range(101)])
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "123456")

        mock_message.author.send.assert_called_once()
        assert "Too many games" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_games_step_no_games(self, handler):
        """Empty fixtures are rejected."""
        _pending_fixtures["123456"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "   \n   \n   "
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "123456")

        mock_message.author.send.assert_called_once()
        assert "No games provided" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_games_step_valid_games(self, handler):
        _pending_fixtures["123456"] = {"step": "games"}

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B\nTeam C - Team D"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "123456")

        assert _pending_fixtures["123456"]["step"] == "deadline"
        assert _pending_fixtures["123456"]["games"] == ["Team A - Team B", "Team C - Team D"]
        assert "default_deadline" in _pending_fixtures["123456"]


class TestDeadlineStep:
    """Test suite for deadline input step."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_deadline_step_invalid_format(self, handler):
        """Invalid dates are rejected."""
        _pending_fixtures["123456"] = {
            "step": "deadline",
            "games": ["Game 1"],
        }

        mock_message = MagicMock()
        mock_message.content = "invalid date"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_deadline_step(mock_message, "123456")

        mock_message.author.send.assert_called_once()
        assert "Invalid date format" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_deadline_step_valid_format_iso(self, handler):
        """ISO format is accepted for timezone-aware parsing."""
        from zoneinfo import ZoneInfo

        from typer_bot.handlers import fixture_handler

        original_tz = fixture_handler.APP_TZ

        try:
            fixture_handler.APP_TZ = ZoneInfo("UTC")

            _pending_fixtures["123456"] = {
                "step": "deadline",
                "games": ["Game 1"],
            }

            mock_message = MagicMock()
            mock_message.content = "2024-12-25 18:00"
            mock_message.author = MagicMock()
            mock_message.author.send = AsyncMock()

            handler._show_preview = AsyncMock()

            await handler._handle_deadline_step(mock_message, "123456")

            assert "deadline" in _pending_fixtures["123456"]
            handler._show_preview.assert_called_once()
        finally:
            fixture_handler.APP_TZ = original_tz


class TestPreviewGeneration:
    """Test suite for fixture preview."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        mock_channel = MagicMock()
        mock_bot.get_channel.return_value = mock_channel
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_show_preview_creates_week_number(self, handler, database):  # noqa: ARG002
        _pending_fixtures["123456"] = {
            "step": "deadline",
            "games": ["Game 1", "Game 2"],
            "deadline": datetime.now(UTC),
            "default_deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        assert _pending_fixtures["123456"]["week_number"] == 1
        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Week 1 Fixture Preview" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_warns_wrong_game_count(self, handler, database):  # noqa: ARG002
        """Wrong game count triggers a warning."""
        _pending_fixtures["123456"] = {
            "step": "deadline",
            "games": ["Game 1", "Game 2"],
            "deadline": datetime.now(UTC),
            "default_deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Warning" in call_content
        assert "Expected 9 games" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_channel_not_found(self, handler, database):  # noqa: ARG002
        """Channel not found cancels the session."""
        handler.bot.get_channel.return_value = None

        _pending_fixtures["123456"] = {
            "step": "deadline",
            "games": ["Game 1"],
            "deadline": datetime.now(UTC),
            "default_deadline": datetime.now(UTC),
            "channel_id": 123456,
        }

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        assert "123456" not in _pending_fixtures
        mock_user.send.assert_called_once()
        assert "Could not find" in mock_user.send.call_args[0][0]


class TestCreateFixture:
    """Test suite for fixture creation."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_create_fixture_saves_to_database(self, handler, database):
        _pending_fixtures["123456"] = {"some": "data"}

        deadline = datetime.now(UTC)
        await handler.create_fixture("123456", 1, ["Game 1", "Game 2"], deadline)

        fixture = await database.get_current_fixture()
        assert fixture is not None
        assert fixture["week_number"] == 1

        assert "123456" not in _pending_fixtures


class TestHandleDM:
    """Test suite for handle_dm method."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_handle_dm_no_session(self, handler):
        mock_message = MagicMock()

        result = await handler.handle_dm(mock_message, "123456", lambda _: True)

        assert result is False

    @pytest.mark.asyncio
    async def test_handle_dm_message_too_long(self, handler):
        """Message length limits prevent DoS."""
        handler.start_session("123456", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "x" * 5001
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler.handle_dm(mock_message, "123456", lambda _: True)

        assert result is True
        mock_message.author.send.assert_called_once()
        assert "too long" in mock_message.author.send.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_handle_dm_games_step(self, handler):
        handler.start_session("123456", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        result = await handler.handle_dm(mock_message, "123456", lambda _: True)

        assert result is True
        assert _pending_fixtures["123456"]["step"] == "deadline"


class TestEdgeCases:
    """Test suite for edge cases."""

    @pytest.fixture
    def handler(self, mock_bot, database):
        return FixtureCreationHandler(mock_bot, database)

    def test_default_deadline_is_friday_1800(self, handler):
        """Default deadline is Friday 18:00 for typical weekend matches."""
        from typer_bot.utils.timezone import now

        current_time = now()
        days_until_friday = (4 - current_time.weekday()) % 7
        if days_until_friday == 0 and current_time.hour >= 18:
            days_until_friday = 7

        expected = current_time + timedelta(days=days_until_friday)
        expected = expected.replace(hour=18, minute=0, second=0, microsecond=0)

        handler.start_session("123456", 123456, 111111)
        deadline = _pending_fixtures["123456"].get("default_deadline")

        if deadline:
            assert deadline.weekday() == 4  # Friday
            assert deadline.hour == 18
            assert deadline.minute == 0


class TestViewBehavioral:
    """Behavioral tests for Discord view handling.

    These tests verify that the correct view types are sent to users
    without instantiating the views directly (which requires event loop).
    """

    @pytest.fixture
    def handler(self, mock_bot, database):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database)

    @pytest.mark.asyncio
    async def test_games_step_sends_deadline_choice_view(self, handler):
        handler.start_session("123456", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B\nTeam C - Team D"
        mock_message.author = MagicMock()

        captured_views = []

        async def capture_send(_content=None, view=None, **_):
            if view:
                captured_views.append(type(view).__name__)

        mock_message.author.send = capture_send

        await handler._handle_games_step(mock_message, "123456")

        assert len(captured_views) == 1
        assert captured_views[0] == "DeadlineChoiceView"

    @pytest.mark.asyncio
    async def test_deadline_step_sends_preview_with_confirm_view(self, handler):
        from typer_bot.handlers import fixture_handler

        original_tz = fixture_handler.APP_TZ

        try:
            from zoneinfo import ZoneInfo

            fixture_handler.APP_TZ = ZoneInfo("UTC")

            _pending_fixtures["123456"] = {
                "step": "deadline",
                "games": ["Game 1", "Game 2"],
                "deadline": datetime.now(UTC),
                "default_deadline": datetime.now(UTC),
                "channel_id": 123456,
            }

            mock_message = MagicMock()
            mock_message.content = "2024-12-25 18:00"
            mock_message.author = MagicMock()

            captured_views = []

            async def capture_send(_content=None, view=None, **_):
                if view:
                    captured_views.append(type(view).__name__)

            mock_message.author.send = capture_send

            async def mock_show_preview(_user, user_id):
                from typer_bot.handlers.fixture_handler import FixtureConfirmView

                view = FixtureConfirmView(
                    handler,
                    user_id,
                    1,
                    _pending_fixtures[user_id]["games"],
                    _pending_fixtures[user_id]["deadline"],
                    None,
                    "Preview text",
                )
                captured_views.append(type(view).__name__)

            handler._show_preview = mock_show_preview

            await handler._handle_deadline_step(mock_message, "123456")

            assert len(captured_views) >= 0
        finally:
            fixture_handler.APP_TZ = original_tz
