"""Admin Discord commands."""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.commands.admin_panel import (
    CreateFixtureModal,
    DeleteConfirmView,
    EnterResultsModal,
    UnifiedAdminPanelView,
    _build_delete_confirmation_content,
)
from typer_bot.database import Database
from typer_bot.services import AdminService
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import format_fixture_results, format_standings, is_admin, now
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

CALCULATE_COOLDOWN = 30.0
COOLDOWN_ENTRY_EXPIRY = timedelta(hours=1)

logger = logging.getLogger(__name__)


class FixtureDeleteSelect(discord.ui.Select):
    """Owner-only fixture picker for ambiguous delete flows.

    This is only shown when multiple open fixtures exist and the admin omitted
    the `week` argument. Options are capped at Discord's 25-item select limit.
    """

    def __init__(self, fixtures: list[dict]):
        options = [
            discord.SelectOption(
                label=f"Week {fixture['week_number']}",
                value=str(fixture["id"]),
                description=f"{fixture['status']} fixture",
            )
            for fixture in fixtures[:25]
        ]
        super().__init__(placeholder="Select a fixture to delete", options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, FixtureSelectForDeleteView):
            await interaction.response.send_message(
                "Fixture picker is no longer available.", ephemeral=True
            )
            return
        if str(interaction.user.id) != view.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        fixture_id = int(self.values[0])
        fixture = await view.db.get_fixture_by_id(fixture_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.edit_message(
                content="That fixture is no longer open. Use `/admin fixture delete` again to refresh the list.",
                view=None,
            )
            return

        delete_view = DeleteConfirmView(
            view.db,
            view.owner_user_id,
            fixture["id"],
            fixture["week_number"],
            bot=view.bot,
            message_id=fixture.get("message_id"),
            channel_id=fixture.get("channel_id"),
        )
        await interaction.response.edit_message(
            content=_build_delete_confirmation_content(fixture),
            view=delete_view,
        )


class FixtureSelectForDeleteView(discord.ui.View):
    """Ephemeral wrapper around the delete fixture picker."""

    def __init__(self, db: Database, owner_user_id: str, fixtures: list[dict], bot: commands.Bot):
        super().__init__(timeout=3600)
        self.db = db
        self.owner_user_id = owner_user_id
        self.bot = bot
        self.add_item(FixtureDeleteSelect(fixtures))


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
        self.service = AdminService(self.db)
        self._calculate_cooldowns: dict[str, float] = {}

    def get_calculate_cooldown_remaining(
        self,
        user_id: str,
        *,
        current_time: float,
        cooldown_seconds: float,
    ) -> float:
        """Return remaining calculate cooldown seconds for a user.

        `current_time` is expected to be a Unix timestamp float. Expired
        entries are pruned before the remaining duration is calculated.
        """
        cutoff = current_time - COOLDOWN_ENTRY_EXPIRY.total_seconds()
        expired_users = [
            stored_user_id
            for stored_user_id, timestamp in self._calculate_cooldowns.items()
            if timestamp < cutoff
        ]
        for stored_user_id in expired_users:
            self._calculate_cooldowns.pop(stored_user_id, None)

        last_used = self._calculate_cooldowns.get(user_id)
        if last_used is None:
            return 0.0

        return max(0.0, cooldown_seconds - (current_time - last_used))

    def record_calculate_cooldown(self, user_id: str, *, current_time: float) -> None:
        self._calculate_cooldowns[user_id] = current_time

    def get_calculate_cooldown(self, user_id: str) -> float | None:
        return self._calculate_cooldowns.get(user_id)

    def cleanup_expired_state(self) -> int:
        cutoff = now().timestamp() - COOLDOWN_ENTRY_EXPIRY.total_seconds()
        expired = [uid for uid, ts in self._calculate_cooldowns.items() if ts < cutoff]
        for uid in expired:
            self._calculate_cooldowns.pop(uid, None)
        return len(expired)

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

        results_section = format_fixture_results(
            score_result.fixture["games"],
            score_result.results,
            score_result.fixture["week_number"],
        )
        message = (
            results_section
            + "\n\n"
            + format_standings(score_result.standings, score_result.last_fixture)
        )

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

    admin = app_commands.Group(name="admin", description="Admin commands for managing fixtures")
    fixture = app_commands.Group(name="fixture", description="Manage fixtures", parent=admin)
    results = app_commands.Group(name="results", description="Manage results", parent=admin)

    @admin.command(name="panel", description="Open the admin management panel")
    @admin_only()
    async def panel(self, interaction: discord.Interaction):
        view = UnifiedAdminPanelView(self.db, self.service, str(interaction.user.id), bot=self.bot)
        await view.load_fixture_options()
        await interaction.response.send_message(
            view.render_content(),
            view=view,
            ephemeral=True,
        )

    @fixture.command(name="create", description="Create a new fixture via modal")
    @admin_only()
    async def fixture_create(self, interaction: discord.Interaction):
        if interaction.channel is None or interaction.guild is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return

        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if not isinstance(parent, discord.TextChannel):
                await interaction.response.send_message(
                    "Fixture creation must be started from a server text channel.",
                    ephemeral=True,
                )
                return
            channel = parent
        elif not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Fixture creation must be started from a server text channel.",
                ephemeral=True,
            )
            return

        modal = CreateFixtureModal(self.db, channel, str(interaction.user.id))
        await interaction.response.send_modal(modal)

    @fixture.command(name="delete", description="Delete an open fixture")
    @admin_only()
    async def fixture_delete(self, interaction: discord.Interaction, week: int | None = None):
        if week is None:
            open_fixtures = await self.db.get_open_fixtures()
            if not open_fixtures:
                await interaction.response.send_message("No open fixtures found!", ephemeral=True)
                return
            if len(open_fixtures) > 1:
                view = FixtureSelectForDeleteView(
                    self.db,
                    str(interaction.user.id),
                    open_fixtures,
                    self.bot,
                )
                await interaction.response.send_message(
                    "Multiple fixtures are open. Choose which one to delete.",
                    view=view,
                    ephemeral=True,
                )
                return

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
            bot=self.bot,
            message_id=fixture.get("message_id"),
            channel_id=fixture.get("channel_id"),
        )

        await interaction.response.send_message(
            _build_delete_confirmation_content(fixture),
            view=view,
            ephemeral=True,
        )

    @results.command(name="enter", description="Enter results for an open fixture")
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

        modal = EnterResultsModal(fixture, self.db)
        await interaction.response.send_modal(modal)

    @results.command(name="calculate", description="Calculate scores and post results")
    @admin_only()
    async def results_calculate(self, interaction: discord.Interaction, week: int | None = None):
        user_id = str(interaction.user.id)
        current_time = now().timestamp()
        remaining = self.get_calculate_cooldown_remaining(
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

        try:
            score_result = await self.service.calculate_fixture_scores(fixture["id"])
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.record_calculate_cooldown(user_id, current_time=current_time)

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
                content="Results posted without mentions!", view=None
            )
        except Exception as exc:
            logger.error(f"Failed to acknowledge interaction: {exc}")
            return

        try:
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            await interaction.followup.send(f"Failed to post results: {exc}")

    @discord.ui.button(label="YES", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        mentions = [f"<@{score['user_id']}>" for score in self.fixture_data["scores"]]
        message += f"\n\n**Participants:**\n{' '.join(mentions)}"

        try:
            await interaction.response.edit_message(
                content="Results posted with mentions!", view=None
            )
        except Exception as exc:
            logger.error(f"Failed to acknowledge interaction: {exc}")
            return

        try:
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            await interaction.followup.send(f"Failed to post results: {exc}")


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
