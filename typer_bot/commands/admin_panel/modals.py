"""Admin panel modal interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.utils import is_admin

from .base import _format_prediction_line

if TYPE_CHECKING:
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView


class ReplacePredictionModal(discord.ui.Modal):
    """Collect corrected prediction lines and trigger optional score recalculation."""

    def __init__(self, parent_view: PredictionsPanelView, fixture: dict, prediction: dict):
        super().__init__(title=f"Replace Week {fixture['week_number']} Prediction")
        self.parent_view = parent_view
        self.fixture = fixture
        self.prediction = prediction
        self.predictions_input = discord.ui.TextInput(
            label="Predictions",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default="\n".join(
                _format_prediction_line(index, game, result)
                for index, (game, result) in enumerate(
                    zip(fixture["games"], prediction["predictions"], strict=False),
                    1,
                )
            ),
            required=True,
            max_length=4000,
        )
        self.add_item(self.predictions_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        try:
            (
                fixture,
                updated_prediction,
                recalculation,
            ) = await self.parent_view.service.replace_prediction(
                self.fixture["id"],
                self.prediction["user_id"],
                self.predictions_input.value,
                str(interaction.user.id),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = f"Replaced {updated_prediction['user_name']}'s prediction in week {fixture['week_number']}."
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        await self.parent_view.load_user_options()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class CorrectResultsModal(discord.ui.Modal):
    """Collect corrected results input for a fixture from the admin panel."""

    def __init__(self, parent_view: ResultsPanelView, fixture: dict):
        super().__init__(title=f"Correct Week {fixture['week_number']} Results")
        self.parent_view = parent_view
        self.fixture = fixture
        self.results_input = discord.ui.TextInput(
            label="Results",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            required=True,
            max_length=4000,
        )
        self.add_item(self.results_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        try:
            (
                fixture,
                _results,
                recalculation,
            ) = await self.parent_view.service.correct_results(
                self.fixture["id"],
                self.results_input.value,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = (
            f"Saved corrected results for week {fixture['week_number']}."
        )
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
