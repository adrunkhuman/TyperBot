"""Tests for main Discord bot implementation."""

import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from typer_bot.bot import TyperBot, main


def guild_permissions(
    *,
    send_messages=True,
    send_messages_in_threads=True,
    read_message_history=True,
    add_reactions=True,
    create_public_threads=True,
):
    return SimpleNamespace(
        send_messages=send_messages,
        send_messages_in_threads=send_messages_in_threads,
        read_message_history=read_message_history,
        add_reactions=add_reactions,
        create_public_threads=create_public_threads,
    )


class TestBotInitialization:
    """Test suite for bot initialization and setup."""

    @pytest.mark.asyncio
    async def test_bot_has_required_intents(self):
        """Message content and member intents are required for prediction processing and permission verification."""
        with (
            patch("typer_bot.bot.commands.Bot.__init__"),
            patch("typer_bot.bot.discord.Intents") as mock_intents,
        ):
            mock_intent_instance = MagicMock()
            mock_intent_instance.message_content = False
            mock_intent_instance.members = False
            mock_intents.default.return_value = mock_intent_instance

            with suppress(Exception):
                TyperBot()

            assert mock_intent_instance.message_content is True
            assert mock_intent_instance.members is True


class TestSetupHook:
    """Test suite for setup_hook lifecycle."""

    @pytest.fixture
    async def bot_instance(self):
        mock_tree = MagicMock()
        mock_tree.sync = AsyncMock(return_value=[])
        mock_admin_cog = MagicMock()
        mock_user_cog = MagicMock()
        mock_cogs = {"AdminCommands": mock_admin_cog, "UserCommands": mock_user_cog}
        with (
            patch("typer_bot.bot.commands.Bot.__init__", return_value=None),
            patch.object(TyperBot, "tree", mock_tree),
            patch.object(TyperBot, "cogs", mock_cogs),
        ):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.db.db_path = "test.db"
            bot.db.initialize = AsyncMock()
            bot.thread_handler = MagicMock()
            bot.load_extension = AsyncMock()
            bot.reminder_task = MagicMock()
            bot._cleanup_sessions_task = MagicMock()
            yield bot

    @pytest.mark.asyncio
    async def test_setup_hook_initializes_database(self, bot_instance):
        """Database is initialized during setup_hook."""
        await bot_instance.setup_hook()
        bot_instance.db.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_hook_runs_optional_auto_seed_after_database_init(self, bot_instance):
        with patch("typer_bot.bot.maybe_auto_seed_test_data", AsyncMock()) as auto_seed:
            await bot_instance.setup_hook()

        auto_seed.assert_awaited_once_with("test.db")

    @pytest.mark.asyncio
    async def test_setup_hook_loads_user_commands(self, bot_instance):
        """User commands cog provides /predict and /standings."""
        await bot_instance.setup_hook()
        bot_instance.load_extension.assert_any_call("typer_bot.commands.user_commands")

    @pytest.mark.asyncio
    async def test_setup_hook_loads_admin_commands(self, bot_instance):
        """Admin commands cog provides league management and modal workflows."""
        await bot_instance.setup_hook()
        bot_instance.load_extension.assert_any_call("typer_bot.commands.admin_commands")

    @pytest.mark.asyncio
    async def test_setup_hook_syncs_commands(self, bot_instance):
        """Commands are synchronized with Discord."""
        await bot_instance.setup_hook()
        bot_instance.tree.sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_hook_starts_reminder_task(self, bot_instance):
        """Reminder task starts automatically."""
        await bot_instance.setup_hook()
        bot_instance.reminder_task.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_hook_raises_on_db_failure(self, bot_instance):
        """Database failure halts startup."""
        bot_instance.db.initialize.side_effect = Exception("DB Error")

        with pytest.raises(Exception, match="DB Error"):
            await bot_instance.setup_hook()

    @pytest.mark.asyncio
    async def test_cleanup_sessions_task_cleans_admin_state_only(self):
        admin_cog = MagicMock()
        admin_cog.cleanup_expired_state.return_value = 1
        with patch.object(TyperBot, "cogs", {"AdminCommands": admin_cog}):
            bot = TyperBot.__new__(TyperBot)
            bot.thread_handler = MagicMock(spec=[])

            await TyperBot._cleanup_sessions_task.coro(bot)

        admin_cog.cleanup_expired_state.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_cleanup_sessions_task_ignores_missing_admin_cog(self):
        with patch.object(TyperBot, "cogs", {}):
            bot = TyperBot.__new__(TyperBot)
            bot.thread_handler = MagicMock(spec=[])

            await TyperBot._cleanup_sessions_task.coro(bot)


