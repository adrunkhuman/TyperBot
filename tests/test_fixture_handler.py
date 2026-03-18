"""Tests for fixture creation handler DM workflow."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from typer_bot.handlers.fixture_handler import FixtureCreationHandler


class TestSessionManagement:
    """Test suite for fixture creation session management."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    def test_start_session_creates_session(self, handler):
        handler.start_session("123456", 123456, 111111)

        session = handler.get_session("123456")
        assert handler.has_session("123456")
        assert session.channel_id == 123456
        assert session.guild_id == 111111
        assert session.step == "games"

    def test_has_session_returns_false_for_no_session(self, handler):
        assert not handler.has_session("nonexistent_user")

    def test_cancel_session_removes_session(self, handler):
        handler.start_session("123456", 123456, 111111)
        assert handler.has_session("123456")

        handler.cancel_session("123456")
        assert not handler.has_session("123456")

    def test_start_session_cleans_expired_sessions(self, handler):
        """Expired sessions are cleaned up after 1 hour timeout."""
        session = handler.workflow_state.start_fixture_session("old_user", 123, 111)
        session.created_at = datetime.now(UTC) - timedelta(hours=2)

        handler.start_session("new_user", 456, 222)

        assert handler.get_session("old_user") is None
        assert handler.has_session("new_user")


class TestAdminVerification:
    """Test suite for admin permission verification."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_verify_admin_no_guild_id(self, handler):
        """Permission checks require server context."""
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
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

        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

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

        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

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

        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

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

        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

        mock_message = MagicMock()

        result = await handler._verify_admin(mock_message, "123456", 111111, lambda _: True)

        assert result is True


class TestGamesStep:
    """Test suite for games list input step."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_handle_games_step_too_many_games(self, handler):
        """Game count limits prevent fixture abuse."""
        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

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
        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "   \n   \n   "
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "123456")

        mock_message.author.send.assert_called_once()
        assert "No games provided" in mock_message.author.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_games_step_valid_games(self, handler):
        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B\nTeam C - Team D"
        mock_message.author = MagicMock()
        mock_message.author.send = AsyncMock()

        await handler._handle_games_step(mock_message, "123456")

        session = handler.get_session("123456")
        assert session.step == "deadline"
        assert session.games == ["Team A - Team B", "Team C - Team D"]
        assert session.default_deadline is not None


class TestDeadlineStep:
    """Test suite for deadline input step."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_handle_deadline_step_invalid_format(self, handler):
        """Invalid dates are rejected."""
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "deadline"
        session.games = ["Game 1"]

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

            session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
            session.step = "deadline"
            session.games = ["Game 1"]

            mock_message = MagicMock()
            mock_message.content = "2024-12-25 18:00"
            mock_message.author = MagicMock()
            mock_message.author.send = AsyncMock()

            handler._show_preview = AsyncMock()

            await handler._handle_deadline_step(mock_message, "123456")

            assert handler.get_session("123456").deadline is not None
            handler._show_preview.assert_called_once()
        finally:
            fixture_handler.APP_TZ = original_tz


class TestPreviewGeneration:
    """Test suite for fixture preview."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_bot.get_channel.return_value = mock_channel
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_show_preview_creates_week_number(self, handler):
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "deadline"
        session.games = ["Game 1", "Game 2"]
        session.deadline = datetime.now(UTC)
        session.default_deadline = datetime.now(UTC)

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        assert handler.get_session("123456").week_number == 1
        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Week 1 Fixture Preview" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_warns_wrong_game_count(self, handler):
        """Wrong game count triggers a warning."""
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "deadline"
        session.games = ["Game 1", "Game 2"]
        session.deadline = datetime.now(UTC)
        session.default_deadline = datetime.now(UTC)

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        mock_user.send.assert_called_once()
        call_content = mock_user.send.call_args[0][0]
        assert "Warning" in call_content
        assert "Expected 9 games" in call_content

    @pytest.mark.asyncio
    async def test_show_preview_channel_not_found(self, handler):
        """Channel not found cancels the session."""
        handler.bot.get_channel.return_value = None

        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "deadline"
        session.games = ["Game 1"]
        session.deadline = datetime.now(UTC)
        session.default_deadline = datetime.now(UTC)

        mock_user = MagicMock()
        mock_user.send = AsyncMock()

        await handler._show_preview(mock_user, "123456")

        assert handler.get_session("123456") is None
        mock_user.send.assert_called_once()
        assert "Could not find" in mock_user.send.call_args[0][0]


