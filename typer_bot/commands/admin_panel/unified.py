"""Unified admin panel view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService
from typer_bot.utils import format_standings, has_setup_permission, now

from .base import (
    MAX_SELECT_OPTIONS,
    FixtureSelect,
    OwnerRestrictedView,
    PanelSelectionState,
    _build_detail_lines,
    _build_indexed_detail_lines,
    _notify_user_dm,
    _render_panel_content,
    _update_public_review_marker,
)
from .fixtures import FixturesDeleteButton
from .modals import CreateFixtureModal, EnterResultsModal
from .predictions import (
    PredictionUserSelect,
    ReplacePredictionButton,
    ToggleWaiverButton,
    ViewPredictionsButton,
    _prediction_status_text,
)
from .results import CorrectResultsButton


class SetupBotButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Setup TyperBot", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        from typer_bot.commands.admin_commands import GuildSetupPromptView

        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server admin can configure TyperBot for this server.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Update TyperBot setup for this server.",
            view=GuildSetupPromptView(self.parent_view.db, str(interaction.user.id)),
            ephemeral=True,
        )


class ApprovePartialButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Approve Late", style=discord.ButtonStyle.success, row=4)

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
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Reject Late", style=discord.ButtonStyle.danger, row=4)

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
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Review Late", style=discord.ButtonStyle.primary, row=4)

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


if TYPE_CHECKING:
    from typer_bot.commands.admin_commands import AdminCommands


class CreateFixtureButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Create Fixture", style=discord.ButtonStyle.success, row=2)

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if interaction.guild is None or channel is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if not isinstance(parent, discord.TextChannel):
                await interaction.response.send_message(
                    "Fixture creation must be started from a server text channel.", ephemeral=True
                )
                return
            channel = parent
        elif not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Fixture creation must be started from a server text channel.", ephemeral=True
            )
            return

        modal = CreateFixtureModal(
            self.parent_view.db,
            channel,
            str(interaction.user.id),
            self.parent_view.bot,
        )
        await interaction.response.send_modal(modal)


class JumpToWeekModal(discord.ui.Modal):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        super().__init__(title="Jump To Week")
        self.parent_view = parent_view
        self.week_input = discord.ui.TextInput(
            label="Week Number",
            placeholder="e.g. 12",
            required=True,
            max_length=8,
        )
        self.add_item(self.week_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            week_number = int(self.week_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Week number must be a whole number.", ephemeral=True
            )
            return

        open_fixtures = await self.parent_view.db.get_open_fixtures(self.parent_view.guild_id)
        matching = [fixture for fixture in open_fixtures if fixture["week_number"] == week_number]
        if not matching:
            await interaction.response.send_message(
                f"No open fixture found for week {week_number}.", ephemeral=True
            )
            return
        if len(matching) > 1:
            await interaction.response.send_message(
                f"More than one open fixture was found for week {week_number}. Resolve duplicate week numbers first.",
                ephemeral=True,
            )
            return

        fixture = matching[0]
        self.parent_view.selection.fixture_id = fixture["id"]
        self.parent_view.selection.fixture_label = (
            f"Week {fixture['week_number']} [{fixture['status'].upper()}]"
        )
        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = []
        self.parent_view.selection.status_message = ""
        await self.parent_view.populate_fixture_details(fixture)
        await self.parent_view.load_user_options()
        self.parent_view.fixture_select.sync_selected_option()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class JumpToWeekButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Jump To Week", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(JumpToWeekModal(self.parent_view))


class EnterResultsButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(
            label="Enter Results",
            style=discord.ButtonStyle.secondary,
            disabled=parent_view.selection.fixture_id is None,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return
        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "That fixture is no longer open.", ephemeral=True
            )
            return
        existing_results = await self.parent_view.db.get_results(fixture_id)
        if existing_results:
            await interaction.response.send_message(
                "Results already entered for this fixture. Use Correct Results instead.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EnterResultsModal(fixture, self.parent_view.db))


class CalculateScoresButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(
            label="Calculate Scores",
            style=discord.ButtonStyle.secondary,
            disabled=parent_view.selection.fixture_id is None,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return
        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "That fixture is no longer open.", ephemeral=True
            )
            return

        admin_commands = self.parent_view.admin_commands
        if admin_commands is None:
            await interaction.response.send_message(
                "Calculate Scores is unavailable in this context.", ephemeral=True
            )
            return
        user_id = str(interaction.user.id)
        current_time = now().timestamp()
        remaining = admin_commands.get_calculate_cooldown_remaining(
            self.parent_view.guild_id,
            user_id,
            current_time=current_time,
            cooldown_seconds=30.0,
        )
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {remaining:.1f}s before calculating again.", ephemeral=True
            )
            return

        try:
            score_result = await self.parent_view.service.calculate_fixture_scores(
                fixture_id, self.parent_view.guild_id
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        admin_commands.record_calculate_cooldown(
            self.parent_view.guild_id, user_id, current_time=current_time
        )
        await admin_commands._create_backup()
        await admin_commands._post_calculation_to_channel(interaction, score_result)


class PostResultsConfirmView(discord.ui.View):
    def __init__(
        self,
        fixture_data: dict,
        standings: list[dict],
        channel: discord.TextChannel,
    ):
        super().__init__(timeout=60)
        self.fixture_data = fixture_data
        self.standings = standings
        self.channel = channel

    @discord.ui.button(label="No Mentions", style=discord.ButtonStyle.primary)
    async def no_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        try:
            await interaction.response.edit_message(
                content="Results posted without mentions!", view=None
            )
        except Exception:
            return
        try:
            await self.channel.send(message)
        except Exception as exc:
            await interaction.followup.send(f"Failed to post results: {exc}")

    @discord.ui.button(label="Mention Users", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        mentions = [f"<@{score['user_id']}>" for score in self.fixture_data["scores"]]
        message += f"\n\n**Participants:**\n{' '.join(mentions)}"
        try:
            await interaction.response.edit_message(
                content="Results posted with mentions!", view=None
            )
        except Exception:
            return
        try:
            await self.channel.send(message)
        except Exception as exc:
            await interaction.followup.send(f"Failed to post results: {exc}")


class PostResultsButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        self.parent_view = parent_view
        super().__init__(label="Re-post Results", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction: discord.Interaction):
        fixture_data = await self.parent_view.db.get_last_fixture_scores(self.parent_view.guild_id)
        standings = await self.parent_view.db.get_standings(self.parent_view.guild_id)
        if not fixture_data:
            await interaction.response.send_message(
                "No completed fixtures found with scores!", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This action can only be used in text channels.", ephemeral=True
            )
            return
        preview = format_standings(standings, fixture_data)
        view = PostResultsConfirmView(fixture_data, standings, interaction.channel)
        await interaction.response.send_message(
            f"{preview}\n\nMention users in this post?",
            view=view,
            ephemeral=True,
        )


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
        guild_id: str,
        admin_commands: AdminCommands | None = None,
        bot: discord.Client | None = None,
    ):
        super().__init__(db, service, owner_user_id, guild_id, bot=bot)
        self.admin_commands = admin_commands
        self.selection = PanelSelectionState()
        self.has_user_overflow = False
        self.has_pending_partials = False
        self.current_prediction: dict | None = None
        self.fixture_select = FixtureSelect(self)
        self.user_select = PredictionUserSelect(self)
        self.user_select.update_options([])
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(self.user_select)
        self.add_item(CreateFixtureButton(self))
        self.add_item(FixturesDeleteButton(self, disabled=self.selection.fixture_id is None, row=2))
        self.add_item(SetupBotButton(self))
        self.add_item(JumpToWeekButton(self))
        if self.has_pending_partials:
            self.add_item(ReviewPendingPartialsButton(self))
        self.add_item(EnterResultsButton(self))
        self.add_item(CalculateScoresButton(self))
        self.add_item(CorrectResultsButton(self, disabled=self.selection.fixture_id is None, row=3))
        self.add_item(PostResultsButton(self))
        if self.current_prediction and self.current_prediction.get("pending_partial_approval"):
            self.add_item(ApprovePartialButton(self))
            self.add_item(RejectPartialButton(self))
        else:
            self.add_item(
                ReplacePredictionButton(
                    self,
                    disabled=self.selection.fixture_id is None or self.selection.user_id is None,
                    row=4,
                )
            )
            self.add_item(
                ToggleWaiverButton(
                    self,
                    disabled=self.selection.fixture_id is None or self.selection.user_id is None,
                    row=4,
                )
            )
        if self.has_user_overflow:
            self.add_item(
                ViewPredictionsButton(self, disabled=self.selection.fixture_id is None, row=4)
            )

    async def load_fixture_options(self) -> None:
        fixtures = await self.db.get_recent_fixtures(self.guild_id, MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)
        self.has_pending_partials = bool(
            await self.db.get_pending_partial_predictions(self.guild_id)
        )
        self._refresh_items()

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

    async def set_selected_prediction(self) -> None:
        self.current_prediction = None
        if self.selection.fixture_id is None or self.selection.user_id is None:
            return
        fixture = await self.db.get_fixture_by_id(self.selection.fixture_id, self.guild_id)
        if fixture is None:
            return
        self.current_prediction = await self.db.get_prediction(
            self.selection.fixture_id, self.selection.user_id, self.guild_id
        )

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
                guidance = "Top row: fixture management. Middle row: results workflow. Bottom row: prediction and late-review actions. Use Jump To Week when the older open week you want is not in the quick list."
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
                "Use the top row for fixture management, the middle row for results, and the bottom row for prediction and late-review actions."
            )
            if self.selection.status_message:
                lines.extend(["", self.selection.status_message])
        return _render_panel_content(lines)
