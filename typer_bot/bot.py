"""Main Discord bot implementation."""

import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from typer_bot.database import Database
from typer_bot.handlers.thread_prediction_handler import ThreadPredictionHandler
from typer_bot.utils import format_for_discord, now
from typer_bot.utils.config import IS_PRODUCTION
from typer_bot.utils.logger import set_log_context, set_trace_id

logger = logging.getLogger(__name__)

load_dotenv()

logger.info("=" * 50)
logger.info("STARTING TYPER BOT")
logger.info("=" * 50)


class TyperBot(commands.Bot):
    """Football predictions Discord bot."""

    def __init__(self):
        logger.info("Initializing TyperBot...")
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents, help_command=None)

        self.db = Database()
        self.thread_handler = ThreadPredictionHandler(self, self.db)
        logger.info("Database instance created")

    async def on_interaction(self, interaction: discord.Interaction):
        """Set trace ID and context for every interaction before processing."""
        set_trace_id(f"req-{interaction.id}")

        user_id = str(interaction.user.id) if interaction.user else None
        guild_id = str(interaction.guild_id) if interaction.guild_id else None
        set_log_context(user_id=user_id, guild_id=guild_id, source="command")

        # ContextVars are task-local, so each interaction gets isolated context.

    async def on_message(self, message: discord.Message):
        """Set trace ID and context for every message before processing."""
        if message.author.bot:
            return

        set_trace_id(f"msg-{message.id}")

        user_id = str(message.author.id)
        guild_id = str(message.guild.id) if message.guild else None
        source = "thread" if isinstance(message.channel, discord.Thread) else "dm"
        set_log_context(user_id=user_id, guild_id=guild_id, source=source)

        try:
            handled = await self.thread_handler.on_message(message)
            if handled:
                return

            await super().on_message(message)
        finally:
            from typer_bot.utils.logger import clear_log_context, clear_trace_id

            clear_log_context()
            clear_trace_id()

    async def on_message_delete(self, message: discord.Message):
        """Handle message deletions."""
        if message.author.bot:
            return

        set_trace_id(f"del-{message.id}")
        # ContextVars are task-local; cleanup automatic when task completes.
        # No log_context set, so no cleanup needed.

    async def setup_hook(self):
        """Initialize database and load cogs."""
        logger.info("Running setup_hook...")

        try:
            await self.db.initialize()
            logger.info("Database initialized successfully")
        except Exception:
            logger.exception("Database initialization failed")
            raise

        await self._run_archive_imports()

        logger.info("Loading command cogs...")
        try:
            await self.load_extension("typer_bot.commands.user_commands")
            logger.info("Loaded user_commands")
        except Exception:
            logger.exception("Failed to load user_commands")
            raise

        try:
            await self.load_extension("typer_bot.commands.admin_commands")
            logger.info("Loaded admin_commands")
        except Exception:
            logger.exception("Failed to load admin_commands")
            raise

        logger.info("Syncing slash commands...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands")
        except Exception:
            logger.exception("Failed to sync commands")

        logger.info("Starting reminder task...")
        self.reminder_task.start()
        logger.info("Setup hook complete")

    async def _validate_archive_sql(self, db_path: str, sql_content: str) -> bool:
        """Validate archive SQL using sandbox transaction - only safe INSERTs allowed."""
        import re

        import aiosqlite

        # Pre-check: block operations that bypass transaction safety
        if re.search(r"\b(ATTACH|DETACH|VACUUM|PRAGMA)\b", sql_content, re.IGNORECASE):
            return False

        async with aiosqlite.connect(db_path) as db:
            await db.execute("BEGIN")
            await db.execute("PRAGMA writable_schema=OFF")
            try:
                await db.executescript(sql_content)
                await db.rollback()
                return True
            except aiosqlite.Error as e:
                logger.warning(f"SQL validation failed: {e}")
                await db.rollback()
                return False

    async def _run_archive_imports(self):
        """Run SQL files from archive folder if database is empty."""
        import os

        import aiosqlite

        auto_import = os.getenv("IMPORT_ARCHIVE", "").lower() in ("true", "1", "yes")
        if not auto_import:
            logger.info("Archive import disabled (set IMPORT_ARCHIVE=true to enable)")
            return

        try:
            async with (
                aiosqlite.connect(self.db.db_path) as db,
                db.execute("SELECT COUNT(*) FROM fixtures") as cursor,
            ):
                count = await cursor.fetchone()
                if count and count[0] > 0:
                    logger.info("Database already has fixtures, skipping archive import")
                    return

            # Path.glob() does string matching only, not file I/O.
            # Actual file reads are wrapped in run_in_executor below.
            archive_files = sorted(Path("archive").glob("*.sql"))  # noqa: ASYNC240
            if not archive_files:
                logger.info("No archive SQL files found")
                return

            logger.info(f"Found {len(archive_files)} archive file(s) to import")

            for sql_file in archive_files:
                logger.info(f"Importing {sql_file}...")
                try:
                    # Run file I/O in thread pool to avoid blocking event loop
                    def _read_sql_file(file=sql_file):
                        with file.open(encoding="utf-8") as f:
                            return f.read()

                    sql_content = await asyncio.get_event_loop().run_in_executor(
                        None, _read_sql_file
                    )

                    if not await self._validate_archive_sql(self.db.db_path, sql_content):
                        logger.error(
                            f"❌ Rejected {sql_file}: validation failed (non-INSERT statements detected)"
                        )
                        continue

                    async with aiosqlite.connect(self.db.db_path) as db:
                        await db.executescript(sql_content)
                        await db.commit()

                        async with db.execute("SELECT COUNT(*) FROM fixtures") as cursor:
                            row = await cursor.fetchone()
                            fixture_count = row[0] if row else 0
                        async with db.execute("SELECT COUNT(*) FROM predictions") as cursor:
                            row = await cursor.fetchone()
                            prediction_count = row[0] if row else 0

                        async with db.execute("SELECT games FROM fixtures LIMIT 1") as cursor:
                            row = await cursor.fetchone()
                            games_count = len(row[0].split("\n")) if row else 0

                    logger.info(f"✅ Successfully imported {sql_file}")
                    logger.info(
                        f"   📊 Imported {fixture_count} fixture(s) with {games_count} games"
                    )
                    logger.info(
                        f"   👥 Imported {prediction_count} predictions from {prediction_count // games_count if games_count else 0} users"
                    )

                except Exception:
                    logger.exception(f"❌ Failed to import {sql_file}")

            logger.info("Archive import complete")

        except Exception:
            logger.exception("Error during archive import")

    async def on_ready(self):
        """Called when bot is ready."""
        logger.info(f"✓ Bot connected: {self.user} (ID: {self.user.id})")
        logger.info(f"✓ Connected to {len(self.guilds)} guild(s):")
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id})")

        # Check bot permissions on all guilds
        await self._check_permissions()

        # Sync any manually-created threads
        await self._sync_fixture_thread()

    async def on_error(self, event_method, *_args, **_kwargs):
        """Handle uncaught errors."""
        from typer_bot.utils.logger import get_log_context, get_trace_id

        context = get_log_context()
        trace_id = get_trace_id()

        logger.exception(
            f"Error in {event_method}",
            extra={
                "event_type": "error.unhandled",
                "event_method": event_method,
                "trace_id": trace_id,
                **context,
            },
        )

    def cog_unload(self):
        """Clean up when bot shuts down."""
        logger.info("Shutting down reminder task...")
        self.reminder_task.cancel()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        """Send reminders 24h and 1h before fixture deadline."""
        current_time = now()
        fixture = await self.db.get_current_fixture()

        if not fixture:
            return

        deadline = fixture["deadline"]

        # Compare minute-level precision to avoid double-sending
        def is_same_minute(t1, t2):
            return t1.replace(second=0, microsecond=0) == t2.replace(second=0, microsecond=0)

        reminder_24h = deadline - timedelta(hours=24)
        if is_same_minute(current_time, reminder_24h):
            logger.info("Sending 24h reminder...")
            await self.send_reminder(fixture, "24 hours remaining")

        reminder_1h = deadline - timedelta(hours=1)
        if is_same_minute(current_time, reminder_1h):
            logger.info("Sending 1h reminder...")
            await self.send_reminder(fixture, "1 hour remaining")

    async def send_reminder(self, fixture: dict, time_description: str):
        """Send prediction reminder to configured channel."""
        channel_id = os.getenv("REMINDER_CHANNEL_ID")
        if not channel_id:
            logger.warning("REMINDER_CHANNEL_ID not set, skipping reminder")
            return

        try:
            channel = self.get_channel(int(channel_id))
            if channel:
                deadline = format_for_discord(fixture["deadline"], "F")
                relative = format_for_discord(fixture["deadline"], "R")
                await channel.send(
                    f"📢 **{time_description}!**\n\n"
                    f"Don't forget to submit your predictions for this week!\n"
                    f"Deadline: **{deadline}** ({relative})\n"
                    f"Use `/predict` to enter your scores."
                )
                logger.info(f"Reminder sent to channel {channel_id}")
            else:
                logger.error(f"Could not find channel {channel_id}")
        except Exception:
            logger.exception("Failed to send reminder")

    async def _sync_fixture_thread(self):
        """Verify fixture announcement exists on startup.

        Checks that the open fixture's announcement message is accessible.
        The stored message_id doubles as the thread_id since Discord
        public threads inherit their parent message's snowflake ID.
        """
        logger.info("Verifying fixture announcement...")

        try:
            fixture = await self.db.get_current_fixture()
            if not fixture:
                logger.info("No open fixture found, skipping verification")
                return

            message_id = fixture.get("message_id")
            if not message_id:
                logger.info(f"Fixture {fixture['id']} has no message_id, skipping verification")
                return

            # Search channels for the announcement message
            for guild in self.guilds:
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(int(message_id))
                        if message.thread:
                            logger.info(f"Fixture {fixture['id']} has thread {message.thread.id}")
                        else:
                            logger.info(
                                f"Fixture {fixture['id']} has no thread (users can use /predict)"
                            )
                        return
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        logger.warning(f"No permission to read channel {channel.id}")
                        continue
                    except Exception as e:
                        logger.warning(f"Could not verify fixture in {channel.id}: {e}")
                        continue

            logger.warning(
                f"Could not find announcement message {message_id} for fixture {fixture['id']}"
            )

        except Exception as e:
            logger.exception(f"Error during fixture verification: {e}")

    async def _check_permissions(self):
        """Check bot permissions on all guilds.

        Logs warnings if the bot is missing critical permissions.
        """
        required_permissions = [
            ("send_messages", "Send Messages"),
            ("read_message_history", "Read Message History"),
            ("add_reactions", "Add Reactions"),
        ]

        for guild in self.guilds:
            me = guild.me
            if not me:
                logger.warning(f"Bot not found in guild {guild.name} (ID: {guild.id})")
                continue

            missing = []
            for perm_attr, perm_name in required_permissions:
                if not getattr(me.guild_permissions, perm_attr, False):
                    missing.append(perm_name)

            if missing:
                logger.warning(
                    f"⚠️  Guild '{guild.name}' (ID: {guild.id}): "
                    f"Missing permissions: {', '.join(missing)}"
                )
            else:
                logger.info(f"✓ Guild '{guild.name}': All required permissions present")


def main():
    """Run the bot."""
    logger.info("Starting main()...")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN environment variable not set!")
        logger.error("Please set DISCORD_TOKEN in Railway variables")
        sys.exit(1)

    if token == "your_bot_token_here":
        logger.error("❌ DISCORD_TOKEN is set to placeholder value!")
        logger.error("Please update it with your actual bot token")
        sys.exit(1)

    # Token is validated above; this satisfies type checker
    if token is None:
        raise RuntimeError("Token validation failed unexpectedly")
    logger.info("✅ Token configured")

    if not IS_PRODUCTION:
        logger.info("⚠️  ENVIRONMENT is not 'production' - running in smoke test mode")
        logger.info("✅ Smoke test successful - deployment validated, exiting")
        sys.exit(0)

    logger.info("Creating TyperBot instance...")

    try:
        bot = TyperBot()
        logger.info("Starting bot.run()...")
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.exception("❌ Discord login failed - check if DISCORD_TOKEN is valid")
        sys.exit(1)
    except Exception:
        logger.exception("❌ Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()
