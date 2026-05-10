"""Prediction-focused admin panel views."""

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
    _build_indexed_detail_lines,
    _prediction_status_text,
    _render_panel_content,
)
from .prediction_actions import ReplacePredictionButton, ToggleWaiverButton, ViewPredictionsButton

if TYPE_CHECKING:
    from .unified import UnifiedAdminPanelView


class PredictionsPanelView(OwnerRestrictedView):
    """Panel for prediction lookup and override actions."""

    def __init__(self, db: Database, service: AdminService, owner_user_id: str, guild_id: str):
        super().__init__(db, service, owner_user_id, guild_id)
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
        if self.has_user_overflow:
            self.add_item(ViewPredictionsButton(self, disabled=self.selection.fixture_id is None))
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

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_recent_fixtures(self.guild_id, MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    async def load_user_options(self) -> None:
        if self.selection.fixture_id is None:
            self.has_user_overflow = False
            self.user_select.update_options([])
            return

        predictions = await self.db.get_all_predictions(
            self.selection.fixture_id, include_pending=True
        )
        self.has_user_overflow = len(predictions) > MAX_SELECT_OPTIONS
        self.user_select.update_options(predictions)

    def render_content(self) -> str:
        lines = ["**Admin Panel - Predictions**"]
        if self.selection.fixture_label:
            header = f"Fixture: {self.selection.fixture_label}"
            if self.selection.user_label:
                header += f"  •  User: {self.selection.user_label}"
            lines.append(header)
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
            if self.selection.detail_lines:
                lines.extend(["", *self.selection.detail_lines])
            elif self.user_select.disabled:
                lines.extend(["", "No predictions saved for this fixture yet."])
            elif self.selection.user_id is None:
                lines.extend(["", "Pick a user to inspect or override a stored prediction."])

            if self.has_user_overflow:
                lines.extend(
                    [
                        "",
                        "More than 25 users predicted this fixture. Use View Predictions for the full list.",
                    ]
                )
        else:
            lines.append(
                "Select a fixture, then pick a user to inspect or override a stored prediction."
            )
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
        return _render_panel_content(lines)


class PredictionUserSelect(discord.ui.Select):
    """Select a user who already has a prediction for the chosen fixture.

    Refreshed option lists re-mark the active user as `default` so Discord keeps
    the visible selection after panel re-renders.
    """

    def __init__(self, parent_view: PredictionsPanelView | UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(
            placeholder="Select user",
            min_values=1,
            max_values=1,
            disabled=True,
        )

    def update_options(self, predictions: list[dict]) -> None:
        if not predictions:
            self.options = [discord.SelectOption(label="No predictions available", value="none")]
            self.disabled = True
            return

        ordered = sorted(predictions, key=lambda prediction: prediction["user_name"].lower())
        self.options = [
            discord.SelectOption(
                label=prediction["user_name"][:100],
                value=prediction["user_id"],
                description=_prediction_status_text(prediction)[:100],
                default=self.parent_view.selection.user_id == prediction["user_id"],
            )
            for prediction in ordered[:MAX_SELECT_OPTIONS]
        ]
        self.disabled = False

    def sync_selected_option(self) -> None:
        selected_value = self.parent_view.selection.user_id
        for option in self.options:
            option.default = option.value == selected_value

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No predictions available.", ephemeral=True)
            return

        selected_user_id = self.values[0]
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        if fixture is None:
            self.parent_view.selection.fixture_id = None
            self.parent_view.selection.fixture_label = ""
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = "Fixture no longer exists."
            await self.parent_view.load_fixture_options()
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return

        prediction = await self.parent_view.db.get_prediction(
            fixture_id, selected_user_id, self.parent_view.guild_id
        )
        if prediction is None:
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = "Prediction no longer exists."
            await self.parent_view.load_user_options()
        else:
            self.parent_view.selection.user_id = selected_user_id
            self.parent_view.selection.user_label = (
                f"{prediction['user_name']} ({_prediction_status_text(prediction)})"
            )
            self.parent_view.selection.detail_lines = _build_indexed_detail_lines(
                prediction["predicted_game_indexes"],
                fixture["games"],
                prediction["predictions"],
            )
            self.parent_view.selection.status_message = ""

        set_selected_prediction = getattr(self.parent_view, "set_selected_prediction", None)
        if callable(set_selected_prediction):
            await set_selected_prediction()

        self.sync_selected_option()

        self.parent_view._refresh_items()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
