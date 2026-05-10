"""Late partial prediction review controls for the unified admin panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .base import (
    _build_indexed_detail_lines,
    _notify_user_dm,
    _prediction_status_text,
    _update_public_review_marker,
)

if TYPE_CHECKING:
    from .unified import UnifiedAdminPanelView


class ApprovePartialButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Approve Late", style=discord.ButtonStyle.success, row=row)

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
            ) = await self.parent_view.service.approve_partial_prediction(
                fixture_id,
                user_id,
                str(interaction.user.id),
                self.parent_view.guild_id,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.user_label = (
            f"{prediction['user_name']} ({_prediction_status_text(prediction)})"
        )
        self.parent_view.selection.detail_lines = _build_indexed_detail_lines(
            prediction["predicted_game_indexes"],
            fixture["games"],
            prediction["predictions"],
        )
        self.parent_view.selection.status_message = f"Approved late prediction for {prediction['user_name']} in week {fixture['week_number']}."
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        await self.parent_view.load_user_options()
        self.parent_view.current_prediction = prediction
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(), view=self.parent_view
        )
        await _update_public_review_marker(
            self.parent_view.bot,
            fixture,
            prediction,
            approved=True,
        )

        notification = (
            f"Your late prediction for Week {fixture['week_number']} was approved by an admin."
        )
        if recalculation is not None:
            notification += " Scores were recalculated."
        await _notify_user_dm(
            self.parent_view.bot,
            prediction["user_id"],
            notification,
            context="partial approval",
        )


class RejectPartialButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Reject Late", style=discord.ButtonStyle.danger, row=row)

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
            ) = await self.parent_view.service.reject_partial_prediction(
                fixture_id,
                user_id,
                self.parent_view.guild_id,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = []
        self.parent_view.selection.status_message = f"Rejected late prediction for {prediction['user_name']} in week {fixture['week_number']}."
        if recalculation is not None:
            self.parent_view.selection.status_message += " Scores were recalculated."

        await self.parent_view.load_user_options()
        self.parent_view.current_prediction = None
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(), view=self.parent_view
        )
        await _update_public_review_marker(
            self.parent_view.bot,
            fixture,
            prediction,
            approved=False,
        )

        notification = (
            f"Your late prediction for Week {fixture['week_number']} was rejected by an admin."
        )
        if recalculation is not None:
            notification += " Scores were recalculated."
        await _notify_user_dm(
            self.parent_view.bot,
            prediction["user_id"],
            notification,
            context="partial rejection",
        )


class ReviewPendingPartialsButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Review Late", style=discord.ButtonStyle.primary, row=row)

    async def callback(self, interaction: discord.Interaction):
        pending_predictions = await self.parent_view.db.get_pending_partial_predictions(
            self.parent_view.guild_id
        )
        if not pending_predictions:
            await interaction.response.send_message(
                "There are no late predictions awaiting review right now.", ephemeral=True
            )
            return

        current_key = (self.parent_view.selection.fixture_id, self.parent_view.selection.user_id)
        next_prediction = pending_predictions[0]
        for index, pending in enumerate(pending_predictions):
            if (pending["fixture_id"], pending["user_id"]) == current_key:
                next_prediction = pending_predictions[(index + 1) % len(pending_predictions)]
                break

        fixture = await self.parent_view.db.get_fixture_by_id(
            next_prediction["fixture_id"], self.parent_view.guild_id
        )
        if fixture is None:
            await interaction.response.send_message(
                "That fixture no longer exists. Try again after refreshing the panel.",
                ephemeral=True,
            )
            return

        self.parent_view.selection.fixture_id = fixture["id"]
        self.parent_view.selection.fixture_label = (
            f"Week {fixture['week_number']} [{fixture['status'].upper()}]"
        )
        self.parent_view.selection.user_id = next_prediction["user_id"]
        self.parent_view.selection.user_label = (
            f"{next_prediction['user_name']} ({_prediction_status_text(next_prediction)})"
        )
        self.parent_view.selection.detail_lines = _build_indexed_detail_lines(
            next_prediction["predicted_game_indexes"],
            fixture["games"],
            next_prediction["predictions"],
        )
        self.parent_view.selection.status_message = f"Reviewing late prediction for {next_prediction['user_name']} in week {fixture['week_number']}."
        await self.parent_view.load_user_options()
        await self.parent_view.set_selected_prediction()
        self.parent_view.fixture_select.sync_selected_option()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
