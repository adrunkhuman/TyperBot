"""Results-focused admin panel views."""

from __future__ import annotations

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService

from .base import (
    MAX_SELECT_OPTIONS,
    BackButton,
    FixtureSelect,
    OwnerRestrictedView,
    PanelSelectionState,
    _format_prediction_line,
)
from .modals import CorrectResultsModal


class ResultsPanelView(OwnerRestrictedView):
    """Panel for result correction workflows."""

    def __init__(self, db: Database, service: AdminService, owner_user_id: str):
        super().__init__(db, service, owner_user_id)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self)
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(ViewResultsButton(self))
        self.add_item(CorrectResultsButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    def render_content(self) -> str:
        status = (
            self.selection.status_message or "Select a fixture to inspect or correct saved results."
        )
        return "**Admin Panel - Results**\n" + status


class ViewResultsButton(discord.ui.Button):
    def __init__(self, parent_view: ResultsPanelView):
        self.parent_view = parent_view
        super().__init__(label="View Results", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            await interaction.response.send_message("Fixture not found.", ephemeral=True)
            return
        results = await self.parent_view.db.get_results(fixture_id)
        if not results:
            await interaction.response.send_message(
                "No results saved for that fixture yet.", ephemeral=True
            )
            return

        lines = [f"**Week {fixture['week_number']} Results**"]
        for index, (game, result) in enumerate(zip(fixture["games"], results, strict=False), 1):
            lines.append(_format_prediction_line(index, game, result))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class CorrectResultsButton(discord.ui.Button):
    def __init__(self, parent_view: ResultsPanelView):
        self.parent_view = parent_view
        super().__init__(label="Correct Results", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            await interaction.response.send_message("Fixture not found.", ephemeral=True)
            return
        if not await self.parent_view.db.get_results(fixture_id):
            await interaction.response.send_message(
                "No results are stored for that fixture yet. Use `/admin results enter` first.",
                ephemeral=True,
            )
            return

        modal = CorrectResultsModal(self.parent_view, fixture)
        await interaction.response.send_modal(modal)
