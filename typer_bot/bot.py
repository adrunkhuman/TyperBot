"""Main Discord bot implementation."""

import asyncio
import logging
import os
import sys
from datetime import timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from typer_bot.database import Database
from typer_bot.handlers.thread_prediction_handler import ThreadPredictionHandler
from typer_bot.services import WorkflowStateStore
from typer_bot.services.dm_router import DMRouter
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
        self.workflow_state = WorkflowStateStore()
        self.thread_handler = ThreadPredictionHandler(self, self.db, self.workflow_state)
        self.dm_router: DMRouter | None = None
        logger.info("Database instance created")

    async def on_interaction(self, interaction: discord.Interaction):
        """Set trace ID and context for every interaction before processing."""
        set_trace_id(f"req-{interaction.id}")

        user_id = str(interaction.user.id) if interaction.user else None
        guild_id = str(interaction.guild_id) if interaction.guild_id else None
        set_log_context(user_id=user_id, guild_id=guild_id, source="command")

        # ContextVars are task-local, so this handler does not need explicit cleanup.

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

            if message.guild is None:
                # DMs: explicit router owns precedence — no cog listener ordering dependency.
                if self.dm_router is not None:
                    await self.dm_router.route(message)
            else:
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
        # No cleanup needed: this handler only sets a trace ID, and ContextVars are task-local.

    async def setup_hook(self):
        """Initialize database and load cogs."""
        logger.info("Running setup_hook...")

        try:
            await self.db.initialize()
            logger.info("Database initialized successfully")
        except Exception:
            logger.exception("Database initialization failed")
            raise

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

        admin_cog = self.cogs.get("AdminCommands")
        user_cog = self.cogs.get("UserCommands")
        if admin_cog is None or user_cog is None:
            raise RuntimeError("Required cogs not loaded before DM router initialisation")
        self.dm_router = DMRouter(
            admin_cog.fixture_handler,  # type: ignore[attr-defined]
            admin_cog.results_handler,  # type: ignore[attr-defined]
            user_cog.prediction_handler,  # type: ignore[attr-defined]
        )
        logger.info("DM router initialised")

        logger.info("Syncing slash commands...")
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands")
        except Exception:
            logger.exception("Failed to sync commands")

        logger.info("Starting background tasks...")
        self.reminder_task.start()
        self._cleanup_sessions_task.start()
        logger.info("Setup hook complete")

    async def on_ready(self):
        """Called when bot is ready."""
        if self.user is None:
            logger.warning("Bot ready event fired before user was available")
            return

        logger.info(f"✓ Bot connected: {self.user} (ID: {self.user.id})")
        logger.info(f"✓ Connected to {len(self.guilds)} guild(s):")
        for guild in self.guilds:
            logger.info(f"  - {guild.name} (ID: {guild.id})")

        await self._check_permissions()

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

    async def close(self):
        """Cancel background tasks and wait for them to finish before closing."""
        self.reminder_task.cancel()
        self._cleanup_sessions_task.cancel()
        pending = [
            t
            for loop in (self.reminder_task, self._cleanup_sessions_task)
            if (t := loop.get_task()) is not None
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await super().close()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        """Send reminders 24h and 1h before each open fixture deadline."""
        current_time = now()
        open_fixtures = await self.db.get_open_fixtures()

        if not open_fixtures:
            return

        # Compare minute-level precision to avoid double-sending
        def is_same_minute(t1, t2):
            return t1.replace(second=0, microsecond=0) == t2.replace(second=0, microsecond=0)

        for fixture in open_fixtures:
            deadline = fixture["deadline"]

            reminder_24h = deadline - timedelta(hours=24)
            if is_same_minute(current_time, reminder_24h):
                logger.info(f"Sending 24h reminder for week {fixture['week_number']}...")
                await self.send_reminder(fixture, "24 hours remaining")

            reminder_1h = deadline - timedelta(hours=1)
            if is_same_minute(current_time, reminder_1h):
                logger.info(f"Sending 1h reminder for week {fixture['week_number']}...")
                await self.send_reminder(fixture, "1 hour remaining")

    @reminder_task.before_loop
    async def _before_reminder(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5)
    async def _cleanup_sessions_task(self) -> None:
        removed = self.workflow_state.cleanup_all_expired()
        if removed:
            logger.debug(f"Session cleanup removed {removed} expired DM session(s)")

    @_cleanup_sessions_task.before_loop
    async def _before_cleanup_sessions(self):
        await self.wait_until_ready()

    async def send_reminder(self, fixture: dict, time_description: str):
        """Send prediction reminder to configured channel."""
        channel_id = os.getenv("REMINDER_CHANNEL_ID")
        if not channel_id:
            logger.warning("REMINDER_CHANNEL_ID not set, skipping reminder")
            return

        try:
            channel = self.get_channel(int(channel_id))
            if channel is None:
                logger.error(f"Could not find channel {channel_id}")
                return

            send = getattr(channel, "send", None)
            if send is None:
                logger.error(f"Reminder channel {channel_id} does not support sending messages")
                return

            deadline = format_for_discord(fixture["deadline"], "F")
            relative = format_for_discord(fixture["deadline"], "R")
            await send(
                f"📢 **{time_description}!**\n\n"
                f"Don't forget to submit your predictions for this week!\n"
                f"Deadline: **{deadline}** ({relative})\n"
                f"Use `/predict` to enter your scores."
            )
            logger.info(f"Reminder sent to channel {channel_id}")
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
            open_fixtures = await self.db.get_open_fixtures()
            if not open_fixtures:
                logger.info("No open fixture found, skipping verification")
                return

            for fixture in open_fixtures:
                message_id = fixture.get("message_id")
                if not message_id:
                    logger.info(f"Fixture {fixture['id']} has no message_id, skipping verification")
                    continue

                found = False
                for guild in self.guilds:
                    for channel in guild.text_channels:
                        try:
                            message = await channel.fetch_message(int(message_id))
                            if message.thread:
                                logger.info(
                                    f"Fixture {fixture['id']} has thread {message.thread.id}"
                                )
                            else:
                                logger.info(
                                    f"Fixture {fixture['id']} has no thread (users can use /predict)"
                                )
                            found = True
                            break
                        except discord.NotFound:
                            continue
                        except discord.Forbidden:
                            logger.warning(f"No permission to read channel {channel.id}")
                            continue
                        except Exception as e:
                            logger.warning(f"Could not verify fixture in {channel.id}: {e}")
                            continue
                    if found:
                        break

                if not found:
                    logger.warning(
                        f"Could not find announcement message {message_id} for fixture {fixture['id']}"
                    )

        except Exception as e:
            logger.exception(f"Error during fixture verification: {e}")

    async def _check_permissions(self):
        """Warn when a guild is missing permissions required for prediction workflows."""
        required_permissions = [
            ("send_messages", "Send Messages"),
            ("read_message_history", "Read Message History"),
            ("add_reactions", "Add Reactions"),
            ("create_public_threads", "Create Public Threads"),
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

    # The guard above exits on missing/placeholder tokens; this keeps the type checker honest.
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
