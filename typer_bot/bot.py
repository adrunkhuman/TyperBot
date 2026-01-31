"""Main Discord bot implementation."""

import os
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from typer_bot.database import Database
from typer_bot.utils import parse_predictions, format_standings, calculate_points

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TyperBot(commands.Bot):
    """Football predictions Discord bot."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        
        self.db = Database()

    async def setup_hook(self):
        """Initialize database and load cogs."""
        await self.db.initialize()
        logger.info("Database initialized")
        
        # Load command cogs
        await self.load_extension("typer_bot.commands.user_commands")
        await self.load_extension("typer_bot.commands.admin_commands")
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
        
        # Start scheduled tasks
        self.reminder_task.start()

    async def on_ready(self):
        """Called when bot is ready."""
        logger.info(f"{self.user} has connected to Discord!")
        logger.info(f"Bot is in {len(self.guilds)} guilds")

    def cog_unload(self):
        """Clean up when bot shuts down."""
        self.reminder_task.cancel()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        """Check for reminders to send."""
        now = datetime.now()
        
        # Thursday 19:00 reminder
        if now.weekday() == 3 and now.hour == 19 and now.minute == 0:
            await self.send_reminder("Thursday evening")
        
        # Friday 17:00 reminder
        if now.weekday() == 4 and now.hour == 17 and now.minute == 0:
            await self.send_reminder("Friday evening")

    async def send_reminder(self, time_description: str):
        """Send prediction reminder to configured channel."""
        channel_id = os.getenv("REMINDER_CHANNEL_ID")
        if not channel_id:
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
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")

    def is_admin(self, user: discord.Member) -> bool:
        """Check if user has admin role."""
        admin_roles = {"Admin", "typer-admin"}
        return any(role.name in admin_roles for role in user.roles)


def main():
    """Run the bot."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable not set")
    
    bot = TyperBot()
    bot.run(token)


if __name__ == "__main__":
    main()