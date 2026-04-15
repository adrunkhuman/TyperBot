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
    _build_detail_lines,
    _prediction_status_text,
    _render_panel_content,
)
from .modals import ReplacePredictionModal

if TYPE_CHECKING:
    from .unified import UnifiedAdminPanelView


class PredictionsPanelView(OwnerRestrictedView):
    """Panel for prediction lookup and override actions."""

    def __init__(self, db: Database, service: AdminService, owner_user_id: str):
        super().__init__(db, service, owner_user_id)
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
    """Select a user who already has a prediction for the chosen fixture."""

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
            )
            for prediction in ordered[:MAX_SELECT_OPTIONS]
        ]
        self.disabled = False

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No predictions available.", ephemeral=True)
            return

        selected_user_id = self.values[0]
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
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return

        prediction = await self.parent_view.db.get_prediction(fixture_id, selected_user_id)
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
            self.parent_view.selection.detail_lines = _build_detail_lines(
                fixture["games"], prediction["predictions"]
            )
            self.parent_view.selection.status_message = ""

        self.parent_view._refresh_items()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class ReplacePredictionButton(discord.ui.Button):
    def __init__(
        self, parent_view: PredictionsPanelView | UnifiedAdminPanelView, disabled: bool = False
    ):
        self.parent_view = parent_view
        super().__init__(
            label="Replace Prediction",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        user_id = self.parent_view.selection.user_id
        if fixture_id is None or user_id is None:
            await interaction.response.send_message(
                "Select both fixture and user first.", ephemeral=True
            )
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        prediction = await self.parent_view.db.get_prediction(fixture_id, user_id)
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
        if prediction is None:
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = "That prediction is no longer available."
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return

        modal = ReplacePredictionModal(self.parent_view, fixture, prediction)
        await interaction.response.send_modal(modal)


class ViewPredictionsButton(discord.ui.Button):
    def __init__(
        self, parent_view: PredictionsPanelView | UnifiedAdminPanelView, disabled: bool = False
    ):
        self.parent_view = parent_view
        super().__init__(
            label="View Predictions",
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        try:
            fixture, predictions = await self.parent_view.service.get_fixture_prediction_summary(
                fixture_id
            )
        except ValueError as exc:
            if str(exc) == "Fixture not found":
                self.parent_view.selection.fixture_id = None
                self.parent_view.selection.fixture_label = ""
                self.parent_view.selection.user_id = None
                self.parent_view.selection.user_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                await self.parent_view.load_fixture_options()
                await self.parent_view.load_user_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return
            if str(exc) == "No predictions saved for this fixture":
                self.parent_view.selection.user_id = None
                self.parent_view.selection.user_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                await self.parent_view.load_user_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return

            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        lines = [f"**Week {fixture['week_number']} Predictions**"]
        for prediction in predictions:
            status = _prediction_status_text(prediction)
            scores = ", ".join(prediction["predictions"])
            lines.append(f"- {prediction['user_name']}: {scores} ({status})")

        await interaction.response.send_message(_render_panel_content(lines), ephemeral=True)


class ToggleWaiverButton(discord.ui.Button):
    def __init__(
        self, parent_view: PredictionsPanelView | UnifiedAdminPanelView, disabled: bool = False
    ):
        self.parent_view = parent_view
        super().__init__(
            label="Toggle Late Waiver",
            style=discord.ButtonStyle.success,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        user_id = self.parent_view.selection.user_id
        if fixture_id is None or user_id is None:
            await interaction.response.send_message(
                "Select both fixture and user first.", ephemeral=True
            )
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
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return

        prediction = await self.parent_view.db.get_prediction(fixture_id, user_id)
        if prediction is None:
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = "That prediction is no longer available."
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return

        try:
            (
                fixture,
                prediction,
                recalculation,
            ) = await self.parent_view.service.toggle_late_penalty_waiver(
                fixture_id,
                user_id,
            )
        except ValueError as exc:
            if str(exc) == "Fixture not found":
                self.parent_view.selection.fixture_id = None
                self.parent_view.selection.fixture_label = ""
                self.parent_view.selection.user_id = None
                self.parent_view.selection.user_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                await self.parent_view.load_fixture_options()
                await self.parent_view.load_user_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return
            if str(exc) in {
                "Prediction not found for that user",
                "Prediction disappeared after waiver update",
            }:
                self.parent_view.selection.user_id = None
                self.parent_view.selection.user_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                await self.parent_view.load_user_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return

            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        status = "enabled" if prediction["late_penalty_waived"] else "disabled"
        self.parent_view.selection.status_message = (
            f"Late waiver {status} for {prediction['user_name']} in week {fixture['week_number']}."
        )
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        self.parent_view.selection.user_label = (
            f"{prediction['user_name']} ({_prediction_status_text(prediction)})"
        )
        self.parent_view.selection.detail_lines = _build_detail_lines(
            fixture["games"], prediction["predictions"]
        )

        await self.parent_view.load_user_options()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
