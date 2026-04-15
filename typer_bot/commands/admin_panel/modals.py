"""Admin panel modal interactions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.utils import APP_TZ, format_for_discord, is_admin, now, parse_line_predictions

from .base import _build_detail_lines, _format_prediction_line, _prediction_status_text

if TYPE_CHECKING:
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView
    from .unified import UnifiedAdminPanelView


MAX_GAMES = 100


def _default_fixture_deadline(current_time: datetime) -> datetime:
    days_until_friday = (4 - current_time.weekday()) % 7
    if days_until_friday == 0 and current_time.hour >= 18:
        days_until_friday = 7
    deadline = current_time + timedelta(days=days_until_friday)
    return deadline.replace(hour=18, minute=0, second=0, microsecond=0)


def _build_fixture_preview_text(week_number: int, games: list[str], deadline: datetime) -> str:
    lines = [f"**Week {week_number} Fixture Preview**", ""]
    lines.extend(f"{index}. {game}" for index, game in enumerate(games, 1))
    lines.extend(
        [
            "",
            f"**Deadline:** {format_for_discord(deadline, 'F')} ({format_for_discord(deadline, 'R')})",
        ]
    )
    return "\n".join(lines)


def _parse_fixture_games(games_text: str) -> list[str]:
    games = [line.strip() for line in games_text.strip().split("\n") if line.strip()]
    if len(games) > MAX_GAMES:
        raise ValueError(f"Too many games! (max {MAX_GAMES})")
    if not games:
        raise ValueError("No games provided! Please enter at least one fixture.")
    return games


def _parse_fixture_deadline(deadline_text: str, current_time: datetime) -> datetime:
    if not deadline_text.strip():
        return _default_fixture_deadline(current_time)

    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(deadline_text.strip(), fmt).replace(tzinfo=APP_TZ)
        except ValueError:
            continue

    raise ValueError(
        "Invalid date format. Use one of these formats:\n"
        "2024-02-15 18:00\n"
        "15.02.2024 18:00\n"
        "15/02/2024 18:00"
    )


class CreateFixtureConfirmView(discord.ui.View):
    """Confirm or cancel modal-based fixture creation.

    Confirm persists the fixture, posts the announcement, and attempts to create
    the predictions thread. The fixture may still be created even if the later
    Discord-side steps fail.
    """

    def __init__(
        self,
        db: Database,
        channel,
        owner_user_id: str,
        preview_week_number: int,
        games: list[str],
        deadline: datetime,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.channel = channel
        self.owner_user_id = owner_user_id
        self.preview_week_number = preview_week_number
        self.games = games
        self.deadline = deadline

    @discord.ui.button(label="Create Fixture", style=discord.ButtonStyle.green)
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

        fixture_id, allocated_week = await self.db.create_next_fixture(self.games, self.deadline)
        final_preview = _build_fixture_preview_text(allocated_week, self.games, self.deadline)

        created_text = f"**Week {allocated_week} Fixture Created!**\n\n{final_preview}"
        if allocated_week != self.preview_week_number:
            created_text += (
                f"\n\n⚠️ **Week number changed:** preview showed Week {self.preview_week_number} but "
                f"this was created as **Week {allocated_week}** because the fixture set "
                f"changed between steps."
            )

        await interaction.response.edit_message(content=created_text, view=None)

        try:
            announcement = await self.channel.send(
                f"**Week {allocated_week} Fixture is now open!**\n\n"
                f"{final_preview}\n\n"
                f"💬 **How to predict:**\n"
                f"• Reply in this thread with your scores (one per line)\n"
                f"• Or use `/predict` to fill a modal and post publicly here"
            )

            await self.db.update_fixture_announcement(
                fixture_id,
                message_id=str(announcement.id),
                channel_id=str(self.channel.id),
            )

            try:
                thread = await announcement.create_thread(
                    name=f"Week {allocated_week} Predictions",
                    auto_archive_duration=1440,
                )
                await thread.send(
                    "💬 **Post your predictions here!**\n"
                    "Reply with your scores (one per line or comma-separated).\n"
                    "Predictions are one-shot here. To change one, use `/predict`."
                )
            except Exception:
                await interaction.followup.send(
                    "⚠️ Fixture created but I couldn't create a prediction thread. Restore the thread before users can predict.",
                    ephemeral=True,
                )
        except Exception:
            await interaction.followup.send(
                "⚠️ Fixture created but I couldn't announce it in the channel. Please announce it manually.",
                ephemeral=True,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Fixture creation cancelled.",
            view=None,
        )


class CreateFixtureModal(discord.ui.Modal):
    """Collect fixture games and an optional deadline in one interaction.

    Blank deadline falls back to Friday 18:00 in the app timezone. Manual
    deadlines accept `YYYY-MM-DD HH:MM`, `DD.MM.YYYY HH:MM`, or
    `DD/MM/YYYY HH:MM`. Open fixtures show a warning in the preview, but do not
    block creation. Nothing is persisted until the confirm view succeeds.
    """

    def __init__(self, db: Database, channel, owner_user_id: str):
        super().__init__(title="Create Fixture")
        self.db = db
        self.channel = channel
        self.owner_user_id = owner_user_id
        self.games_input = discord.ui.TextInput(
            label="Games",
            style=discord.TextStyle.paragraph,
            placeholder="Team A - Team B\nTeam C - Team D",
            required=True,
            max_length=4000,
        )
        self.deadline_input = discord.ui.TextInput(
            label="Deadline",
            style=discord.TextStyle.short,
            placeholder="YYYY-MM-DD HH:MM or blank for Friday 18:00",
            required=False,
            max_length=32,
        )
        self.add_item(self.games_input)
        self.add_item(self.deadline_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        try:
            games = _parse_fixture_games(self.games_input.value)
            deadline = _parse_fixture_deadline(self.deadline_input.value, now())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        preview_week_number = await self.db.get_max_week_number() + 1
        preview = _build_fixture_preview_text(preview_week_number, games, deadline)
        open_fixtures = await self.db.get_open_fixtures()
        if open_fixtures:
            open_weeks = ", ".join(str(fixture["week_number"]) for fixture in open_fixtures)
            preview += (
                f"\n\n⚠️ **Warning:** Week(s) {open_weeks} are already open. "
                "Creating another fixture may overlap prediction windows."
            )

        view = CreateFixtureConfirmView(
            self.db,
            self.channel,
            self.owner_user_id,
            preview_week_number,
            games,
            deadline,
        )
        await interaction.response.send_message(
            f"{preview}\n\nCreate this fixture?",
            view=view,
            ephemeral=True,
        )


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
    """Collect corrected results input for a fixture from the admin panel.

    Successful submits update the parent panel inline, clear any stale selected
    prediction user, and may trigger score recalculation. If the fixture
    disappears mid-flow, the parent panel reloads its selectors before
    re-rendering the error state.
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
