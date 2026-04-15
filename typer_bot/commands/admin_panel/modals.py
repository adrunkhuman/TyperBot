"""Admin panel modal interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.utils import is_admin, parse_line_predictions

from .base import _build_detail_lines, _format_prediction_line, _prediction_status_text

if TYPE_CHECKING:
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView


def _build_results_preview(week_number: int, games: list[str], results: list[str]) -> str:
    lines = [f"**Week {week_number} Results Preview**", ""]
    lines.extend(
        _format_prediction_line(index, game, result)
        for index, (game, result) in enumerate(zip(games, results, strict=False), 1)
    )
    return "\n".join(lines)


class EnterResultsConfirmView(discord.ui.View):
    """Confirm or cancel modal-based results entry.

    Confirm is owner-only, rechecks admin permission, and persists the parsed
    results only after the preview step succeeds.
    """

    def __init__(
        self,
        db: Database,
        fixture: dict,
        results: list[str],
        preview: str,
        owner_user_id: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.fixture = fixture
        self.results = results
        self.preview = preview
        self.owner_user_id = owner_user_id

    @discord.ui.button(label="Save Results", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
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
            await self.db.save_results(self.fixture["id"], self.results)
        except ValueError as exc:
            await interaction.response.edit_message(
                content=f"**Cannot save results:** {exc}", view=None
            )
            return

        await interaction.response.edit_message(
            content=f"**Results Saved!**\n\n{self.preview}\n\nUse `/admin results calculate` to calculate scores.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Results entry cancelled. Use `/admin results enter` to try again.",
            view=None,
        )


class EnterResultsModal(discord.ui.Modal):
    """Collect results for an open fixture without starting a DM session.

    Parse failures are returned ephemerally, and nothing is persisted until the
    follow-up confirm view succeeds.
    """

    def __init__(self, fixture: dict, db: Database):
        super().__init__(title=f"Enter Week {fixture['week_number']} Results")
        self.fixture = fixture
        self.db = db
        self.results_input = discord.ui.TextInput(
            label="Results",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default="\n".join(f"{game} 2:0" for game in fixture["games"]),
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

        results, errors = parse_line_predictions(self.results_input.value, self.fixture["games"])
        if errors:
            await interaction.response.send_message("\n".join(errors), ephemeral=True)
            return

        preview = _build_results_preview(
            self.fixture["week_number"], self.fixture["games"], results
        )
        view = EnterResultsConfirmView(
            self.db,
            self.fixture,
            results,
            preview,
            str(interaction.user.id),
        )
        await interaction.response.send_message(
            f"{preview}\n\nSave these results?",
            view=view,
            ephemeral=True,
        )


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
            if str(exc) in {
                "Fixture not found",
                "Prediction not found for that user",
                "Prediction disappeared after update",
            }:
                self.parent_view.selection.user_id = None
                self.parent_view.selection.user_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                if str(exc) == "Fixture not found":
                    self.parent_view.selection.fixture_id = None
                    self.parent_view.selection.fixture_label = ""
                    await self.parent_view.load_fixture_options()
                await self.parent_view.load_user_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return

            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = f"Replaced {updated_prediction['user_name']}'s prediction in week {fixture['week_number']}."
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        self.parent_view.selection.user_label = (
            f"{updated_prediction['user_name']} ({_prediction_status_text(updated_prediction)})"
        )
        self.parent_view.selection.detail_lines = _build_detail_lines(
            fixture["games"], updated_prediction["predictions"]
        )

        await self.parent_view.load_user_options()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class CorrectResultsModal(discord.ui.Modal):
    """Collect corrected results input for a fixture from the admin panel."""

    def __init__(self, parent_view: ResultsPanelView, fixture: dict, results: list[str] | None):
        super().__init__(title=f"Correct Week {fixture['week_number']} Results")
        self.parent_view = parent_view
        self.fixture = fixture
        self.results_input = discord.ui.TextInput(
            label="Results",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default=(
                "\n".join(
                    _format_prediction_line(index, game, result)
                    for index, (game, result) in enumerate(
                        zip(fixture["games"], results, strict=False),
                        1,
                    )
                )
                if results
                else None
            ),
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
            if str(exc) == "Fixture not found":
                self.parent_view.selection.fixture_id = None
                self.parent_view.selection.fixture_label = ""
                self.parent_view.selection.detail_lines = []
                self.parent_view.selection.status_message = str(exc)
                await self.parent_view.load_fixture_options()
                self.parent_view._refresh_items()
                await interaction.response.edit_message(
                    content=self.parent_view.render_content(),
                    view=self.parent_view,
                )
                return

            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = (
            f"Saved corrected results for week {fixture['week_number']}."
        )
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        self.parent_view.selection.detail_lines = _build_detail_lines(fixture["games"], _results)

        self.parent_view._refresh_items()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