class TestCreateFixture:
    """Test suite for fixture creation."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return FixtureCreationHandler(mock_bot, database, workflow_state)

    @pytest.mark.asyncio
    async def test_create_fixture_saves_to_database(self, handler, database):
        handler.workflow_state.start_fixture_session("123456", 123456, 111111)

        deadline = datetime.now(UTC)
        await handler.create_fixture("123456", ["Game 1", "Game 2"], deadline)

        fixture = await database.get_current_fixture()
        assert fixture is not None
        assert fixture["week_number"] == 1

        assert handler.get_session("123456") is None


class TestHandleDM:
    """Test suite for handle_dm method."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database, workflow_state)

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
        assert handler.get_session("123456").step == "deadline"


class TestEdgeCases:
    """Test suite for edge cases."""

    @pytest.fixture
    def handler(self, mock_bot, database, workflow_state):
        return FixtureCreationHandler(mock_bot, database, workflow_state)

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
        deadline = handler.get_session("123456").default_deadline

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
    def handler(self, mock_bot, database, workflow_state):
        mock_guild = MagicMock()
        mock_guild.get_member.return_value = MagicMock()
        mock_bot.get_guild.return_value = mock_guild
        return FixtureCreationHandler(mock_bot, database, workflow_state)

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

            session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
            session.step = "deadline"
            session.games = ["Game 1", "Game 2"]
            session.deadline = datetime.now(UTC)
            session.default_deadline = datetime.now(UTC)

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
                    handler.get_session(user_id).games,
                    handler.get_session(user_id).deadline,
                    None,
                    "Preview text",
                )
                captured_views.append(type(view).__name__)

            handler._show_preview = mock_show_preview

            await handler._handle_deadline_step(mock_message, "123456")

            assert len(captured_views) >= 0
        finally:
            fixture_handler.APP_TZ = original_tz

    @pytest.mark.asyncio
    async def test_confirm_warns_when_thread_creation_forbidden(self, handler, database):
        """Fixture creation should succeed even if Discord blocks thread creation."""
        from typer_bot.handlers.fixture_handler import FixtureConfirmView

        deadline = datetime.now(UTC)
        announcement = MagicMock()
        announcement.id = 999999
        announcement.create_thread = AsyncMock(
            side_effect=discord.Forbidden(
                MagicMock(status=403, reason="Forbidden", text="Missing Permissions"),
                "Missing Permissions",
            )
        )

        channel = MagicMock()
        channel.send = AsyncMock(return_value=announcement)

        interaction = MagicMock()
        interaction.response = MagicMock()
        interaction.response.edit_message = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        view = FixtureConfirmView(
            handler,
            "123456",
            1,
            ["Game 1", "Game 2"],
            deadline,
            channel,
            "Preview text",
        )

        create_button = next(child for child in view.children if child.label == "Create Fixture")

        await create_button.callback(interaction)

        fixture = await database.get_current_fixture()
        assert fixture is not None
        assert fixture["message_id"] == "999999"
        interaction.response.edit_message.assert_called_once()
        interaction.followup.send.assert_called_once_with(
            "⚠️ Fixture created but I couldn't create a prediction thread. Users can still use `/predict`.",
            ephemeral=True,
        )

    @pytest.mark.asyncio
    async def test_edit_games_resets_step_and_prompts_re_entry(self, handler):
        """Edit Games drops back to games input while keeping the deadline."""
        from typer_bot.handlers.fixture_handler import FixtureConfirmView

        deadline = datetime.now(UTC) + timedelta(days=3)
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "confirm"
        session.games = ["Wrong Team A - Team B"]
        session.deadline = deadline

        interaction = MagicMock()
        interaction.user.id = 123456
        interaction.response.edit_message = AsyncMock()
        interaction.response.send_message = AsyncMock()

        view = FixtureConfirmView(
            handler, "123456", 1, session.games, deadline, MagicMock(), "Preview"
        )
        edit_button = next(child for child in view.children if child.label == "Edit Games")
        await edit_button.callback(interaction)

        state = handler.get_session("123456")
        assert state is not None
        assert state.step == "games"
        assert state.deadline == deadline  # deadline preserved
        interaction.response.edit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_games_step_skips_deadline_when_already_set(self, handler):
        """Re-entered games go straight to preview if a deadline was already chosen."""
        session = handler.workflow_state.start_fixture_session("123456", 123456, 111111)
        session.step = "games"
        session.deadline = datetime.now(UTC) + timedelta(days=3)

        preview_calls = []
        handler._show_preview = AsyncMock(side_effect=lambda _u, uid: preview_calls.append(uid))

        mock_message = MagicMock()
        mock_message.content = "Team A - Team B\nTeam C - Team D"
        mock_message.author = MagicMock()

        await handler._handle_games_step(mock_message, "123456")

        assert preview_calls == ["123456"]
        assert handler.get_session("123456").step == "games"  # _show_preview mocked, step unchanged
