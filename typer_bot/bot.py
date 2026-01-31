"""Main Discord bot implementation."""

import logging
import os
import sys
from datetime import datetime

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from typer_bot.database import Database
from typer_bot.utils.logger import setup_logging

# Logging MUST be configured before any other imports to prevent
# discord.py from attaching its own default handlers
setup_logging()
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

        super().__init__(command_prefix="!", intents=intents, help_command=None)

        self.db = Database()
        logger.info("Database instance created")

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

    def _validate_archive_sql(self, sql_content: str) -> bool:
        """Validate archive SQL - only INSERTs allowed. Blocks DDL/DML that could corrupt the db."""
        import re

        normalized = re.sub(r"--.*?$", "", sql_content, flags=re.MULTILINE)
        normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
        normalized = normalized.upper()

        dangerous = [
            "DROP",
            "DELETE",
            "UPDATE",
            "ALTER",
            "CREATE",
            "TRUNCATE",
            "REPLACE",
            "ATTACH",
            "DETACH",
            "PRAGMA",
        ]

        for keyword in dangerous:
            if re.search(rf"\b{keyword}\b", normalized):
                return False

        statements = [s.strip() for s in normalized.split(";") if s.strip()]
        return all(stmt.startswith("INSERT") for stmt in statements)

    async def _run_archive_imports(self):
        """Run SQL files from archive folder if database is empty."""
        import glob

        import aiosqlite

        try:
            async with (
                aiosqlite.connect(self.db.db_path) as db,
                db.execute("SELECT COUNT(*) FROM fixtures") as cursor,
            ):
                count = await cursor.fetchone()
                if count and count[0] > 0:
                    logger.info("Database already has fixtures, skipping archive import")
                    return

            archive_files = sorted(glob.glob("archive/*.sql"))
            if not archive_files:
                logger.info("No archive SQL files found")
                return

            logger.info(f"Found {len(archive_files)} archive file(s) to import")

            for sql_file in archive_files:
                logger.info(f"Importing {sql_file}...")
                try:
                    with open(sql_file, encoding="utf-8") as f:
                        sql_content = f.read()

                    if not self._validate_archive_sql(sql_content):
                        logger.error(f"❌ Rejected {sql_file}: contains non-INSERT statements")
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

    async def on_error(self, event_method, *args, **kwargs):
        """Handle uncaught errors."""
        logger.exception(f"Error in {event_method}")

    def cog_unload(self):
        """Clean up when bot shuts down."""
        logger.info("Shutting down reminder task...")
        self.reminder_task.cancel()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        """Check for reminders to send."""
        now = datetime.now()

        if now.weekday() == 3 and now.hour == 19 and now.minute == 0:
            logger.info("Sending Thursday reminder...")
            await self.send_reminder("Thursday evening")

        if now.weekday() == 4 and now.hour == 17 and now.minute == 0:
            logger.info("Sending Friday reminder...")
            await self.send_reminder("Friday evening")

    async def send_reminder(self, time_description: str):
        """Send prediction reminder to configured channel."""
        channel_id = os.getenv("REMINDER_CHANNEL_ID")
        if not channel_id:
            logger.warning("REMINDER_CHANNEL_ID not set, skipping reminder")
            return

        try:
            channel = self.get_channel(int(channel_id))
            if channel:
                fixture = await self.db.get_current_fixture()
                if fixture:
                    deadline = fixture["deadline"].strftime("%A %H:%M")
                    await channel.send(
                        f"📢 **{time_description} reminder!**\n\n"
                        f"Don't forget to submit your predictions for this week!\n"
                        f"Deadline: **{deadline}**\n"
                        f"Use `/predict` to enter your scores."
                    )
                    logger.info(f"Reminder sent to channel {channel_id}")
                else:
                    logger.warning("No active fixture for reminder")
            else:
                logger.error(f"Could not find channel {channel_id}")
        except Exception:
            logger.exception("Failed to send reminder")

    def is_admin(self, user: discord.Member) -> bool:
        """Check if user has admin role (case-insensitive)."""
        admin_roles = {"admin", "typer-admin"}
        return any(role.name.lower() in admin_roles for role in user.roles)


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
    logger.info("Creating TyperBot instance...")

    try:
        bot = TyperBot()
        logger.info("Starting bot.run()...")
        bot.run(token)
    except discord.LoginFailure:
        logger.exception("❌ Discord login failed - check if DISCORD_TOKEN is valid")
        sys.exit(1)
    except Exception:
        logger.exception("❌ Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()
