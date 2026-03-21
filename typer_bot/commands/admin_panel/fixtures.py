"""Fixture-focused admin panel views."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService
from typer_bot.utils import is_admin

from .base import BackButton, FixtureSelect, OwnerRestrictedView, PanelSelectionState

logger = logging.getLogger(__name__)


async def _cleanup_discord_announcement(
    bot: discord.Client,
    channel_id: str,
    message_id: str,
    week_number: int,
) -> None:
    """Best-effort Discord cleanup — logs on failure."""
    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            return
        message = await channel.fetch_message(int(message_id))
        if message.thread is not None:
            await message.thread.delete()
        await message.delete()
    except Exception:
        logger.warning("Could not delete Discord announcement for Week %s fixture", week_number)


def _build_delete_confirmation_content(fixture: dict) -> str:
    lines = [f"**Delete Week {fixture['week_number']}?**\n"]
    for index, game in enumerate(fixture["games"], 1):
        lines.append(f"{index}. {game}")
    return (
        "\n".join(lines)
        + "\n\nThis will delete the fixture and all associated predictions, results, and scores. Are you sure?"
    )


class FixturesPanelView(OwnerRestrictedView):
    """Panel for fixture deletion and workflow guidance."""

    def __init__(
        self,
        db: Database,
        service: AdminService,
        owner_user_id: str,
        bot: discord.Client | None = None,
    ):
        super().__init__(db, service, owner_user_id, bot=bot)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self)
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(FixturesDeleteButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_open_fixtures()
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

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "Only open fixtures can be deleted from the panel.", ephemeral=True
            )
            return

        confirm_view = DeleteConfirmView(
            self.parent_view.db,
            self.parent_view.owner_user_id,
            fixture_id,
            fixture["week_number"],
            bot=self.parent_view.bot,
            message_id=fixture.get("message_id"),
            channel_id=fixture.get("channel_id"),
        )
        content = _build_delete_confirmation_content(fixture)
        await interaction.response.send_message(content, view=confirm_view, ephemeral=True)


class DeleteConfirmView(discord.ui.View):
    """View for confirming fixture deletion."""

    def __init__(
        self,
        db: Database,
        user_id: str,
        fixture_id: int,
        week_number: int,
        bot: discord.Client | None = None,
        message_id: str | None = None,
        channel_id: str | None = None,
    ):
        super().__init__(timeout=60)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.week_number = week_number
        self.bot = bot
        self.message_id = message_id
        self.channel_id = channel_id

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
            logger.exception(
                "Failed to delete fixture %s (week %s)", self.fixture_id, self.week_number
            )
            await interaction.response.edit_message(
                content="⚠️ Failed to delete the fixture. Check the logs.",
                view=None,
            )
            return

        await interaction.response.edit_message(
            content=f"**Week {self.week_number} Fixture Deleted!**",
            view=None,
        )

        if self.bot is not None and self.message_id is not None and self.channel_id is not None:
            await _cleanup_discord_announcement(
                self.bot, self.channel_id, self.message_id, self.week_number
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


StartFixtureDM = Callable[
    [discord.User | discord.Member, str, int, int],
    Coroutine[Any, Any, bool],
]


class OpenFixtureWarningView(discord.ui.View):
    """Shown when an admin tries to create a fixture while others are already open."""

    def __init__(
        self,
        start_fixture_dm: StartFixtureDM,
        user_id: str,
        channel_id: int,
        guild_id: int,
    ):
        super().__init__(timeout=60)
        self.start_fixture_dm = start_fixture_dm
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
        if not await self.start_fixture_dm(
            interaction.user,
            self.user_id,
            self.channel_id,
            self.guild_id,
        ):
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Fixture creation cancelled.", view=None)
