"""Unified admin panel view."""

from __future__ import annotations

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
from .fixtures import FixturesDeleteButton
from .predictions import (
    PredictionUserSelect,
    ReplacePredictionButton,
    ToggleWaiverButton,
    ViewPredictionsButton,
)
from .results import CorrectResultsButton


class UnifiedAdminPanelView(OwnerRestrictedView):
    """Single admin panel containing fixture, prediction, and results actions.

    This is the exported default view for `/admin panel`. Call
    `load_fixture_options()` before the initial render so the fixture selector is
    populated. The unified layout keeps fixture and user selectors in one
    message, and falls back to `View Predictions` when a fixture has more than
    25 saved predictions and the user selector cannot show them all.
    """

    def __init__(
        self,
        db: Database,
        service: AdminService,
        owner_user_id: str,
        bot: discord.Client | None = None,
    ):
        super().__init__(db, service, owner_user_id, bot=bot)
        self.selection = PanelSelectionState()
        self.has_user_overflow = False
        self.fixture_select = FixtureSelect(self)
        self.user_select = PredictionUserSelect(self)
        self.user_select.update_options([])
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(self.user_select)
        self.add_item(FixturesDeleteButton(self, disabled=self.selection.fixture_id is None))
        self.add_item(
            ReplacePredictionButton(
                self,
                disabled=self.selection.fixture_id is None or self.selection.user_id is None,
            )
        )
        self.add_item(
            ToggleWaiverButton(
                self,
                disabled=self.selection.fixture_id is None or self.selection.user_id is None,
            )
        )
        self.add_item(CorrectResultsButton(self, disabled=self.selection.fixture_id is None))
        if self.has_user_overflow:
            self.add_item(ViewPredictionsButton(self, disabled=self.selection.fixture_id is None))

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    async def load_user_options(self) -> None:
        if self.selection.fixture_id is None:
            self.has_user_overflow = False
            self.user_select.update_options([])
            return

        predictions = await self.db.get_all_predictions(self.selection.fixture_id)
        self.has_user_overflow = len(predictions) > MAX_SELECT_OPTIONS
        self.user_select.update_options(predictions)

    async def populate_fixture_details(self, fixture: dict | None) -> None:
        self.selection.detail_lines = []
        if fixture is None:
            return

        results = await self.db.get_results(fixture["id"])
        if results:
            self.selection.detail_lines = _build_detail_lines(fixture["games"], results)

    def render_content(self) -> str:
        lines = ["**Admin Panel**"]
        if self.selection.fixture_label:
            header = f"Fixture: {self.selection.fixture_label}"
            if self.selection.user_label:
                header += f"  •  User: {self.selection.user_label}"
            lines.append(header)
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
            if self.selection.detail_lines:
                lines.extend(["", *self.selection.detail_lines])
            else:
                guidance = "Pick a user to inspect or override predictions, correct results, or delete the fixture."
                lines.extend(["", guidance])

            if self.has_user_overflow:
                lines.extend(
                    [
                        "",
                        "More than 25 users predicted this fixture. Use View Predictions for the full list.",
                    ]
                )
        else:
            lines.append(
                "Select a fixture to inspect predictions, correct results, toggle waivers, or delete it."
            )
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
        return _render_panel_content(lines)
