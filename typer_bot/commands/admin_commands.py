"""Admin Discord commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.handlers import FixtureCreationHandler, ResultsEntryHandler
from typer_bot.utils import (
    calculate_points,
    format_standings,
    is_admin,
    is_admin_member,
    now,
)
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

# Rate limiting: user_id -> timestamp
_calculate_cooldowns: dict = {}
CALCULATE_COOLDOWN = 30.0

logger = logging.getLogger(__name__)


def admin_only():
    """Decorator to check if user has admin permissions."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return False
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You don't have permission to use admin commands.", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


class AdminCommands(commands.Cog):
    """Commands for admins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # TyperBot sets db attr dynamically; discord.py typing doesn't track custom attrs
        self.db: Database = bot.db  # type: ignore
        self.fixture_handler = FixtureCreationHandler(bot, self.db)
        self.results_handler = ResultsEntryHandler(bot, self.db)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs from admins."""
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)

        # Check if this is a fixture creation DM
        if self.fixture_handler.has_session(user_id):
            await self.fixture_handler.handle_dm(message, user_id, is_admin_member)
            return

        # Check if this is a results entry DM
        if self.results_handler.has_session(user_id):
            await self.results_handler.handle_dm(message, user_id, is_admin_member)
            return

    # Admin group
    admin = app_commands.Group(name="admin", description="Admin commands for managing fixtures")

    # Fixture subgroup
    fixture = app_commands.Group(name="fixture", description="Manage fixtures", parent=admin)

    @fixture.command(name="create", description="Create a new fixture (DM workflow)")
    @admin_only()
    async def fixture_create(self, interaction: discord.Interaction):
        """Initiate fixture creation via DM."""
        user_id = str(interaction.user.id)
        # admin_only() decorator ensures guild context, so these should always exist
        if interaction.channel_id is None or interaction.guild_id is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        self.fixture_handler.start_session(user_id, interaction.channel_id, interaction.guild_id)

        await interaction.response.send_message(
            "Check your DMs! I've sent you instructions for creating the fixture.",
            ephemeral=True,
        )

        try:
            await interaction.user.send(
                "**Create New Fixture**\n\n"
                "Step 1/2: Send me the list of games in this format:\n"
                "```\n"
                "Team A - Team B\n"
                "Team C - Team D\n"
                "Team E - Team F\n"
                "...\n"
                "```\n"
                "One game per line."
            )
        except discord.Forbidden:
            self.fixture_handler.cancel_session(user_id)
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @fixture.command(name="delete", description="Delete the current fixture")
    @admin_only()
    async def fixture_delete(self, interaction: discord.Interaction):
        """Delete current fixture."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "No active fixture found to delete!", ephemeral=True
            )
            return

        view = DeleteConfirmView(
            self.db, str(interaction.user.id), fixture["id"], fixture["week_number"]
        )

        lines = [f"**Delete Week {fixture['week_number']}?**\n"]
        for i, game in enumerate(fixture["games"], 1):
            lines.append(f"{i}. {game}")

        await interaction.response.send_message(
            "\n".join(lines)
            + "\n\nThis will delete the fixture and all associated predictions. Are you sure?",
            view=view,
            ephemeral=True,
        )

    # Results subgroup
    results = app_commands.Group(name="results", description="Manage results", parent=admin)

    @results.command(
        name="enter", description="Enter results for the current fixture (DM workflow)"
    )
    @admin_only()
    async def results_enter(self, interaction: discord.Interaction):
        """Initiate results entry via DM."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message("No active fixture found!", ephemeral=True)
            return

        existing_results = await self.db.get_results(fixture["id"])
        if existing_results:
            await interaction.response.send_message(
                "Results already entered for this fixture!\n"
                "Use `/admin results calculate` to calculate scores.",
                ephemeral=True,
            )
            return

        user_id = str(interaction.user.id)
        # admin_only() decorator ensures guild context, so this should always exist
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        self.results_handler.start_session(user_id, fixture["id"], interaction.guild_id)

        await interaction.response.send_message(
            "Check your DMs! I've sent you instructions for entering results.",
            ephemeral=True,
        )

        try:
            lines = [
                f"**Week {fixture['week_number']} - Enter Results**",
                "",
                "Reply with the actual results in this format:",
                "```",
            ]
            for game in fixture["games"]:
                lines.append(f"{game} 2:0")
            lines.extend(
                [
                    "```",
                    "",
                    "Add the actual score (e.g., 2:0 or 2-1) at the end of each line.",
                    "Type 'x' for cancelled or postponed games.",
                ]
            )
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            self.results_handler.cancel_session(user_id)
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @results.command(name="calculate", description="Calculate scores and post results")
    @admin_only()
    async def results_calculate(self, interaction: discord.Interaction):
        """Calculate scores for current fixture and post results."""
        user_id = str(interaction.user.id)
        current_time = now().timestamp()

        if user_id in _calculate_cooldowns:
            last_used = _calculate_cooldowns[user_id]
            if current_time - last_used < CALCULATE_COOLDOWN:
                remaining = CALCULATE_COOLDOWN - (current_time - last_used)
                await interaction.response.send_message(
                    f"Please wait {remaining:.1f}s before calculating again.",
                    ephemeral=True,
                )
                return

        _calculate_cooldowns[user_id] = current_time

        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message("No active fixture found!", ephemeral=True)
            return

        results = await self.db.get_results(fixture["id"])
        if not results:
            await interaction.response.send_message(
                "No results entered for this fixture!\nUse `/admin results enter` first.",
                ephemeral=True,
            )
            return

        predictions = await self.db.get_all_predictions(fixture["id"])

        if not predictions:
            await interaction.response.send_message(
                "No predictions found for this fixture!", ephemeral=True
            )
            return

        # Calculate scores
        scores = []
        for pred in predictions:
            score_data = calculate_points(pred["predictions"], results, pred["is_late"])
            scores.append(
                {
                    "user_id": pred["user_id"],
                    "user_name": pred["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        scores.sort(key=lambda x: x["points"], reverse=True)
        await self.db.save_scores(fixture["id"], scores)

        # Create backup
        try:
            await self.bot.loop.run_in_executor(
                None, lambda: create_backup(self.db.db_path, BACKUP_DIR)
            )
            await self.bot.loop.run_in_executor(
                None, lambda: cleanup_old_backups(BACKUP_DIR, keep=10)
            )
        except Exception as e:
            logger.warning(f"Backup failed but calculation succeeded: {e}")

        # Post results to channel (without mentions by default)
        channel = interaction.channel
        if channel:
            # Get overall standings and format the complete message
            standings = await self.db.get_standings()
            last_fixture = await self.db.get_last_fixture_scores()

            # Use format_standings to generate both overall and last week results
            message = format_standings(standings, last_fixture)

            try:
                # Send the complete standings message
                await channel.send(message)

                await interaction.response.send_message(
                    f"Week {fixture['week_number']} results calculated and posted!",
                    ephemeral=True,
                )
            except Exception as e:
                logger.error(f"Failed to post results to channel: {e}")
                await interaction.response.send_message(
                    "Scores calculated but failed to post to channel.", ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "Could not find channel to post in.", ephemeral=True
            )

    @results.command(name="post", description="Post results with optional user mentions")
    @admin_only()
    async def results_post(self, interaction: discord.Interaction):
        """Post the latest fixture results to the channel with mention confirmation."""
        # Get the latest closed fixture scores and overall standings
        fixture_data = await self.db.get_last_fixture_scores()
        standings = await self.db.get_standings()

        if not fixture_data:
            await interaction.response.send_message(
                "No completed fixtures found with scores!", ephemeral=True
            )
            return

        # Show preview with confirmation buttons using format_standings
        preview = format_standings(standings, fixture_data)

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in text channels.", ephemeral=True
            )
            return

        view = PostResultsConfirmView(self.db, fixture_data, standings, interaction.channel)

        await interaction.response.send_message(
            f"{preview}\n\nMention users in this post?",
            view=view,
            ephemeral=True,
        )


class DeleteConfirmView(discord.ui.View):
    """View for confirming fixture deletion."""

    def __init__(self, db: Database, user_id: str, fixture_id: int, week_number: int):
        super().__init__(timeout=60)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.week_number = week_number

    async def on_timeout(self):
        pass

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        """Delete fixture from database."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await self.db.delete_fixture(self.fixture_id)

        await interaction.response.edit_message(
            content=f"**Week {self.week_number} Fixture Deleted!**", view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        """Cancel deletion."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Deletion cancelled. The fixture is still active.", view=None
        )


class PostResultsConfirmView(discord.ui.View):
    """View for confirming results posting with mentions."""

    def __init__(
        self, db: Database, fixture_data: dict, standings: list[dict], channel: discord.TextChannel
    ):
        super().__init__(timeout=60)
        self.db = db
        self.fixture_data = fixture_data
        self.standings = standings
        self.channel = channel

    async def on_timeout(self):
        pass

    @discord.ui.button(label="NO", style=discord.ButtonStyle.primary)
    async def no_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        """Post results without mentions."""
        message = format_standings(self.standings, self.fixture_data)

        try:
            await self.channel.send(message)
            await interaction.response.edit_message(
                content="Results posted without mentions!", view=None
            )
        except Exception as e:
            logger.error(f"Failed to post results: {e}")
            await interaction.response.edit_message(
                content=f"Failed to post results: {e}", view=None
            )

    @discord.ui.button(label="YES", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        """Post results with user mentions."""
        message = format_standings(self.standings, self.fixture_data)

        # Add mentions section
        mentions = []
        for score in self.fixture_data["scores"]:
            mentions.append(f"<@{score['user_id']}>")

        message += f"\n\n**Participants:**\n{' '.join(mentions)}"

        try:
            await self.channel.send(message)
            await interaction.response.edit_message(
                content="Results posted with mentions!", view=None
            )
        except Exception as e:
            logger.error(f"Failed to post results: {e}")
            await interaction.response.edit_message(
                content=f"Failed to post results: {e}", view=None
            )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
