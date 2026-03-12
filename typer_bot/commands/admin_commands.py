"""Admin Discord commands."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.commands.admin_panel import AdminPanelHomeView, DeleteConfirmView
from typer_bot.database import Database
from typer_bot.handlers import FixtureCreationHandler, ResultsEntryHandler
from typer_bot.services import AdminService, WorkflowStateStore
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import format_standings, is_admin, is_admin_member, now
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

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
        self.db: Database = bot.db  # type: ignore
        self.workflow_state: WorkflowStateStore = bot.workflow_state  # type: ignore[attr-defined]
        self.service = AdminService(self.db)
        self.fixture_handler = FixtureCreationHandler(bot, self.db, self.workflow_state)
        self.results_handler = ResultsEntryHandler(bot, self.db, self.workflow_state)

    @staticmethod
    def _format_open_weeks(open_fixtures: list[dict]) -> str:
        return ", ".join(str(fixture["week_number"]) for fixture in open_fixtures)

    async def _resolve_open_fixture(
        self,
        interaction: discord.Interaction,
        week: int | None,
        command_example: str,
    ) -> dict | None:
        open_fixtures = await self.db.get_open_fixtures()

        if not open_fixtures:
            await interaction.response.send_message("No open fixtures found!", ephemeral=True)
            return None

        if week is None:
            if len(open_fixtures) == 1:
                return open_fixtures[0]

            open_weeks = self._format_open_weeks(open_fixtures)
            await interaction.response.send_message(
                "Multiple fixtures are currently open. "
                "Please specify the `week` argument to choose one.\n"
                f"Open weeks: {open_weeks}\n"
                f"Example: `{command_example}`",
                ephemeral=True,
            )
            return None

        matching = [fixture for fixture in open_fixtures if fixture["week_number"] == week]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            await interaction.response.send_message(
                f"More than one open fixture was found for week {week}. "
                "Please resolve duplicate week numbers in the database before continuing.",
                ephemeral=True,
            )
            return None

        open_weeks = self._format_open_weeks(open_fixtures)
        await interaction.response.send_message(
            f"No open fixture found for week {week}.\nOpen weeks: {open_weeks}",
            ephemeral=True,
        )
        return None

    async def _create_backup(self) -> None:
        try:
            await self.bot.loop.run_in_executor(
                None, lambda: create_backup(self.db.db_path, BACKUP_DIR)
            )
            await self.bot.loop.run_in_executor(
                None, lambda: cleanup_old_backups(BACKUP_DIR, keep=10)
            )
        except Exception as exc:
            logger.warning(f"Backup failed but calculation succeeded: {exc}")

    async def _post_calculation_to_channel(
        self,
        interaction: discord.Interaction,
        score_result: FixtureScoreResult,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
            await interaction.response.send_message(
                "Could not find channel to post in.", ephemeral=True
            )
            return

        message = format_standings(score_result.standings, score_result.last_fixture)

        try:
            await channel.send(message)
            await interaction.response.send_message(
                f"Week {score_result.fixture['week_number']} results calculated and posted!",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error(f"Failed to post results to channel: {exc}")
            await interaction.response.send_message(
                "Scores calculated but failed to post to channel.", ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)

        if self.fixture_handler.has_session(user_id):
            await self.fixture_handler.handle_dm(message, user_id, is_admin_member)
            return

        if self.results_handler.has_session(user_id):
            await self.results_handler.handle_dm(message, user_id, is_admin_member)
            return

    admin = app_commands.Group(name="admin", description="Admin commands for managing fixtures")
    fixture = app_commands.Group(name="fixture", description="Manage fixtures", parent=admin)
    results = app_commands.Group(name="results", description="Manage results", parent=admin)

    @admin.command(name="panel", description="Open the admin management panel")
    @admin_only()
    async def panel(self, interaction: discord.Interaction):
        view = AdminPanelHomeView(self, str(interaction.user.id))
        await interaction.response.send_message(
            "**Admin Panel**\nChoose the workflow you want to manage.",
            view=view,
            ephemeral=True,
        )

    @fixture.command(name="create", description="Create a new fixture (DM workflow)")
    @admin_only()
    async def fixture_create(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
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
            self.fixture_handler.cancel_session(user_id, reason="dm_forbidden")
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @fixture.command(name="delete", description="Delete an open fixture")
    @admin_only()
    async def fixture_delete(self, interaction: discord.Interaction, week: int | None = None):
        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin fixture delete week:12",
        )
        if not fixture:
            return

        view = DeleteConfirmView(
            self.db,
            str(interaction.user.id),
            fixture["id"],
            fixture["week_number"],
        )

        lines = [f"**Delete Week {fixture['week_number']}?**\n"]
        for index, game in enumerate(fixture["games"], 1):
            lines.append(f"{index}. {game}")

        await interaction.response.send_message(
            "\n".join(lines)
            + "\n\nThis will delete the fixture and all associated predictions. Are you sure?",
            view=view,
            ephemeral=True,
        )

    @results.command(name="enter", description="Enter results for an open fixture (DM workflow)")
    @admin_only()
    async def results_enter(self, interaction: discord.Interaction, week: int | None = None):
        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin results enter week:12",
        )
        if not fixture:
            return

        existing_results = await self.db.get_results(fixture["id"])
        if existing_results:
            await interaction.response.send_message(
                "Results already entered for this fixture. "
                "Use `/admin panel` to correct them or `/admin results calculate` to post scores.",
                ephemeral=True,
            )
            return

        user_id = str(interaction.user.id)
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
            self.results_handler.cancel_session(user_id, reason="dm_forbidden")
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @results.command(name="calculate", description="Calculate scores and post results")
    @admin_only()
    async def results_calculate(self, interaction: discord.Interaction, week: int | None = None):
        user_id = str(interaction.user.id)
        current_time = now().timestamp()
        remaining = self.workflow_state.get_calculate_cooldown_remaining(
            user_id,
            current_time=current_time,
            cooldown_seconds=CALCULATE_COOLDOWN,
        )
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {remaining:.1f}s before calculating again.",
                ephemeral=True,
            )
            return

        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin results calculate week:12",
        )
        if not fixture:
            return

        self.workflow_state.record_calculate_cooldown(user_id, current_time=current_time)

        try:
            score_result = await self.service.calculate_fixture_scores(fixture["id"])
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self._create_backup()
        await self._post_calculation_to_channel(interaction, score_result)

    @results.command(name="post", description="Post results with optional user mentions")
    @admin_only()
    async def results_post(self, interaction: discord.Interaction):
        fixture_data = await self.db.get_last_fixture_scores()
        standings = await self.db.get_standings()

        if not fixture_data:
            await interaction.response.send_message(
                "No completed fixtures found with scores!", ephemeral=True
            )
            return

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


class PostResultsConfirmView(discord.ui.View):
    """View for confirming results posting with mentions."""

    def __init__(
        self,
        db: Database,
        fixture_data: dict,
        standings: list[dict],
        channel: discord.TextChannel,
    ):
        super().__init__(timeout=60)
        self.db = db
        self.fixture_data = fixture_data
        self.standings = standings
        self.channel = channel

    @discord.ui.button(label="NO", style=discord.ButtonStyle.primary)
    async def no_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)

        try:
            await interaction.response.edit_message(
                content="Results posted without mentions!",
                view=None,
            )
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            if not interaction.response.is_done():
                await interaction.response.edit_message(
                    content=f"Failed to post results: {exc}",
                    view=None,
                )
            else:
                await interaction.followup.send(f"Failed to post results: {exc}")

    @discord.ui.button(label="YES", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        mentions = [f"<@{score['user_id']}>" for score in self.fixture_data["scores"]]
        message += f"\n\n**Participants:**\n{' '.join(mentions)}"

        try:
            await interaction.response.edit_message(
                content="Results posted with mentions!",
                view=None,
            )
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            if not interaction.response.is_done():
                await interaction.response.edit_message(
                    content=f"Failed to post results: {exc}",
                    view=None,
                )
            else:
                await interaction.followup.send(f"Failed to post results: {exc}")


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
