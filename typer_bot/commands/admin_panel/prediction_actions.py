"""Prediction action buttons for admin panel views."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.services import (
    FixtureNotFoundError,
    NoPredictionsSavedError,
    PredictionDisappearedError,
    PredictionNotFoundError,
)

from .base import (
    _build_detail_lines,
    _notify_user_dm,
    _prediction_status_text,
    _render_panel_content,
)
from .modals import ReplacePredictionModal

if TYPE_CHECKING:
    from .predictions import PredictionsPanelView
    from .unified import UnifiedAdminPanelView


class ReplacePredictionButton(discord.ui.Button):
    def __init__(
        self,
        parent_view: PredictionsPanelView | UnifiedAdminPanelView,
        disabled: bool = False,
        row: int | None = None,
    ):
        self.parent_view = parent_view
        super().__init__(
            label="Replace Prediction",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        user_id = self.parent_view.selection.user_id
        if fixture_id is None or user_id is None:
            await interaction.response.send_message(
                "Select both fixture and user first.", ephemeral=True
            )
            return

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        prediction = await self.parent_view.db.get_prediction(
            fixture_id, user_id, self.parent_view.guild_id
        )
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
        self,
        parent_view: PredictionsPanelView | UnifiedAdminPanelView,
        disabled: bool = False,
        row: int | None = None,
    ):
        self.parent_view = parent_view
        super().__init__(
            label="View Predictions",
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        try:
            fixture, predictions = await self.parent_view.service.get_fixture_prediction_summary(
                fixture_id, self.parent_view.guild_id
            )
        except FixtureNotFoundError as exc:
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
        except NoPredictionsSavedError as exc:
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
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        lines = [f"**Week {fixture['week_number']} Predictions**"]
        for prediction in predictions:
            status = _prediction_status_text(prediction)
            scores = ", ".join(prediction["predictions"])
            lines.append(f"- {prediction['user_name']}: {scores} ({status})")

        await interaction.response.send_message(_render_panel_content(lines), ephemeral=True)


class ToggleWaiverButton(discord.ui.Button):
    """Toggle the late waiver flag and best-effort DM the affected user."""

    def __init__(
        self,
        parent_view: PredictionsPanelView | UnifiedAdminPanelView,
        disabled: bool = False,
        row: int | None = None,
    ):
        self.parent_view = parent_view
        super().__init__(
            label="Toggle Late Waiver",
            style=discord.ButtonStyle.success,
            disabled=disabled,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        user_id = self.parent_view.selection.user_id
        if fixture_id is None or user_id is None:
            await interaction.response.send_message(
                "Select both fixture and user first.", ephemeral=True
            )
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
            fixture_id, user_id, self.parent_view.guild_id
        )
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
            await self.parent_view.load_user_options()
            self.parent_view._refresh_items()
            await interaction.response.edit_message(
                content=self.parent_view.render_content(),
                view=self.parent_view,
            )
            return
        except (PredictionNotFoundError, PredictionDisappearedError) as exc:
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
        except ValueError as exc:
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

        status_line = "enabled" if prediction["late_penalty_waived"] else "disabled"
        notification = f"An admin {status_line} the late waiver for your Week {fixture['week_number']} prediction."
        if recalculation is not None:
            notification += " Scores were recalculated."
        await _notify_user_dm(
            self.parent_view.bot,
            prediction["user_id"],
            notification,
            context="late waiver update",
        )