class TestOnReady:
    """Test suite for on_ready event handler."""

    @pytest.fixture
    def bot_instance(self):
        mock_user = MagicMock()
        mock_user.id = 123456
        mock_user.name = "TestBot"
        with (
            patch("typer_bot.bot.commands.Bot.__init__", return_value=None),
            patch.object(TyperBot, "user", mock_user),
            patch.object(TyperBot, "guilds", []),
        ):
            bot = TyperBot.__new__(TyperBot)
            bot._check_permissions = AsyncMock()
            bot._sync_fixture_thread = AsyncMock()
            yield bot

    @pytest.mark.asyncio
    async def test_on_ready_checks_permissions(self, bot_instance):
        """Permission verification at startup alerts admins to missing rights."""
        await bot_instance.on_ready()
        bot_instance._check_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_ready_syncs_fixture_threads(self, bot_instance):
        """Thread synchronization restores prediction listening after restarts."""
        await bot_instance.on_ready()
        bot_instance._sync_fixture_thread.assert_called_once()


class TestPermissionCheck:
    """Test suite for permission checking."""

    @pytest.fixture
    def bot_instance(self):
        with (
            patch("typer_bot.bot.commands.Bot.__init__", return_value=None),
            patch.object(TyperBot, "guilds", []),
        ):
            bot = TyperBot.__new__(TyperBot)
            yield bot

    @pytest.mark.asyncio
    async def test_check_permissions_logs_missing_permissions(self, bot_instance):
        """Missing permission warnings help admins identify configuration issues."""
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 123456
        mock_guild.me = MagicMock()
        mock_guild.me.guild_permissions = guild_permissions(
            send_messages=False,
            send_messages_in_threads=False,
            read_message_history=False,
            add_reactions=False,
            create_public_threads=False,
        )

        bot_instance.guilds = [mock_guild]

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._check_permissions()
            warning_call = mock_logger.warning.call_args

        assert warning_call.args[0] == "Guild missing permissions"
        assert warning_call.kwargs["extra"]["guild_name"] == "Test Guild"
        assert "Send Messages" in warning_call.kwargs["extra"]["missing_permissions"]
        assert "Send Messages in Threads" in warning_call.kwargs["extra"]["missing_permissions"]
        assert "Read Message History" in warning_call.kwargs["extra"]["missing_permissions"]
        assert "Add Reactions" in warning_call.kwargs["extra"]["missing_permissions"]
        assert "Create Public Threads" in warning_call.kwargs["extra"]["missing_permissions"]

    @pytest.mark.asyncio
    async def test_check_permissions_warns_when_only_thread_permission_missing(self, bot_instance):
        """Thread permission must be present before startup reports a healthy setup."""
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 123456
        mock_guild.me = MagicMock()
        mock_guild.me.guild_permissions = guild_permissions(create_public_threads=False)

        bot_instance.guilds = [mock_guild]

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._check_permissions()
            warning_call = mock_logger.warning.call_args

            assert warning_call.args[0] == "Guild missing permissions"
            assert "Create Public Threads" in warning_call.kwargs["extra"]["missing_permissions"]
            mock_logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_permissions_warns_when_only_thread_send_permission_missing(
        self, bot_instance
    ):
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 123456
        mock_guild.me = MagicMock()
        mock_guild.me.guild_permissions = guild_permissions(send_messages_in_threads=False)

        bot_instance.guilds = [mock_guild]

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._check_permissions()
            warning_call = mock_logger.warning.call_args

            assert warning_call.args[0] == "Guild missing permissions"
            assert "Send Messages in Threads" in warning_call.kwargs["extra"]["missing_permissions"]
            mock_logger.info.assert_not_called()


class TestGuildLifecycle:
    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.db.get_guild_config = AsyncMock(return_value=None)
            yield bot

    @pytest.mark.asyncio
    async def test_on_guild_join_logs_invite_and_setup_state(self, bot_instance):
        guild = MagicMock()
        guild.id = 123456
        guild.name = "New Guild"
        guild.member_count = 42
        guild.me = MagicMock()
        guild.me.guild_permissions = guild_permissions()

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_guild_join(guild)

        bot_instance.db.get_guild_config.assert_awaited_once_with("123456")
        info_call = mock_logger.info.call_args
        assert info_call.args[0] == "Joined guild"
        assert info_call.kwargs["extra"] == {
            "guild_id": 123456,
            "guild_name": "New Guild",
            "member_count": 42,
            "setup_configured": False,
        }
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_guild_join_logs_configured_state(self, bot_instance):
        bot_instance.db.get_guild_config.return_value = {"guild_id": "123456"}
        guild = MagicMock()
        guild.id = 123456
        guild.name = "Configured Guild"
        guild.member_count = 10
        guild.me = MagicMock()
        guild.me.guild_permissions = guild_permissions()

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_guild_join(guild)

        assert mock_logger.info.call_args.kwargs["extra"]["setup_configured"] is True

    @pytest.mark.asyncio
    async def test_on_guild_join_logs_even_when_setup_lookup_fails(self, bot_instance):
        bot_instance.db.get_guild_config.side_effect = RuntimeError("db unavailable")
        guild = MagicMock()
        guild.id = 123456
        guild.name = "New Guild"
        guild.member_count = 42
        guild.me = MagicMock()
        guild.me.guild_permissions = guild_permissions()

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_guild_join(guild)

        mock_logger.exception.assert_called_once()
        info_call = mock_logger.info.call_args
        assert info_call.args[0] == "Joined guild"
        assert info_call.kwargs["extra"]["setup_configured"] is None

    @pytest.mark.asyncio
    async def test_on_guild_join_logs_missing_permissions(self, bot_instance):
        guild = MagicMock()
        guild.id = 123456
        guild.name = "New Guild"
        guild.member_count = 42
        guild.me = MagicMock()
        guild.me.guild_permissions = guild_permissions(send_messages_in_threads=False)

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_guild_join(guild)

        assert mock_logger.warning.call_args.args[0] == "Guild missing permissions"

    @pytest.mark.asyncio
    async def test_on_guild_remove_logs_guild_metadata(self, bot_instance):
        guild = MagicMock()
        guild.id = 123456
        guild.name = "Old Guild"
        guild.member_count = 5

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_guild_remove(guild)

        info_call = mock_logger.info.call_args
        assert info_call.args[0] == "Removed from guild"
        assert info_call.kwargs["extra"] == {
            "guild_id": 123456,
            "guild_name": "Old Guild",
            "member_count": 5,
        }


