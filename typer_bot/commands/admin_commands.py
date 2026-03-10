"""Admin Discord commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.handlers import FixtureCreationHandler, ResultsEntryHandler
from typer_bot.services import AdminService
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import format_standings, is_admin, is_admin_member, now
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

# Rate limiting: user_id -> timestamp
_calculate_cooldowns: dict[str, float] = {}
CALCULATE_COOLDOWN = 30.0
COOLDOWN_EXPIRY_HOURS = 1
MAX_SELECT_OPTIONS = 25

logger = logging.getLogger(__name__)


def _cleanup_expired_cooldowns() -> None:
    """Remove cooldown entries older than COOLDOWN_EXPIRY_HOURS."""
    current_time = now().timestamp()
    cutoff = current_time - (COOLDOWN_EXPIRY_HOURS * 3600)
    expired = [uid for uid, ts in list(_calculate_cooldowns.items()) if ts < cutoff]
    for uid in expired:
        _calculate_cooldowns.pop(uid, None)


def admin_only():
    """Decorator to check if user has admin permissions."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return False
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You don't have permission to use admin commands.", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


def _fixture_select_label(fixture: dict) -> str:
    status = fixture["status"].upper()
    return f"Week {fixture['week_number']} [{status}]"


def _fixture_select_description(fixture: dict) -> str:
    games_count = len(fixture["games"])
    return f"{games_count} matches"


def _format_prediction_line(index: int, game: str, prediction: str) -> str:
    return f"{index}. {game} {prediction}"


def _prediction_status_text(prediction: dict) -> str:
    if not prediction["is_late"]:
        return "on time"
    if prediction["late_penalty_waived"]:
        return "late, waiver active"
    return "late, penalty active"


@dataclass(slots=True)
class PanelSelectionState:
    """Shared selection state for admin panel flows."""

    fixture_id: int | None = None
    user_id: str | None = None
    status_message: str = ""


