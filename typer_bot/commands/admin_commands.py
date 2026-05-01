"""Admin Discord commands."""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.commands.admin_panel import (
    UnifiedAdminPanelView,
)
from typer_bot.database import Database
from typer_bot.services import AdminService
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import (
    format_fixture_results,
    format_standings,
    get_admin_permission_error,
    has_setup_permission,
    now,
)
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

CALCULATE_COOLDOWN = 30.0
COOLDOWN_ENTRY_EXPIRY = timedelta(hours=1)

logger = logging.getLogger(__name__)


def admin_only():
    """Decorator to check if user has admin permissions."""

    async def predicate(interaction: discord.Interaction) -> bool:
        db = getattr(interaction.client, "db", None)
        if db is None:
            await interaction.response.send_message("Bot database is not ready.", ephemeral=True)
            return False
        permission_error = await get_admin_permission_error(interaction, db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
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

    admin = app_commands.Group(
        name="admin", description="Open the admin panel and manage fixtures/results"
    )

    @admin.command(name="panel", description="Open the admin management panel")
    @admin_only()
    async def panel(self, interaction: discord.Interaction):
        permission_error = await get_admin_permission_error(interaction, self.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        view = UnifiedAdminPanelView(
            self.db,
            self.service,
            str(interaction.user.id),
            str(interaction.guild_id),
            admin_commands=self,
            bot=self.bot,
        )
        await view.load_fixture_options()
        await interaction.response.send_message(
            view.render_content(),
            view=view,
            ephemeral=True,
        )

    @admin.command(name="setup", description="Configure TyperBot for this server")
    @app_commands.describe(
        admin_role="Role allowed to use TyperBot admin actions",
        channel="League channel for fixture announcements and reminders",
    )
    async def setup_config(
        self,
        interaction: discord.Interaction,
        admin_role: discord.Role,
        channel: discord.TextChannel,
    ):
        if not interaction.guild or interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server manager can configure TyperBot for this server.", ephemeral=True
            )
            return

        if channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(
                "Setup channel must belong to this server.", ephemeral=True
            )
            return

        await self.db.upsert_guild_config(
            str(interaction.guild_id),
            str(admin_role.id),
            str(channel.id),
        )
        await interaction.response.send_message(
            f"TyperBot setup saved. Admin role: {admin_role.mention}. League channel: {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