class TestFixtureAnnouncementSync:
    @pytest.fixture
    def bot_instance(self):
        with (
            patch("typer_bot.bot.commands.Bot.__init__", return_value=None),
            patch.object(TyperBot, "guilds", []),
        ):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.get_channel = MagicMock()
            bot.get_guild = MagicMock()
            bot.fetch_channel = AsyncMock()
            yield bot

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_uses_stored_channel_id(self, bot_instance):
        fixture = {"id": 1, "guild_id": "111111", "message_id": "789012", "channel_id": "123456"}
        message = MagicMock()
        message.thread = MagicMock(id=789012)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])
        bot_instance.get_channel.return_value = channel

        await bot_instance._sync_fixture_thread()

        bot_instance.get_channel.assert_called_once_with(123456)
        channel.fetch_message.assert_awaited_once_with(789012)
        bot_instance.fetch_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_scans_owning_guild_channels_only_without_channel_id(
        self, bot_instance
    ):
        fixture = {"id": 1, "guild_id": "111111", "message_id": "789012", "channel_id": None}
        message = MagicMock()
        message.thread = MagicMock(id=789012)
        other_channel = MagicMock()
        other_channel.id = 50
        other_channel.fetch_message = AsyncMock(return_value=message)
        other_guild = MagicMock()
        other_guild.id = 222222
        other_guild.text_channels = [other_channel]
        miss_channel = MagicMock()
        miss_channel.id = 100
        miss_channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "missing"))
        hit_channel = MagicMock()
        hit_channel.id = 200
        hit_channel.fetch_message = AsyncMock(return_value=message)
        guild = MagicMock()
        guild.id = 111111
        guild.text_channels = [miss_channel, hit_channel]
        bot_instance.guilds = [other_guild, guild]
        bot_instance.get_guild.return_value = guild
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])

        await bot_instance._sync_fixture_thread()

        bot_instance.get_channel.assert_not_called()
        bot_instance.fetch_channel.assert_not_called()
        other_channel.fetch_message.assert_not_called()
        miss_channel.fetch_message.assert_awaited_once_with(789012)
        hit_channel.fetch_message.assert_awaited_once_with(789012)

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_does_not_scan_when_stored_channel_missing(
        self, bot_instance
    ):
        fixture = {"id": 1, "guild_id": "111111", "message_id": "789012", "channel_id": "123456"}
        guild = MagicMock()
        guild.text_channels = [MagicMock()]
        bot_instance.guilds = [guild]
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])
        bot_instance.get_channel.return_value = None
        bot_instance.fetch_channel.side_effect = discord.NotFound(MagicMock(), "missing")

        await bot_instance._sync_fixture_thread()

        guild.text_channels[0].fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_does_not_scan_when_stored_channel_message_missing(
        self, bot_instance
    ):
        fixture = {"id": 1, "guild_id": "111111", "message_id": "789012", "channel_id": "123456"}
        channel = MagicMock()
        channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "missing"))
        guild = MagicMock()
        guild.text_channels = [MagicMock()]
        bot_instance.guilds = [guild]
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])
        bot_instance.get_channel.return_value = channel

        await bot_instance._sync_fixture_thread()

        channel.fetch_message.assert_awaited_once_with(789012)
        guild.text_channels[0].fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_fetches_stored_channel_when_not_cached(self, bot_instance):
        fixture = {"id": 1, "guild_id": "111111", "message_id": "789012", "channel_id": "123456"}
        message = MagicMock()
        message.thread = MagicMock(id=789012)
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])
        bot_instance.get_channel.return_value = None
        bot_instance.fetch_channel.return_value = channel

        await bot_instance._sync_fixture_thread()

        bot_instance.fetch_channel.assert_awaited_once_with(123456)
        channel.fetch_message.assert_awaited_once_with(789012)

    @pytest.mark.asyncio
    async def test_sync_fixture_thread_does_not_scan_with_invalid_stored_ids(self, bot_instance):
        fixture = {
            "id": 1,
            "guild_id": "111111",
            "message_id": "not-a-message",
            "channel_id": "not-a-channel",
        }
        guild = MagicMock()
        guild.text_channels = [MagicMock()]
        bot_instance.guilds = [guild]
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])

        await bot_instance._sync_fixture_thread()

        bot_instance.get_channel.assert_not_called()
        bot_instance.fetch_channel.assert_not_called()
        guild.text_channels[0].fetch_message.assert_not_called()