class AdminCommands(commands.Cog):
    """Commands for admins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db  # type: ignore
        self.service = AdminService(self.db)
        self.fixture_handler = FixtureCreationHandler(bot, self.db)
        self.results_handler = ResultsEntryHandler(bot, self.db)

    @staticmethod
    def _format_open_weeks(open_fixtures: list[dict]) -> str:
        return ", ".join(str(fixture["week_number"]) for fixture in open_fixtures)

    async def _resolve_open_fixture(
        self,
        interaction: discord.Interaction,
        week: int | None,
        command_example: str,
    ) -> dict | None:
        open_fixtures = await self.db.get_open_fixtures()

        if not open_fixtures:
            await interaction.response.send_message("No open fixtures found!", ephemeral=True)
            return None

        if week is None:
            if len(open_fixtures) == 1:
                return open_fixtures[0]

            open_weeks = self._format_open_weeks(open_fixtures)
            await interaction.response.send_message(
                "Multiple fixtures are currently open. "
                "Please specify the `week` argument to choose one.\n"
                f"Open weeks: {open_weeks}\n"
                f"Example: `{command_example}`",
                ephemeral=True,
            )
            return None

        matching = [fixture for fixture in open_fixtures if fixture["week_number"] == week]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            await interaction.response.send_message(
                f"More than one open fixture was found for week {week}. "
                "Please resolve duplicate week numbers in the database before continuing.",
                ephemeral=True,
            )
            return None

        open_weeks = self._format_open_weeks(open_fixtures)
        await interaction.response.send_message(
            f"No open fixture found for week {week}.\nOpen weeks: {open_weeks}",
            ephemeral=True,
        )
        return None

    async def _create_backup(self) -> None:
        try:
            await self.bot.loop.run_in_executor(
                None, lambda: create_backup(self.db.db_path, BACKUP_DIR)
            )
            await self.bot.loop.run_in_executor(
                None, lambda: cleanup_old_backups(BACKUP_DIR, keep=10)
            )
        except Exception as exc:
            logger.warning(f"Backup failed but calculation succeeded: {exc}")

    async def _post_calculation_to_channel(
        self,
        interaction: discord.Interaction,
        score_result: FixtureScoreResult,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
            await interaction.response.send_message(
                "Could not find channel to post in.", ephemeral=True
            )
            return

        message = format_standings(score_result.standings, score_result.last_fixture)

        try:
            await channel.send(message)
            await interaction.response.send_message(
                f"Week {score_result.fixture['week_number']} results calculated and posted!",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error(f"Failed to post results to channel: {exc}")
            await interaction.response.send_message(
                "Scores calculated but failed to post to channel.", ephemeral=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)

        if self.fixture_handler.has_session(user_id):
            await self.fixture_handler.handle_dm(message, user_id, is_admin_member)
            return

        if self.results_handler.has_session(user_id):
            await self.results_handler.handle_dm(message, user_id, is_admin_member)
            return

    admin = app_commands.Group(name="admin", description="Admin commands for managing fixtures")
    fixture = app_commands.Group(name="fixture", description="Manage fixtures", parent=admin)
    results = app_commands.Group(name="results", description="Manage results", parent=admin)

    @admin.command(name="panel", description="Open the admin management panel")
    @admin_only()
    async def panel(self, interaction: discord.Interaction):
        view = AdminPanelHomeView(self, str(interaction.user.id))
        await interaction.response.send_message(
            "**Admin Panel**\nChoose the workflow you want to manage.",
            view=view,
            ephemeral=True,
        )

    @fixture.command(name="create", description="Create a new fixture (DM workflow)")
    @admin_only()
    async def fixture_create(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if interaction.channel_id is None or interaction.guild_id is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        self.fixture_handler.start_session(user_id, interaction.channel_id, interaction.guild_id)

        await interaction.response.send_message(
            "Check your DMs! I've sent you instructions for creating the fixture.",
            ephemeral=True,
        )

        try:
            await interaction.user.send(
                "**Create New Fixture**\n\n"
                "Step 1/2: Send me the list of games in this format:\n"
                "```\n"
                "Team A - Team B\n"
                "Team C - Team D\n"
                "Team E - Team F\n"
                "...\n"
                "```\n"
                "One game per line."
            )
        except discord.Forbidden:
            self.fixture_handler.cancel_session(user_id, reason="dm_forbidden")
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @fixture.command(name="delete", description="Delete an open fixture")
    @admin_only()
    async def fixture_delete(self, interaction: discord.Interaction, week: int | None = None):
        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin fixture delete week:12",
        )
        if not fixture:
            return

        view = DeleteConfirmView(
            self.db,
            str(interaction.user.id),
            fixture["id"],
            fixture["week_number"],
        )

        lines = [f"**Delete Week {fixture['week_number']}?**\n"]
        for index, game in enumerate(fixture["games"], 1):
            lines.append(f"{index}. {game}")

        await interaction.response.send_message(
            "\n".join(lines)
            + "\n\nThis will delete the fixture and all associated predictions. Are you sure?",
            view=view,
            ephemeral=True,
        )

    @results.command(name="enter", description="Enter results for an open fixture (DM workflow)")
    @admin_only()
    async def results_enter(self, interaction: discord.Interaction, week: int | None = None):
        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin results enter week:12",
        )
        if not fixture:
            return

        existing_results = await self.db.get_results(fixture["id"])
        if existing_results:
            await interaction.response.send_message(
                "Results already entered for this fixture. "
                "Use `/admin panel` to correct them or `/admin results calculate` to post scores.",
                ephemeral=True,
            )
            return

        user_id = str(interaction.user.id)
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        self.results_handler.start_session(user_id, fixture["id"], interaction.guild_id)

        await interaction.response.send_message(
            "Check your DMs! I've sent you instructions for entering results.",
            ephemeral=True,
        )

        try:
            lines = [
                f"**Week {fixture['week_number']} - Enter Results**",
                "",
                "Reply with the actual results in this format:",
                "```",
            ]
            for game in fixture["games"]:
                lines.append(f"{game} 2:0")
            lines.extend(
                [
                    "```",
                    "",
                    "Add the actual score (e.g., 2:0 or 2-1) at the end of each line.",
                    "Type 'x' for cancelled or postponed games.",
                ]
            )
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            self.results_handler.cancel_session(user_id, reason="dm_forbidden")
            await interaction.followup.send(
                "I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @results.command(name="calculate", description="Calculate scores and post results")
    @admin_only()
    async def results_calculate(self, interaction: discord.Interaction, week: int | None = None):
        _cleanup_expired_cooldowns()

        user_id = str(interaction.user.id)
        current_time = now().timestamp()
        if user_id in _calculate_cooldowns:
            remaining = CALCULATE_COOLDOWN - (current_time - _calculate_cooldowns[user_id])
            if remaining > 0:
                await interaction.response.send_message(
                    f"Please wait {remaining:.1f}s before calculating again.",
                    ephemeral=True,
                )
                return

        fixture = await self._resolve_open_fixture(
            interaction,
            week,
            "/admin results calculate week:12",
        )
        if not fixture:
            return

        _calculate_cooldowns[user_id] = current_time

        try:
            score_result = await self.service.calculate_fixture_scores(fixture["id"])
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self._create_backup()
        await self._post_calculation_to_channel(interaction, score_result)

    @results.command(name="post", description="Post results with optional user mentions")
    @admin_only()
    async def results_post(self, interaction: discord.Interaction):
        fixture_data = await self.db.get_last_fixture_scores()
        standings = await self.db.get_standings()

        if not fixture_data:
            await interaction.response.send_message(
                "No completed fixtures found with scores!", ephemeral=True
            )
            return

        preview = format_standings(standings, fixture_data)

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in text channels.", ephemeral=True
            )
            return

        view = PostResultsConfirmView(self.db, fixture_data, standings, interaction.channel)
        await interaction.response.send_message(
            f"{preview}\n\nMention users in this post?",
            view=view,
            ephemeral=True,
        )


class OwnerRestrictedView(discord.ui.View):
    """View base class that restricts interactions to one admin."""

    def __init__(self, admin_cog: AdminCommands, owner_user_id: str, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.admin_cog = admin_cog
        self.owner_user_id = owner_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return False
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return False
        return True


class AdminPanelHomeView(OwnerRestrictedView):
    @discord.ui.button(label="Fixtures", style=discord.ButtonStyle.secondary)
    async def fixtures(self, interaction: discord.Interaction, _button: discord.ui.Button):
        view = FixturesPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)

    @discord.ui.button(label="Predictions", style=discord.ButtonStyle.primary)
    async def predictions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        view = PredictionsPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)

    @discord.ui.button(label="Results", style=discord.ButtonStyle.success)
    async def results(self, interaction: discord.Interaction, _button: discord.ui.Button):
        view = ResultsPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)


class FixturesPanelView(OwnerRestrictedView):
    """Panel for fixture deletion and workflow guidance."""

    def __init__(self, admin_cog: AdminCommands, owner_user_id: str):
        super().__init__(admin_cog, owner_user_id)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self, open_only=True)
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(FixturesDeleteButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.admin_cog.db.get_open_fixtures()
        self.fixture_select.update_options(fixtures)

    def render_content(self) -> str:
        status = (
            self.selection.status_message
            or "Select an open fixture to delete, or use `/admin fixture create` for new fixtures."
        )
        return "**Admin Panel - Fixtures**\n" + status


class PredictionsPanelView(OwnerRestrictedView):
    """Panel for prediction lookup and override actions."""

    def __init__(self, admin_cog: AdminCommands, owner_user_id: str):
        super().__init__(admin_cog, owner_user_id)
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
        fixtures = await self.admin_cog.service.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    async def load_user_options(self) -> None:
        if self.selection.fixture_id is None:
            self.user_select.update_options([])
            return
        predictions = await self.admin_cog.db.get_all_predictions(self.selection.fixture_id)
        self.user_select.update_options(predictions)

    def render_content(self) -> str:
        status = self.selection.status_message or (
            "Select a fixture, then pick a user to view or override a stored prediction."
        )
        return "**Admin Panel - Predictions**\n" + status


class ResultsPanelView(OwnerRestrictedView):
    """Panel for result correction workflows."""

    def __init__(self, admin_cog: AdminCommands, owner_user_id: str):
        super().__init__(admin_cog, owner_user_id)
        self.selection = PanelSelectionState()
        self.fixture_select = FixtureSelect(self)
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(self.fixture_select)
        self.add_item(ViewResultsButton(self))
        self.add_item(CorrectResultsButton(self))
        self.add_item(BackButton(self))

    async def load_fixture_options(self) -> None:
        fixtures = await self.admin_cog.service.get_recent_fixtures(MAX_SELECT_OPTIONS)
        self.fixture_select.update_options(fixtures)

    def render_content(self) -> str:
        status = (
            self.selection.status_message or "Select a fixture to inspect or correct saved results."
        )
        return "**Admin Panel - Results**\n" + status


class FixtureSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: FixturesPanelView | PredictionsPanelView | ResultsPanelView,
        open_only: bool = False,
    ):
        self.parent_view = parent_view
        self.open_only = open_only
        super().__init__(
            placeholder="Select fixture",
            min_values=1,
            max_values=1,
            disabled=True,
        )

    def update_options(self, fixtures: list[dict]) -> None:
        if self.open_only:
            fixtures = [fixture for fixture in fixtures if fixture["status"] == "open"]

        if not fixtures:
            self.options = [discord.SelectOption(label="No fixtures available", value="none")]
            self.disabled = True
            return

        self.options = [
            discord.SelectOption(
                label=_fixture_select_label(fixture),
                value=str(fixture["id"]),
                description=_fixture_select_description(fixture),
            )
            for fixture in fixtures[:MAX_SELECT_OPTIONS]
        ]
        self.disabled = False

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No fixtures available.", ephemeral=True)
            return

        fixture_id = int(self.values[0])
        self.parent_view.selection.fixture_id = fixture_id
        self.parent_view.selection.user_id = None

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            self.parent_view.selection.status_message = "Fixture no longer exists."
        else:
            self.parent_view.selection.status_message = (
                f"Selected week {fixture['week_number']} ({fixture['status']})."
            )

        if isinstance(self.parent_view, PredictionsPanelView):
            await self.parent_view.load_user_options()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


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

        prediction = await self.parent_view.admin_cog.db.get_prediction(
            fixture_id,
            self.values[0],
        )
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


class BackButton(discord.ui.Button):
    def __init__(self, parent_view: OwnerRestrictedView):
        self.parent_view = parent_view
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        view = AdminPanelHomeView(self.parent_view.admin_cog, self.parent_view.owner_user_id)
        await interaction.response.edit_message(
            content="**Admin Panel**\nChoose the workflow you want to manage.",
            view=view,
        )


class FixturesDeleteButton(discord.ui.Button):
    def __init__(self, parent_view: FixturesPanelView):
        self.parent_view = parent_view
        super().__init__(label="Delete Fixture", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select an open fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "Only open fixtures can be deleted from the panel.", ephemeral=True
            )
            return

        confirm_view = DeleteConfirmView(
            self.parent_view.admin_cog.db,
            self.parent_view.owner_user_id,
            fixture_id,
            fixture["week_number"],
        )
        await interaction.response.send_message(
            f"Delete week {fixture['week_number']} and all related predictions/results/scores?",
            view=confirm_view,
            ephemeral=True,
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
            ) = await self.parent_view.admin_cog.service.get_fixture_prediction_summary(fixture_id)
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

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await self.parent_view.admin_cog.db.get_prediction(fixture_id, user_id)
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
            ) = await self.parent_view.admin_cog.service.toggle_late_penalty_waiver(
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


class ViewResultsButton(discord.ui.Button):
    def __init__(self, parent_view: ResultsPanelView):
        self.parent_view = parent_view
        super().__init__(label="View Results", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        results = await self.parent_view.admin_cog.db.get_results(fixture_id)
        if fixture is None:
            await interaction.response.send_message("Fixture not found.", ephemeral=True)
            return
        if not results:
            await interaction.response.send_message(
                "No results saved for that fixture yet.", ephemeral=True
            )
            return

        lines = [f"**Week {fixture['week_number']} Results**"]
        for index, (game, result) in enumerate(zip(fixture["games"], results, strict=False), 1):
            lines.append(_format_prediction_line(index, game, result))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class CorrectResultsButton(discord.ui.Button):
    def __init__(self, parent_view: ResultsPanelView):
        self.parent_view = parent_view
        super().__init__(label="Correct Results", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return

        fixture = await self.parent_view.admin_cog.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            await interaction.response.send_message("Fixture not found.", ephemeral=True)
            return
        if not await self.parent_view.admin_cog.db.get_results(fixture_id):
            await interaction.response.send_message(
                "No results are stored for that fixture yet. Use `/admin results enter` first.",
                ephemeral=True,
            )
            return

        modal = CorrectResultsModal(self.parent_view, fixture)
        await interaction.response.send_modal(modal)


class ReplacePredictionModal(discord.ui.Modal):
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
            ) = await self.parent_view.admin_cog.service.replace_prediction(
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
            ) = await self.parent_view.admin_cog.service.correct_results(
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


class DeleteConfirmView(discord.ui.View):
    """View for confirming fixture deletion."""

    def __init__(self, db: Database, user_id: str, fixture_id: int, week_number: int):
        super().__init__(timeout=60)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.week_number = week_number

    @discord.ui.button(label="Yes, Delete", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        await self.db.delete_fixture(self.fixture_id)
        await interaction.response.edit_message(
            content=f"**Week {self.week_number} Fixture Deleted!**",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        if not is_admin(interaction):
            await interaction.response.send_message(
                "You no longer have permission to use admin commands.", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Deletion cancelled. The fixture is still active.",
            view=None,
        )


class PostResultsConfirmView(discord.ui.View):
    """View for confirming results posting with mentions."""

    def __init__(
        self,
        db: Database,
        fixture_data: dict,
        standings: list[dict],
        channel: discord.TextChannel,
    ):
        super().__init__(timeout=60)
        self.db = db
        self.fixture_data = fixture_data
        self.standings = standings
        self.channel = channel

    @discord.ui.button(label="NO", style=discord.ButtonStyle.primary)
    async def no_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)

        try:
            await interaction.response.edit_message(
                content="Results posted without mentions!",
                view=None,
            )
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            if not interaction.response.is_done():
                await interaction.response.edit_message(
                    content=f"Failed to post results: {exc}",
                    view=None,
                )
            else:
                await interaction.followup.send(f"Failed to post results: {exc}")

    @discord.ui.button(label="YES", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        mentions = [f"<@{score['user_id']}>" for score in self.fixture_data["scores"]]
        message += f"\n\n**Participants:**\n{' '.join(mentions)}"

        try:
            await interaction.response.edit_message(
                content="Results posted with mentions!",
                view=None,
            )
            await self.channel.send(message)
        except Exception as exc:
            logger.error(f"Failed to post results: {exc}")
            if not interaction.response.is_done():
                await interaction.response.edit_message(
                    content=f"Failed to post results: {exc}",
                    view=None,
                )
            else:
                await interaction.followup.send(f"Failed to post results: {exc}")


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
