"""Prediction and result correction modals for the admin panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.services import (
    FixtureNotFoundError,
    PredictionDisappearedError,
    PredictionNotFoundError,
)
from typer_bot.utils import get_admin_permission_error

from .base import (
    _build_detail_lines,
    _build_indexed_detail_lines,
    _format_prediction_line,
    _notify_user_dm,
    _prediction_status_text,
)

if TYPE_CHECKING:
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView
    from .unified import UnifiedAdminPanelView


class ReplacePredictionModal(discord.ui.Modal):
    """Collect corrected prediction lines and trigger optional score recalculation.

    Successful submits also send a best-effort DM to the affected user. DM
    delivery failures are logged and do not block the admin action.
    """

    def __init__(
        self,
        parent_view: PredictionsPanelView | UnifiedAdminPanelView,
        fixture: dict,
        prediction: dict,
    ):
        super().__init__(title=f"Replace Week {fixture['week_number']} Prediction")
        self.parent_view = parent_view
        self.fixture = fixture
        self.prediction = prediction
        self.predictions_input = discord.ui.TextInput(
            label="Predictions",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default="\n".join(
                _format_prediction_line(index + 1, fixture["games"][index], result)
                for index, result in zip(
                    prediction["predicted_game_indexes"], prediction["predictions"], strict=False
                )
            ),
            required=True,
            max_length=4000,
        )
        self.add_item(self.predictions_input)

    async def on_submit(self, interaction: discord.Interaction):
        permission_error = await get_admin_permission_error(interaction, self.parent_view.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
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
                self.parent_view.guild_id,
            )
        except (FixtureNotFoundError, PredictionNotFoundError, PredictionDisappearedError) as exc:
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = str(exc)
            if isinstance(exc, FixtureNotFoundError):
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
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = f"Replaced {updated_prediction['user_name']}'s prediction in week {fixture['week_number']}."
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        self.parent_view.selection.user_label = (
            f"{updated_prediction['user_name']} ({_prediction_status_text(updated_prediction)})"
        )
        self.parent_view.selection.detail_lines = _build_indexed_detail_lines(
            updated_prediction["predicted_game_indexes"],
            fixture["games"],
            updated_prediction["predictions"],
        )

        await self.parent_view.load_user_options()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )

        notification_lines = [
            f"An admin updated your Week {fixture['week_number']} prediction.",
            "",
            *self.parent_view.selection.detail_lines,
        ]
        if recalculation is not None:
            notification_lines.extend(["", "Scores were recalculated."])
        await _notify_user_dm(
            self.parent_view.bot,
            updated_prediction["user_id"],
            "\n".join(notification_lines),
            context="prediction replacement",
        )


class CorrectResultsModal(discord.ui.Modal):
    """Collect corrected results input for a fixture from the admin panel.

    Successful submits update the parent panel inline, clear any stale selected
    prediction user, may trigger score recalculation, and send best-effort DMs
    to all participants for that fixture. If the fixture disappears mid-flow,
    the parent panel reloads its selectors before re-rendering the error state.
    """

    def __init__(
        self,
        parent_view: ResultsPanelView | UnifiedAdminPanelView,
        fixture: dict,
        results: list[str] | None,
    ):
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
        permission_error = await get_admin_permission_error(interaction, self.parent_view.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        try:
            (
                fixture,
                _results,
                recalculation,
            ) = await self.parent_view.service.correct_results(
                self.fixture["id"],
                self.results_input.value,
                self.parent_view.guild_id,
            )
        except FixtureNotFoundError as exc:
            self.parent_view.selection.fixture_id = None
            self.parent_view.selection.fixture_label = ""
            self.parent_view.selection.user_id = None
            self.parent_view.selection.user_label = ""
            self.parent_view.selection.detail_lines = []
            self.parent_view.selection.status_message = str(exc)
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
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.status_message = (
            f"Saved corrected results for week {fixture['week_number']}."
        )
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = _build_detail_lines(fixture["games"], _results)

        load_user_options = getattr(self.parent_view, "load_user_options", None)
        if callable(load_user_options):
            await load_user_options()
        self.parent_view._refresh_items()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )

        predictions = await self.parent_view.db.predictions.get_all_predictions(fixture["id"])
        dm_message_lines = [
            f"Results were corrected for Week {fixture['week_number']}.",
            "",
            *self.parent_view.selection.detail_lines,
        ]
        if recalculation is not None:
            dm_message_lines.extend(["", "Scores were recalculated."])
        dm_message = "\n".join(dm_message_lines)
        for prediction in predictions:
            await _notify_user_dm(
                self.parent_view.bot,
                prediction["user_id"],
                dm_message,
                context="results correction",
            )
