"""Shared admin panel state, helpers, and base components."""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from typer_bot.database import Database
from typer_bot.services import AdminService
from typer_bot.utils import is_admin

if TYPE_CHECKING:
    from .fixtures import FixturesPanelView
    from .predictions import PredictionsPanelView
    from .results import ResultsPanelView
    from .unified import UnifiedAdminPanelView

MAX_SELECT_OPTIONS = 25
MAX_PANEL_CONTENT_LENGTH = 1900

logger = logging.getLogger(__name__)


def _fixture_select_label(fixture: dict) -> str:
    status = fixture["status"].upper()
    return f"Week {fixture['week_number']} [{status}]"


def _fixture_select_description(fixture: dict) -> str:
    games_count = len(fixture["games"])
    return f"{games_count} matches"


def _format_prediction_line(index: int, game: str, prediction: str) -> str:
    """Format a per-match score line for prediction or result displays."""

    return f"{index}. {game} {prediction}"


def _build_detail_lines(games: list[str], values: list[str]) -> list[str]:
    """Format per-match detail lines, truncating to the shorter input list.

    This intentionally drops unmatched trailing items if the inputs differ.
    """

    return [
        _format_prediction_line(index, game, value)
        for index, (game, value) in enumerate(zip(games, values, strict=False), 1)
    ]


def _build_indexed_detail_lines(
    game_indexes: list[int],
    games: list[str],
    values: list[str],
) -> list[str]:
    """Format detail lines while preserving original fixture row numbers."""
    return [
        _format_prediction_line(game_index + 1, games[game_index], value)
        for game_index, value in zip(game_indexes, values, strict=False)
        if 0 <= game_index < len(games)
    ]


def _render_panel_content(lines: list[str]) -> str:
    """Join panel lines under Discord's message limit.

    Uses a 1900-char safety cap so panel edits stay below Discord's 2000-char
    hard limit after adding a truncation marker when needed.
    """

    content = ""
    suffix = "\n\n... content truncated."
    for line in lines:
        candidate = f"{content}\n{line}" if content else line
        if len(candidate) <= MAX_PANEL_CONTENT_LENGTH:
            content = candidate
            continue

        if not content:
            return line[: MAX_PANEL_CONTENT_LENGTH - 3] + "..."

        if len(suffix) >= MAX_PANEL_CONTENT_LENGTH:
            return content[:MAX_PANEL_CONTENT_LENGTH]

        if len(content) + len(suffix) <= MAX_PANEL_CONTENT_LENGTH:
            return content + suffix

        return content[: MAX_PANEL_CONTENT_LENGTH - len(suffix)] + suffix

    return content


def _prediction_status_text(prediction: dict) -> str:
    if prediction.get("pending_partial_approval"):
        return "late, pending review"
    if not prediction["is_late"]:
        return "on time"
    if prediction["late_penalty_waived"]:
        return "late, waiver active"
    return "late, penalty active"


@dataclass(slots=True)
class PanelSelectionState:
    """Shared render state for admin panel views.

    `fixture_label`, `user_label`, and `detail_lines` drive the inline panel body.
    `status_message` carries transient feedback after selections or admin actions.
    """

    fixture_id: int | None = None
    user_id: str | None = None
    fixture_label: str = ""
    user_label: str = ""
    detail_lines: list[str] = field(default_factory=list)
    status_message: str = ""


class OwnerRestrictedView(discord.ui.View):
    """Base view that only accepts interactions from the opening admin.

    The owner check persists across unified-panel refreshes and modal callbacks
    so one admin cannot click through another admin's ephemeral management flow.
    """

    def __init__(
        self,
        db: Database,
        service: AdminService,
        owner_user_id: str,
        bot: discord.Client | None = None,
        timeout: float = 180,
    ):
        super().__init__(timeout=timeout)
        self.db = db
        self.service = service
        self.owner_user_id = owner_user_id
        self.bot = bot

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


async def _notify_user_dm(
    bot: discord.Client | None,
    user_id: str,
    message: str,
    *,
    context: str,
) -> None:
    """Send a best-effort DM notification for admin correction actions.

    This helper expects a numeric Discord user ID string, falls back to
    `fetch_user()` when the user is uncached, and logs/swallow failures instead
    of raising so admin actions never depend on DM delivery.
    """
    if bot is None:
        return

    try:
        discord_user_id = int(user_id)
    except ValueError:
        logger.warning("Could not parse user id %s for %s notification", user_id, context)
        return

    user = bot.get_user(discord_user_id)
    if user is None:
        fetch_user = getattr(bot, "fetch_user", None)
        if fetch_user is None:
            return
        try:
            user = await fetch_user(discord_user_id)
        except discord.HTTPException:
            logger.warning("Could not fetch user %s for %s notification", user_id, context)
            return

    try:
        await user.send(message)
    except discord.HTTPException:
        logger.warning("Could not DM user %s for %s notification", user_id, context)


async def _get_fixture_thread(
    bot: discord.Client | None,
    fixture: dict,
) -> discord.Thread | None:
    if bot is None:
        return None

    message_id = fixture.get("message_id")
    if not message_id:
        return None

    thread = bot.get_channel(int(message_id))
    if isinstance(thread, discord.Thread):
        return thread

    fetch_channel = getattr(bot, "fetch_channel", None)
    if fetch_channel is None:
        return None

    try:
        fetched = await fetch_channel(int(message_id))
    except discord.HTTPException:
        logger.warning("Could not fetch fixture thread %s for public review update", message_id)
        return None
    return fetched if isinstance(fetched, discord.Thread) else None


