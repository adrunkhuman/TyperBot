"""Prediction-focused admin panel views."""

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
    _prediction_status_text,
)
from .modals import ReplacePredictionModal


class PredictionsPanelView(OwnerRestrictedView):
    """Panel for prediction lookup and override actions."""

    def __init__(self, db: Database, service: AdminService, owner_user_id: str):
        super().__init__(db, service, owner_user_id)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self)
        self.user_select = PredictionUserSelect(self)
        self.user_select.update_options([])
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(self.user_select)
        self.add_item(ViewPredictionsButton(self))
        self.add_item(ReplacePredictionButton(self))
        self.add_item(ToggleWaiverButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.service.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    async def load_user_options(self) -> None:
        if self.selection.fixture_id is None:
            self.user_select.update_options([])
            return

        predictions = await self.db.get_all_predictions(self.selection.fixture_id)
        self.user_select.update_options(predictions)

    def render_content(self) -> str:
        status = self.selection.status_message or (
            "Select a fixture, then pick a user to view or override a stored prediction."
        )
        return "**Admin Panel - Predictions**\n" + status


class PredictionUserSelect(discord.ui.Select):
    """Select a user who already has a prediction for the chosen fixture."""

    def __init__(self, parent_view: PredictionsPanelView):
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

        self.parent_view.selection.user_id = self.values[0]
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        prediction = await self.parent_view.db.get_prediction(fixture_id, self.values[0])
        if prediction is None:
            self.parent_view.selection.status_message = "Prediction no longer exists."
        else:
            self.parent_view.selection.status_message = (
                f"Selected {prediction['user_name']} ({_prediction_status_text(prediction)})."
            )

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class ViewPredictionsButton(discord.ui.Button):
    def __init__(self, parent_view: PredictionsPanelView):
        self.parent_view = parent_view
        super().__init__(label="View Predictions", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        try:
            (
                fixture,
                predictions,
            ) = await self.parent_view.service.get_fixture_prediction_summary(fixture_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        lines = [f"**Week {fixture['week_number']} Predictions**"]
        for prediction in predictions:
            status = _prediction_status_text(prediction)
            scores = ", ".join(prediction["predictions"])
            lines.append(f"- {prediction['user_name']}: {scores} ({status})")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class ReplacePredictionButton(discord.ui.Button):
    def __init__(self, parent_view: PredictionsPanelView):
        self.parent_view = parent_view
        super().__init__(label="Replace Prediction", style=discord.ButtonStyle.primary)

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
        if fixture is None or prediction is None:
            await interaction.response.send_message(
                "That prediction is no longer available.", ephemeral=True
            )
            return

        modal = ReplacePredictionModal(self.parent_view, fixture, prediction)
        await interaction.response.send_modal(modal)


class ToggleWaiverButton(discord.ui.Button):
    def __init__(self, parent_view: PredictionsPanelView):
        self.parent_view = parent_view
        super().__init__(label="Toggle Late Waiver", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        user_id = self.parent_view.selection.user_id
        if fixture_id is None or user_id is None:
            await interaction.response.send_message(
                "Select both fixture and user first.", ephemeral=True
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
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        status = "enabled" if prediction["late_penalty_waived"] else "disabled"
        self.parent_view.selection.status_message = (
            f"Late waiver {status} for {prediction['user_name']} in week {fixture['week_number']}."
        )
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        await self.parent_view.load_user_options()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
