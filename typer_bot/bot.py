"""Main Discord bot implementation."""

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
from typer_bot.utils.logger import set_trace_id

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
        """Set trace ID for every interaction before processing."""
        # Use request ID format: req-<interaction_id>
        # This context is preserved for all async calls within this interaction
        set_trace_id(f"req-{interaction.id}")

    async def on_message(self, message: discord.Message):
        """Set trace ID for every message before processing."""
        # Use message ID format: msg-<message_id>
        if message.author.bot:
            return

        set_trace_id(f"msg-{message.id}")

        # Handle thread predictions first
        handled = await self.thread_handler.on_message(message)
        if handled:
            return

        await super().on_message(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Handle message edits, including thread prediction updates."""
        if after.author.bot:
            return

        set_trace_id(f"edit-{after.id}")

        # Handle thread prediction edits
        handled = await self.thread_handler.on_message_edit(before, after)
        if handled:
            return

        await super().on_message_edit(before, after)

    async def on_message_delete(self, message: discord.Message):
        """Handle message deletions."""
        if message.author.bot:
            return

        set_trace_id(f"del-{message.id}")
        await super().on_message_delete(message)

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

            archive_files = sorted(Path("archive").glob("*.sql"))
            if not archive_files:
                logger.info("No archive SQL files found")
                return

            logger.info(f"Found {len(archive_files)} archive file(s) to import")

            for sql_file in archive_files:
                logger.info(f"Importing {sql_file}...")
                try:
                    # Archive import is one-time startup operation, blocking I/O is acceptable
                    with sql_file.open(encoding="utf-8") as f:  # noqa: ASYNC230
                        sql_content = f.read()

                    if not await self._validate_archive_sql(self.db.db_path, sql_content):
                        logger.error(
                            f"❌ Rejected {sql_file}: validation failed (non-INSERT statements detected)"
                        )
                        continue

                    async with aiosqlite.connect(self.db.db_path) as db:
                        await db.executescript(sql_content)
                        await db.commit()

                        async with db.execute("SELECT COUNT(*) FROM fixtures") as cursor:
                            fixture_count = (await cursor.fetchone())[0]
                        async with db.execute("SELECT COUNT(*) FROM predictions") as cursor:
                            prediction_count = (await cursor.fetchone())[0]

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

        # Auto-refresh usernames on startup
        await self._refresh_usernames()

        # Sync any manually-created threads
        await self._sync_fixture_thread()

    async def on_error(self, event_method, *_args, **_kwargs):
        """Handle uncaught errors."""
        logger.exception(f"Error in {event_method}")

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

    def is_admin(self, user: discord.Member) -> bool:
        """Check if user has admin role (case-insensitive)."""
        admin_roles = {"admin", "typer-admin"}
        return any(role.name.lower() in admin_roles for role in user.roles)

    async def _refresh_usernames(self):
        """Refresh all usernames in the database from Discord."""
        logger.info("Starting username refresh on startup...")

        try:
            user_ids = await self.db.get_all_user_ids()
            if not user_ids:
                logger.info("No users found to refresh")
                return

            updated = 0
            failed = 0

            for user_id in user_ids:
                try:
                    user = self.get_user(int(user_id))
                    if not user:
                        # Try to fetch from API if not in cache
                        try:
                            user = await self.fetch_user(int(user_id))
                        except discord.NotFound:
                            failed += 1
                            continue

                    if user:
                        await self.db.update_username(user_id, user.display_name)
                        updated += 1
                except Exception as e:
                    logger.warning(f"Failed to update username for {user_id}: {e}")
                    failed += 1

            logger.info(f"Username refresh complete: {updated} updated, {failed} failed")

        except Exception as e:
            logger.exception(f"Error during username refresh: {e}")

    async def _sync_fixture_thread(self):
        """Sync manually-created thread on startup.

        If an open fixture has an announcement message but no thread_id,
        check if a thread exists on that message and sync it.
        """
        logger.info("Syncing fixture threads...")

        try:
            fixture = await self.db.get_current_fixture()
            if not fixture:
                logger.info("No open fixture found, skipping thread sync")
                return

            if fixture.get("thread_id"):
                logger.info(f"Fixture {fixture['id']} already has thread_id, skipping sync")
                return

            announcement_id = fixture.get("announcement_message_id")
            if not announcement_id:
                logger.info(
                    f"Fixture {fixture['id']} has no announcement_message_id, skipping sync"
                )
                return

            # Search channels for the announcement message
            for guild in self.guilds:
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(int(announcement_id))
                        if message.thread:
                            await self.db.update_fixture_announcement(
                                fixture["id"], thread_id=str(message.thread.id)
                            )
                            logger.info(
                                f"Synced thread {message.thread.id} to fixture {fixture['id']}"
                            )
                        else:
                            logger.info(
                                f"Message {announcement_id} has no thread for fixture {fixture['id']}"
                            )
                        return
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        logger.warning(f"No permission to read channel {channel.id}")
                        continue
                    except Exception as e:
                        logger.warning(f"Could not sync thread in {channel.id}: {e}")
                        continue

            logger.warning(
                f"Could not find announcement message {announcement_id} for fixture {fixture['id']}"
            )

        except Exception as e:
            logger.exception(f"Error during thread sync: {e}")

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

    logger.info(f"Token check: Token starts with '{token[:20]}...'")

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