def _review_status_line(*, approved: bool) -> str:
    return (
        "✅ Late prediction approved by an admin."
        if approved
        else "❌ Late prediction rejected by an admin."
    )


def _render_public_review_content(content: str, *, approved: bool) -> str:
    lines = content.splitlines()
    status_prefixes = (
        "⏳ Late prediction awaiting admin review.",
        "⏳ Late partial pending admin approval.",
        "✅ Late prediction approved by an admin.",
        "❌ Late prediction rejected by an admin.",
    )
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith(status_prefixes):
            lines[index] = _review_status_line(approved=approved)
            return "\n".join(lines)

    return f"{content}\n\n{_review_status_line(approved=approved)}"


async def _update_public_review_marker(
    bot: discord.Client | None,
    fixture: dict,
    prediction: dict,
    *,
    approved: bool,
) -> None:
    """Best-effort public marker update for reviewed late predictions."""
    public_message_id = prediction.get("public_message_id")
    public_message_kind = prediction.get("public_message_kind")
    if public_message_id is None or public_message_kind is None:
        return

    thread = await _get_fixture_thread(bot, fixture)
    if thread is None:
        return

    try:
        message = await thread.fetch_message(int(public_message_id))
    except (ValueError, discord.HTTPException, AttributeError):
        logger.warning(
            "Could not fetch public review marker %s for fixture %s",
            public_message_id,
            fixture.get("id"),
        )
        return

    if public_message_kind == "bot_post":
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            return
        try:
            await message.edit(content=_render_public_review_content(content, approved=approved))
        except discord.HTTPException:
            logger.warning(
                "Could not edit public review post %s for fixture %s",
                public_message_id,
                fixture.get("id"),
            )
        return

    if public_message_kind != "thread_message" or bot is None or bot.user is None:
        return

    with suppress(discord.HTTPException, AttributeError):
        await message.remove_reaction("⏳", bot.user)
    try:
        await message.add_reaction("✅" if approved else "❌")
    except discord.HTTPException:
        logger.warning(
            "Could not update public review reaction on message %s for fixture %s",
            public_message_id,
            fixture.get("id"),
        )


class FixtureSelect(discord.ui.Select):
    """Shared fixture selector that updates panel selection state in place.

    Before hooks run, the selector clears user/detail state and refreshes the
    selected fixture label + status. Subviews can optionally react to fixture
    changes by exposing `populate_fixture_details()` and/or `load_user_options()`.
    The selector calls those hooks before re-rendering the panel.

    Re-rendered Discord selects lose their visible selection unless the active
    option is re-marked as `default`, so this component re-applies that state.
    """

    def __init__(
        self,
        parent_view: FixturesPanelView
        | PredictionsPanelView
        | ResultsPanelView
        | UnifiedAdminPanelView,
    ):
        self.parent_view = parent_view
        super().__init__(
            placeholder="Select fixture",
            min_values=1,
            max_values=1,
            disabled=True,
        )

    def update_options(self, fixtures: list[dict]) -> None:
        if not fixtures:
            self.options = [discord.SelectOption(label="No fixtures available", value="none")]
            self.disabled = True
            return

        self.options = [
            discord.SelectOption(
                label=_fixture_select_label(fixture),
                value=str(fixture["id"]),
                description=_fixture_select_description(fixture),
                default=self.parent_view.selection.fixture_id == fixture["id"],
            )
            for fixture in fixtures[:MAX_SELECT_OPTIONS]
        ]
        self.disabled = False

    def sync_selected_option(self) -> None:
        selected_value = (
            str(self.parent_view.selection.fixture_id)
            if self.parent_view.selection.fixture_id is not None
            else None
        )
        for option in self.options:
            option.default = option.value == selected_value

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No fixtures available.", ephemeral=True)
            return

        fixture_id = int(self.values[0])
        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = []

        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            self.parent_view.selection.fixture_id = None
            self.parent_view.selection.fixture_label = ""
            self.parent_view.selection.status_message = "Fixture no longer exists."
            load_fixture_options = getattr(self.parent_view, "load_fixture_options", None)
            if callable(load_fixture_options):
                await load_fixture_options()
        else:
            self.parent_view.selection.fixture_id = fixture_id
            self.parent_view.selection.fixture_label = _fixture_select_label(fixture)
            self.parent_view.selection.status_message = ""

        populate_fixture_details = getattr(self.parent_view, "populate_fixture_details", None)
        if callable(populate_fixture_details):
            await populate_fixture_details(fixture)

        load_user_options = getattr(self.parent_view, "load_user_options", None)
        if callable(load_user_options):
            await load_user_options()

        set_selected_prediction = getattr(self.parent_view, "set_selected_prediction", None)
        if callable(set_selected_prediction):
            await set_selected_prediction()

        self.sync_selected_option()

        refresh_items = getattr(self.parent_view, "_refresh_items", None)
        if callable(refresh_items):
            refresh_items()

        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )
