"""Results-focused admin panel views."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService

from .base import (
    MAX_SELECT_OPTIONS,
    FixtureSelect,
    OwnerRestrictedView,
    PanelSelectionState,
    _build_detail_lines,
    _render_panel_content,
)
from .modals import CorrectResultsModal

if TYPE_CHECKING:
    from .unified import UnifiedAdminPanelView


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
        self.add_item(CorrectResultsButton(self, disabled=self.selection.fixture_id is None))

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    async def populate_fixture_details(self, fixture: dict | None) -> None:
        """Update inline result lines and empty-state message for the selection."""

        self.selection.detail_lines = []
        if fixture is None:
            return

        results = await self.db.get_results(fixture["id"])
        if not results:
            self.selection.status_message = "No results saved for that fixture yet."
            return

        self.selection.detail_lines = _build_detail_lines(fixture["games"], results)

    def render_content(self) -> str:
        lines = ["**Admin Panel - Results**"]
        if self.selection.fixture_label:
            lines.append(f"Fixture: {self.selection.fixture_label}")
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
            if self.selection.detail_lines:
                lines.extend(["", *self.selection.detail_lines])
            elif not self.selection.status_message:
                lines.extend(["", "No results saved for that fixture yet."])
        else:
            lines.append("Select a fixture to inspect or correct saved results.")
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
        return _render_panel_content(lines)


class CorrectResultsButton(discord.ui.Button):
    def __init__(
        self,
        parent_view: ResultsPanelView | UnifiedAdminPanelView,
        disabled: bool = False,
        row: int | None = None,
    ):
        self.parent_view = parent_view
        super().__init__(
            label="Correct Results",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            self.parent_view.selection.fixture_id = None
            self.parent_view.selection.fixture_label = ""
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = "Fixture no longer exists."
            await self.parent_view.load_fixture_options()
            load_user_options = getattr(self.parent_view, "load_user_options", None)
            if callable(load_user_options):
                await load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return
        results = await self.parent_view.db.get_results(fixture_id)
        if not results:
            await interaction.response.send_message(
                "No results are stored for that fixture yet. Use the Enter Results button in `/admin panel` first.",
                ephemeral=True,
            )
            return

        modal = CorrectResultsModal(self.parent_view, fixture, results)
        await interaction.response.send_modal(modal)