class TestReminderSystem:
    """Test suite for reminder scheduling."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.send_reminder = AsyncMock()
            yield bot

    @pytest.mark.asyncio
    @patch("typer_bot.bot.now")
    async def test_reminder_24h_triggered_at_correct_time(self, mock_now, bot_instance):
        """24-hour reminder triggers at correct time."""
        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        current_time = deadline - timedelta(hours=24)
        mock_now.return_value = current_time

        fixture = {
            "id": 1,
            "guild_id": "111111",
            "deadline": deadline,
            "week_number": 1,
        }
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_called_once_with(fixture, "24 hours remaining")

    @pytest.mark.asyncio
    @patch("typer_bot.bot.now")
    async def test_reminder_1h_triggered_at_correct_time(self, mock_now, bot_instance):
        """1-hour reminder triggers at correct time."""
        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        current_time = deadline - timedelta(hours=1)
        mock_now.return_value = current_time

        fixture = {
            "id": 1,
            "guild_id": "111111",
            "deadline": deadline,
            "week_number": 1,
        }
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_called_once_with(fixture, "1 hour remaining")

    @pytest.mark.asyncio
    async def test_reminder_sent_at_exact_time(self, bot_instance):
        """Minute-precision triggering prevents duplicate reminders."""
        from freezegun import freeze_time

        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        current_time = deadline - timedelta(hours=24)

        fixture = {
            "id": 1,
            "guild_id": "111111",
            "deadline": deadline,
            "week_number": 1,
        }
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture])

        with freeze_time(current_time):
            await bot_instance.reminder_task()
            assert bot_instance.send_reminder.call_count == 1

        with freeze_time(current_time + timedelta(minutes=1)):
            await bot_instance.reminder_task()
            assert bot_instance.send_reminder.call_count == 1

    @pytest.mark.asyncio
    @patch("typer_bot.bot.now")
    async def test_reminder_skips_if_no_fixture(self, mock_now, bot_instance):
        """Reminders are skipped when no fixture is active."""
        mock_now.return_value = datetime.now(UTC)
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[])

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_not_called()

    @pytest.mark.asyncio
    @patch("typer_bot.bot.now")
    async def test_reminder_checks_all_open_fixtures(self, mock_now, bot_instance):
        """Reminder loop evaluates every concurrently open fixture."""
        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        mock_now.return_value = deadline - timedelta(hours=24)
        fixture_a = {"id": 1, "guild_id": "111111", "deadline": deadline, "week_number": 1}
        fixture_b = {"id": 2, "guild_id": "222222", "deadline": deadline, "week_number": 2}
        bot_instance.db.get_all_open_fixtures = AsyncMock(return_value=[fixture_a, fixture_b])

        await bot_instance.reminder_task()

        assert bot_instance.send_reminder.await_count == 2
        bot_instance.send_reminder.assert_any_await(fixture_a, "24 hours remaining")
        bot_instance.send_reminder.assert_any_await(fixture_b, "24 hours remaining")


class TestSendReminder:
    """Test suite for send_reminder method."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.get_channel = MagicMock()
            bot.fetch_channel = AsyncMock()
            yield bot

    @pytest.mark.asyncio
    async def test_send_reminder_to_configured_channel(self, bot_instance):
        """Reminders route to the configured channel."""
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        bot_instance.get_channel.return_value = mock_channel
        bot_instance.db.get_guild_config = AsyncMock(return_value={"league_channel_id": "123456"})

        fixture = {
            "id": 1,
            "guild_id": "111111",
            "deadline": datetime.now(UTC) + timedelta(days=1),
            "week_number": 1,
        }

        await bot_instance.send_reminder(fixture, "24 hours remaining")

        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert "24 hours remaining" in call_args
        assert "/predict" in call_args

    @pytest.mark.asyncio
    async def test_send_reminder_routes_each_guild_to_its_configured_channel(self, bot_instance):
        channel_one = MagicMock()
        channel_one.send = AsyncMock()
        channel_two = MagicMock()
        channel_two.send = AsyncMock()
        configs = {
            "111111": {"league_channel_id": "123456"},
            "222222": {"league_channel_id": "234567"},
        }
        channels = {123456: channel_one, 234567: channel_two}
        bot_instance.db.get_guild_config = AsyncMock(side_effect=lambda guild_id: configs[guild_id])
        bot_instance.get_channel.side_effect = lambda channel_id: channels[channel_id]
        deadline = datetime.now(UTC) + timedelta(days=1)

        await bot_instance.send_reminder(
            {"id": 1, "guild_id": "111111", "deadline": deadline, "week_number": 1},
            "24 hours remaining",
        )
        await bot_instance.send_reminder(
            {"id": 2, "guild_id": "222222", "deadline": deadline, "week_number": 1},
            "24 hours remaining",
        )

        channel_one.send.assert_awaited_once()
        channel_two.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_reminder_missing_guild_config(self, bot_instance):
        """Missing channel configuration skips delivery."""
        bot_instance.db.get_guild_config = AsyncMock(return_value=None)
        fixture = {"id": 1, "guild_id": "111111", "deadline": datetime.now(UTC), "week_number": 1}
        await bot_instance.send_reminder(fixture, "24 hours remaining")

        bot_instance.get_channel.assert_not_called()


