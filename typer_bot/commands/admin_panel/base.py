"""Shared admin panel state, helpers, and base components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from typer_bot.utils import is_admin

if TYPE_CHECKING:
    from typer_bot.commands.admin_commands import AdminCommands

    from .fixtures import FixturesPanelView
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView

MAX_SELECT_OPTIONS = 25


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
        from .fixtures import FixturesPanelView

        view = FixturesPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)

    @discord.ui.button(label="Predictions", style=discord.ButtonStyle.primary)
    async def predictions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from .predictions import PredictionsPanelView

        view = PredictionsPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)

    @discord.ui.button(label="Results", style=discord.ButtonStyle.success)
    async def results(self, interaction: discord.Interaction, _button: discord.ui.Button):
        from .results import ResultsPanelView

        view = ResultsPanelView(self.admin_cog, self.owner_user_id)
        await view.load_fixture_options()
        await interaction.response.edit_message(content=view.render_content(), view=view)


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

        load_user_options = getattr(self.parent_view, "load_user_options", None)
        if callable(load_user_options):
            await load_user_options()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
