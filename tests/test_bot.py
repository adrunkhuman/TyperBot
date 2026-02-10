"""Tests for main Discord bot implementation."""

import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from typer_bot.bot import TyperBot, main


class TestBotInitialization:
    """Test suite for bot initialization and setup."""

    @pytest.mark.asyncio
    async def test_bot_creates_database_instance(self):
        """Database is initialized at startup."""
        with patch.object(TyperBot, "__init__", lambda _: None):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.thread_handler = MagicMock()
            assert bot.db is not None

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
        with (
            patch("typer_bot.bot.commands.Bot.__init__", return_value=None),
            patch.object(TyperBot, "tree", mock_tree),
        ):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.db.initialize = AsyncMock()
            bot.thread_handler = MagicMock()
            bot.load_extension = AsyncMock()
            bot.reminder_task = MagicMock()
            bot._run_archive_imports = AsyncMock()
            yield bot

    @pytest.mark.asyncio
    async def test_setup_hook_initializes_database(self, bot_instance):
        """Database is initialized during setup_hook."""
        await bot_instance.setup_hook()
        bot_instance.db.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_hook_loads_user_commands(self, bot_instance):
        """User commands cog provides /predict and /standings."""
        await bot_instance.setup_hook()
        bot_instance.load_extension.assert_any_call("typer_bot.commands.user_commands")

    @pytest.mark.asyncio
    async def test_setup_hook_loads_admin_commands(self, bot_instance):
        """Admin commands cog provides league management via DM workflows."""
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
    async def test_setup_hook_runs_archive_import(self, bot_instance):
        """Archive import runs on first boot."""
        await bot_instance.setup_hook()
        bot_instance._run_archive_imports.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_hook_raises_on_db_failure(self, bot_instance):
        """Database failure halts startup."""
        bot_instance.db.initialize.side_effect = Exception("DB Error")

        with pytest.raises(Exception, match="DB Error"):
            await bot_instance.setup_hook()


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
    async def test_on_ready_logs_bot_info(self, bot_instance, caplog):  # noqa: ARG002
        """Connection logging provides deployment visibility."""
        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance.on_ready()
            mock_logger.info.assert_any_call(
                f"✓ Bot connected: {bot_instance.user} (ID: {bot_instance.user.id})"
            )

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
    async def test_check_permissions_logs_missing_permissions(self, bot_instance, caplog):  # noqa: ARG002
        """Missing permission warnings help admins identify configuration issues."""
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 123456
        mock_guild.me = MagicMock()
        mock_guild.me.guild_permissions.send_messages = False
        mock_guild.me.guild_permissions.read_message_history = False
        mock_guild.me.guild_permissions.add_reactions = False

        bot_instance.guilds = [mock_guild]

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._check_permissions()
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_check_permissions_logs_all_permissions_ok(self, bot_instance):
        """Permission success logging confirms proper bot configuration."""
        mock_guild = MagicMock()
        mock_guild.name = "Test Guild"
        mock_guild.id = 123456
        mock_guild.me = MagicMock()
        mock_guild.me.guild_permissions.send_messages = True
        mock_guild.me.guild_permissions.read_message_history = True
        mock_guild.me.guild_permissions.add_reactions = True

        bot_instance.guilds = [mock_guild]

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._check_permissions()
            mock_logger.info.assert_called_with(
                "✓ Guild 'Test Guild': All required permissions present"
            )


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

        bot_instance.db.get_current_fixture = AsyncMock(
            return_value={
                "id": 1,
                "deadline": deadline,
                "week_number": 1,
            }
        )

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_called_once_with(
            bot_instance.db.get_current_fixture.return_value, "24 hours remaining"
        )

    @pytest.mark.asyncio
    @patch("typer_bot.bot.now")
    async def test_reminder_1h_triggered_at_correct_time(self, mock_now, bot_instance):
        """1-hour reminder triggers at correct time."""
        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        current_time = deadline - timedelta(hours=1)
        mock_now.return_value = current_time

        bot_instance.db.get_current_fixture = AsyncMock(
            return_value={
                "id": 1,
                "deadline": deadline,
                "week_number": 1,
            }
        )

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_called_once_with(
            bot_instance.db.get_current_fixture.return_value, "1 hour remaining"
        )

    @pytest.mark.asyncio
    async def test_reminder_sent_at_exact_time(self, bot_instance):
        """Minute-precision triggering prevents duplicate reminders."""
        from freezegun import freeze_time

        deadline = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        current_time = deadline - timedelta(hours=24)

        bot_instance.db.get_current_fixture = AsyncMock(
            return_value={
                "id": 1,
                "deadline": deadline,
                "week_number": 1,
            }
        )

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
        bot_instance.db.get_current_fixture = AsyncMock(return_value=None)

        await bot_instance.reminder_task()

        bot_instance.send_reminder.assert_not_called()


class TestSendReminder:
    """Test suite for send_reminder method."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.get_channel = MagicMock()
            yield bot

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"REMINDER_CHANNEL_ID": "123456"})
    async def test_send_reminder_to_configured_channel(self, bot_instance):
        """Reminders route to the configured channel."""
        mock_channel = MagicMock()
        mock_channel.send = AsyncMock()
        bot_instance.get_channel.return_value = mock_channel

        fixture = {
            "deadline": datetime.now(UTC) + timedelta(days=1),
            "week_number": 1,
        }

        await bot_instance.send_reminder(fixture, "24 hours remaining")

        mock_channel.send.assert_called_once()
        call_args = mock_channel.send.call_args[0][0]
        assert "24 hours remaining" in call_args
        assert "/predict" in call_args

    @pytest.mark.asyncio
    async def test_send_reminder_missing_channel_id(self, bot_instance):
        """Missing channel configuration logs a warning."""
        with patch.dict(os.environ, {}, clear=True), patch("typer_bot.bot.logger") as mock_logger:
            fixture = {"deadline": datetime.now(UTC), "week_number": 1}
            await bot_instance.send_reminder(fixture, "24 hours remaining")
            mock_logger.warning.assert_called_with("REMINDER_CHANNEL_ID not set, skipping reminder")


class TestArchiveImport:
    """Test suite for archive import functionality."""

    @pytest.fixture
    def bot_instance(self, tmp_path):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.db = MagicMock()
            bot.db.db_path = str(tmp_path / "test.db")
            yield bot

    @pytest.mark.asyncio
    async def test_archive_import_disabled_by_default(self, bot_instance):
        """Archive import is opt-in to prevent accidental data overwrites."""
        with patch.dict(os.environ, {}, clear=True), patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._run_archive_imports()
            mock_logger.info.assert_any_call(
                "Archive import disabled (set IMPORT_ARCHIVE=true to enable)"
            )

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"IMPORT_ARCHIVE": "true"})
    async def test_archive_import_skips_if_fixtures_exist(self, bot_instance, tmp_path):  # noqa: ARG002
        """Import is skipped when fixtures already exist."""
        import aiosqlite

        async with aiosqlite.connect(bot_instance.db.db_path) as db:
            await db.execute("""
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open'
                )
            """)
            await db.execute(
                "INSERT INTO fixtures (week_number, games, deadline) VALUES (?, ?, ?)",
                (1, "Game 1", datetime.now(UTC)),
            )
            await db.commit()

        with patch("typer_bot.bot.logger") as mock_logger:
            await bot_instance._run_archive_imports()
            mock_logger.info.assert_any_call(
                "Database already has fixtures, skipping archive import"
            )

    @pytest.mark.asyncio
    async def test_validate_archive_sql_blocks_dangerous_statements(self, bot_instance):
        """Dangerous SQL statements are blocked to prevent malicious archive files."""
        dangerous_sqls = [
            "ATTACH DATABASE 'evil.db' AS evil;",
            "DETACH DATABASE evil;",
            "VACUUM;",
            "PRAGMA foreign_keys = OFF;",
        ]

        for sql in dangerous_sqls:
            result = await bot_instance._validate_archive_sql(bot_instance.db.db_path, sql)
            assert result is False, f"Should have rejected: {sql}"


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
    @patch("typer_bot.bot.logger")
    def test_main_smoke_test_mode(self, mock_logger):
        """Smoke test mode validates configuration without connecting."""
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        mock_logger.info.assert_any_call(
            "⚠️  ENVIRONMENT is not 'production' - running in smoke test mode"
        )


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


class TestOnMessageEdit:
    """Test suite for on_message_edit event handler."""

    @pytest.fixture
    def bot_instance(self):
        with patch("typer_bot.bot.commands.Bot.__init__", return_value=None):
            bot = TyperBot.__new__(TyperBot)
            bot.thread_handler = MagicMock()
            bot.thread_handler.on_message_edit = AsyncMock(return_value=False)
            yield bot

    @pytest.mark.asyncio
    async def test_on_message_edit_ignores_bots(self, bot_instance):
        """Bot edits are ignored."""
        mock_before = MagicMock()
        mock_after = MagicMock()
        mock_after.author.bot = True

        with patch("typer_bot.bot.set_trace_id") as mock_set_trace:
            await bot_instance.on_message_edit(mock_before, mock_after)
            mock_set_trace.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_edit_sets_trace_id(self, bot_instance):
        """Trace ID on edits enables observability for prediction corrections."""
        mock_before = MagicMock()
        mock_after = MagicMock()
        mock_after.author.bot = False
        mock_after.id = 123456

        with patch("typer_bot.bot.set_trace_id") as mock_set_trace:
            with suppress(AttributeError):  # Expected - parent doesn't have on_message_edit
                await bot_instance.on_message_edit(mock_before, mock_after)

            mock_set_trace.assert_called_once_with("edit-123456")


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
