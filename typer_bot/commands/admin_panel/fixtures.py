"""Fixture-focused admin panel views."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.utils import is_admin

from .base import BackButton, FixtureSelect, OwnerRestrictedView, PanelSelectionState

if TYPE_CHECKING:
    from typer_bot.commands.admin_commands import AdminCommands

logger = logging.getLogger(__name__)


class FixturesPanelView(OwnerRestrictedView):
    """Panel for fixture deletion and workflow guidance."""

    def __init__(self, admin_cog: AdminCommands, owner_user_id: str):
        super().__init__(admin_cog, owner_user_id)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self, open_only=True)
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(FixturesDeleteButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.admin_cog.db.get_open_fixtures()
        self.fixture_select.update_options(fixtures)

    def render_content(self) -> str:
        status = (
            self.selection.status_message
            or "Select an open fixture to delete, or use `/admin fixture create` for new fixtures."
        )
        return "**Admin Panel - Fixtures**\n" + status


class FixturesDeleteButton(discord.ui.Button):
    def __init__(self, parent_view: FixturesPanelView):
        self.parent_view = parent_view
        super().__init__(label="Delete Fixture", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select an open fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "Only open fixtures can be deleted from the panel.", ephemeral=True
            )
            return

        confirm_view = DeleteConfirmView(
            self.parent_view.admin_cog.db,
            self.parent_view.owner_user_id,
            fixture_id,
            fixture["week_number"],
        )
        lines = [f"**Delete Week {fixture['week_number']}?**\n"]
        for index, game in enumerate(fixture["games"], 1):
            lines.append(f"{index}. {game}")
        content = (
            "\n".join(lines)
            + "\n\nThis will delete the fixture and all associated predictions/results/scores. Are you sure?"
        )
        await interaction.response.send_message(content, view=confirm_view, ephemeral=True)


class DeleteConfirmView(discord.ui.View):
    """View for confirming fixture deletion."""

    def __init__(self, db: Database, user_id: str, fixture_id: int, week_number: int):
        super().__init__(timeout=60)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.week_number = week_number

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        try:
            await self.db.delete_fixture(self.fixture_id)
        except Exception:
            logger.exception("Failed to delete fixture %s", self.fixture_id)
            await interaction.response.edit_message(
                content="⚠️ Failed to delete the fixture. Check the logs.",
                view=None,
            )
            return

        await interaction.response.edit_message(
            content=f"**Week {self.week_number} Fixture Deleted!**",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Deletion cancelled. The fixture is still active.",
            view=None,
        )


class OpenFixtureWarningView(discord.ui.View):
    """Shown when an admin tries to create a fixture while others are already open."""

    def __init__(self, admin_cog: AdminCommands, user_id: str, channel_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.admin_cog = admin_cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return False
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes, Proceed", style=discord.ButtonStyle.danger)
    async def proceed(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Check your DMs! I've sent you instructions for creating the fixture.",
            view=None,
        )
        await self.admin_cog._start_fixture_dm(
            interaction.user,
            self.user_id,
            self.channel_id,
            self.guild_id,
            followup=interaction.followup,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Fixture creation cancelled.", view=None)
