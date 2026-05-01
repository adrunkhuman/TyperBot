"""Admin Discord commands."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import cast

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
    SETUP_REQUIRED_MESSAGE,
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


def _is_everyone_role(role: discord.Role, guild_id: int | None) -> bool:
    is_default = getattr(role, "is_default", None)
    if callable(is_default) and is_default():
        return True
    return guild_id is not None and role.id == guild_id


async def _save_guild_config(
    db: Database,
    interaction: discord.Interaction,
    admin_role: discord.Role,
    league_channel: discord.TextChannel,
) -> None:
    await db.upsert_guild_config(
        str(interaction.guild_id),
        str(admin_role.id),
        str(league_channel.id),
    )


def _setup_saved_message(admin_role: discord.Role, league_channel: discord.TextChannel) -> str:
    return f"TyperBot setup saved. Admin role: {admin_role.mention}. League channel: {league_channel.mention}."


class EveryoneRoleConfirmView(discord.ui.View):
    def __init__(
        self,
        db: Database,
        owner_user_id: str,
        admin_role: discord.Role,
        league_channel: discord.TextChannel,
    ):
        super().__init__(timeout=60)
        self.db = db
        self.owner_user_id = owner_user_id
        self.admin_role = admin_role
        self.league_channel = league_channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return False
        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server manager can configure TyperBot for this server.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm @everyone", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.guild_id is None or self.league_channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(
                "Setup channel must belong to this server.", ephemeral=True
            )
            return

        await _save_guild_config(self.db, interaction, self.admin_role, self.league_channel)
        await interaction.response.edit_message(
            content=_setup_saved_message(self.admin_role, self.league_channel),
            view=None,
        )


def _everyone_warning_view(
    db: Database,
    interaction: discord.Interaction,
    admin_role: discord.Role,
    league_channel: discord.TextChannel,
) -> EveryoneRoleConfirmView:
    return EveryoneRoleConfirmView(db, str(interaction.user.id), admin_role, league_channel)


class SetupRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent_view: GuildSetupPromptView):
        self.parent_view = parent_view
        super().__init__(placeholder="Choose TyperBot admin role", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.admin_role = self.values[0]
        self.parent_view.refresh_save_button()
        await interaction.response.edit_message(view=self.parent_view)


class SetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: GuildSetupPromptView):
        self.parent_view = parent_view
        super().__init__(
            placeholder="Choose league channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.league_channel = cast(discord.TextChannel, self.values[0])
        self.parent_view.refresh_save_button()
        await interaction.response.edit_message(view=self.parent_view)


class SaveSetupButton(discord.ui.Button):
    def __init__(self, parent_view: GuildSetupPromptView):
        self.parent_view = parent_view
        super().__init__(label="Save Setup", style=discord.ButtonStyle.success, disabled=True)

    async def callback(self, interaction: discord.Interaction):
        admin_role = self.parent_view.admin_role
        league_channel = self.parent_view.league_channel
        if admin_role is None or league_channel is None:
            await interaction.response.send_message(
                "Choose an admin role and league channel first.", ephemeral=True
            )
            return
        if interaction.guild_id is None or league_channel.guild.id != interaction.guild_id:
            await interaction.response.send_message(
                "Setup channel must belong to this server.", ephemeral=True
            )
            return

        if _is_everyone_role(admin_role, interaction.guild_id):
            await interaction.response.edit_message(
                content="You selected @everyone as the TyperBot admin role. This gives every server member access to admin actions. Confirm this intentionally?",
                view=_everyone_warning_view(
                    self.parent_view.db, interaction, admin_role, league_channel
                ),
            )
            return

        await _save_guild_config(self.parent_view.db, interaction, admin_role, league_channel)
        await interaction.response.edit_message(
            content=_setup_saved_message(admin_role, league_channel),
            view=None,
        )


class GuildSetupPromptView(discord.ui.View):
    def __init__(self, db: Database, owner_user_id: str):
        super().__init__(timeout=180)
        self.db = db
        self.owner_user_id = owner_user_id
        self.admin_role: discord.Role | None = None
        self.league_channel: discord.TextChannel | None = None
        self.role_select = SetupRoleSelect(self)
        self.channel_select = SetupChannelSelect(self)
        self.save_button = SaveSetupButton(self)
        self.add_item(self.role_select)
        self.add_item(self.channel_select)
        self.add_item(self.save_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return False
        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server manager can configure TyperBot for this server.", ephemeral=True
            )
            return False
        return True

    def refresh_save_button(self) -> None:
        self.save_button.disabled = self.admin_role is None or self.league_channel is None


class StartSetupButton(discord.ui.Button):
    def __init__(self, parent_view: GuildSetupStartView):
        self.parent_view = parent_view
        super().__init__(label="Setup TyperBot", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server manager can configure TyperBot for this server.", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Choose the TyperBot admin role and league channel below.",
            view=GuildSetupPromptView(self.parent_view.db, str(interaction.user.id)),
        )


class GuildSetupStartView(discord.ui.View):
    def __init__(self, db: Database, owner_user_id: str):
        super().__init__(timeout=180)
        self.db = db
        self.owner_user_id = owner_user_id
        self.add_item(StartSetupButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return False
        return True


async def send_setup_prompt_if_allowed(interaction: discord.Interaction, db: Database) -> None:
    if not interaction.guild or interaction.guild_id is None:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return
    if not has_setup_permission(interaction):
        await interaction.response.send_message(SETUP_REQUIRED_MESSAGE, ephemeral=True)
        return

    await interaction.response.send_message(
        "TyperBot needs setup before league admin commands can be used.",
        view=GuildSetupStartView(db, str(interaction.user.id)),
        ephemeral=True,
    )


def admin_only():
    """Decorator to check if user has admin permissions."""

    async def predicate(interaction: discord.Interaction) -> bool:
        db = getattr(interaction.client, "db", None)
        if db is None:
            await interaction.response.send_message("Bot database is not ready.", ephemeral=True)
            return False
        if (
            interaction.guild_id is not None
            and await db.get_guild_config(str(interaction.guild_id)) is None
        ):
            await send_setup_prompt_if_allowed(interaction, db)
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
        self._calculate_cooldowns: dict[tuple[str, str], float] = {}

    def get_calculate_cooldown_remaining(
        self,
        guild_id: str,
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
            stored_key
            for stored_key, timestamp in self._calculate_cooldowns.items()
            if timestamp < cutoff
        ]
        for stored_key in expired_users:
            self._calculate_cooldowns.pop(stored_key, None)

        last_used = self._calculate_cooldowns.get((guild_id, user_id))
        if last_used is None:
            return 0.0

        return max(0.0, cooldown_seconds - (current_time - last_used))

    def record_calculate_cooldown(
        self, guild_id: str, user_id: str, *, current_time: float
    ) -> None:
        self._calculate_cooldowns[(guild_id, user_id)] = current_time

    def get_calculate_cooldown(self, guild_id: str, user_id: str) -> float | None:
        return self._calculate_cooldowns.get((guild_id, user_id))

    def cleanup_expired_state(self) -> int:
        cutoff = now().timestamp() - COOLDOWN_ENTRY_EXPIRY.total_seconds()
        expired = [key for key, ts in self._calculate_cooldowns.items() if ts < cutoff]
        for key in expired:
            self._calculate_cooldowns.pop(key, None)
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
    async def panel(self, interaction: discord.Interaction):
        permission_error = await get_admin_permission_error(interaction, self.db)
        if permission_error is not None:
            if await self.db.get_guild_config(str(interaction.guild_id)) is None:
                await send_setup_prompt_if_allowed(interaction, self.db)
            else:
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


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