class TestMainFunction:
    """Test suite for main entry point."""

    @patch.dict(os.environ, {}, clear=True)
    def test_main_exits_without_token(self):
        """Exiting without a token provides clear failure signal."""
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch.dict(os.environ, {"DISCORD_TOKEN": "your_bot_token_here"})
    def test_main_exits_with_placeholder_token(self):
        """Placeholder token detection prevents accidental deployment."""
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch.dict(os.environ, {"DISCORD_TOKEN": "valid_token", "ENVIRONMENT": "development"})
    @patch("typer_bot.bot.TyperBot")
    def test_main_runs_bot_in_non_production_environment(self, mock_bot_cls):
        """Non-production environments still connect to Discord."""
        mock_bot = mock_bot_cls.return_value

        main()

        mock_bot.run.assert_called_once_with("valid_token", log_handler=None)

    @patch.dict(os.environ, {"DISCORD_TOKEN": "valid_token"}, clear=True)
    @patch("typer_bot.bot.TyperBot")
    def test_main_runs_bot_when_environment_is_unset(self, mock_bot_cls):
        """Missing ENVIRONMENT still boots with the default non-production label."""
        mock_bot = mock_bot_cls.return_value

        main()

        mock_bot.run.assert_called_once_with("valid_token", log_handler=None)

    @patch.dict(os.environ, {"DISCORD_TOKEN": "valid_token", "ENVIRONMENT": "production"})
    @patch("typer_bot.bot.TyperBot")
    def test_main_runs_bot_in_production_environment(self, mock_bot_cls):
        """Production environment uses the production label and still boots normally."""
        mock_bot = mock_bot_cls.return_value

        main()

        mock_bot.run.assert_called_once_with("valid_token", log_handler=None)

    @patch.dict(os.environ, {"DISCORD_TOKEN": "valid_token", "ENVIRONMENT": "production"})
    @patch("typer_bot.bot.TyperBot")
    @patch("typer_bot.bot.logger")
    def test_main_logs_clear_error_for_missing_privileged_intents(self, mock_logger, mock_bot_cls):
        """Privileged intent failures should point to the developer portal setting."""
        mock_bot = mock_bot_cls.return_value
        mock_bot.run.side_effect = discord.PrivilegedIntentsRequired(shard_id=None)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "Privileged intents" in mock_logger.exception.call_args.args[0]


class TestOnMessage:
    """Test suite for on_message event handler."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.thread_handler = MagicMock()
            bot.thread_handler.on_message = AsyncMock(return_value=False)
            yield bot

    @pytest.mark.asyncio
    async def test_on_message_ignores_bots(self, bot_instance):
        """Bot messages are ignored to prevent response loops."""
        mock_message = MagicMock()
        mock_message.author.bot = True

        with (
            patch("typer_bot.bot.set_trace_id") as mock_set_trace,
            patch.object(bot_instance, "process_commands"),
        ):
            await bot_instance.on_message(mock_message)
            mock_set_trace.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_sets_trace_id(self, bot_instance):
        """Trace ID assignment enables request correlation across logs."""
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.id = 123456

        with (
            patch("typer_bot.bot.set_trace_id") as mock_set_trace,
            patch.object(bot_instance, "process_commands"),
        ):
            await bot_instance.on_message(mock_message)
            mock_set_trace.assert_called_once_with("msg-123456")


class TestOnMessageRouting:
    """Test suite verifying non-thread messages use the normal command pipeline."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.thread_handler = MagicMock()
            bot.thread_handler.on_message = AsyncMock(return_value=False)
            yield bot

    @pytest.mark.asyncio
    async def test_dm_messages_fall_through_to_command_pipeline(self, bot_instance):
        """DMs are no longer specially routed after DM workflow removal."""
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.guild = None
        mock_message.id = 1

        with patch("discord.ext.commands.Bot.on_message", new_callable=AsyncMock) as mock_super:
            await bot_instance.on_message(mock_message)

        mock_super.assert_awaited_once_with(mock_message)

    @pytest.mark.asyncio
    async def test_guild_messages_use_command_pipeline(self, bot_instance):
        """Guild messages still go through normal command processing."""
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.guild = MagicMock()
        mock_message.id = 2

        with patch("discord.ext.commands.Bot.on_message", new_callable=AsyncMock) as mock_super:
            await bot_instance.on_message(mock_message)

        mock_super.assert_awaited_once_with(mock_message)

    @pytest.mark.asyncio
    async def test_thread_handler_takes_priority_over_command_pipeline(self, bot_instance):
        """Handled thread predictions short-circuit the normal command pipeline."""
        bot_instance.thread_handler.on_message = AsyncMock(return_value=True)
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.guild = MagicMock()
        mock_message.id = 4

        with patch("discord.ext.commands.Bot.on_message", new_callable=AsyncMock) as mock_super:
            await bot_instance.on_message(mock_message)

        mock_super.assert_not_awaited()


class TestOnInteraction:
    """Test suite for on_interaction event handler."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            return TyperBot.__new__(TyperBot)

    @pytest.mark.asyncio
    async def test_on_interaction_sets_trace_id(self, bot_instance):
        """Trace ID on slash commands enables workflow tracking."""
        mock_interaction = MagicMock()
        mock_interaction.id = 123456

        with patch("typer_bot.bot.set_trace_id") as mock_set_trace:
            await bot_instance.on_interaction(mock_interaction)
            mock_set_trace.assert_called_once_with("req-123456")
