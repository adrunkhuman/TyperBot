"""Unified admin panel view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService

from .base import (
    MAX_SELECT_OPTIONS,
    FixtureSelect,
    OwnerRestrictedView,
    PanelSelectionState,
    _build_detail_lines,
    _render_panel_content,
)
from .fixtures import FixturesDeleteButton
from .partial_review import ApprovePartialButton, RejectPartialButton, ReviewPendingPartialsButton
from .predictions import (
    PredictionUserSelect,
    ReplacePredictionButton,
    ToggleWaiverButton,
    ViewPredictionsButton,
)
from .results import CorrectResultsButton
from .unified_actions import (
    CalculateScoresButton,
    CreateFixtureButton,
    EnterResultsButton,
    JumpToWeekButton,
    JumpToWeekModal,
    NewSeasonButton,
    NewSeasonModal,
    PostResultsButton,
    PostResultsConfirmView,
    SetupBotButton,
)

if TYPE_CHECKING:
    from typer_bot.commands.admin_commands import AdminCommands

__all__ = [
    "CalculateScoresButton",
    "CreateFixtureButton",
    "EnterResultsButton",
    "JumpToWeekButton",
    "JumpToWeekModal",
    "NewSeasonButton",
    "NewSeasonModal",
    "PostResultsButton",
    "PostResultsConfirmView",
    "SetupBotButton",
    "UnifiedAdminPanelView",
]


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
        self.active_season: dict | None = None
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
        self.add_item(NewSeasonButton(self))
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
        self.active_season = await self.db.get_or_create_active_season(self.guild_id)
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
        if self.active_season is not None:
            lines.append(f"Active season: {self.active_season['name']}")
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
