"""Tests for admin panel interactions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.conftest import MockInteraction, MockMessage, MockThread, MockUser
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    CorrectResultsModal,
    CreateFixtureModal,
    DeleteConfirmView,
    EnterResultsModal,
    FixturesPanelView,
    PostResultsConfirmView,
    PredictionsPanelView,
    ReplacePredictionModal,
    ResultsPanelView,
    UnifiedAdminPanelView,
)
from typer_bot.commands.admin_panel.fixtures import _cleanup_discord_announcement
from typer_bot.database import Database
from typer_bot.utils import now


def _get_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    return next(child for child in view.children if getattr(child, "label", None) == label)


def _has_button(view: discord.ui.View, label: str) -> bool:
    return any(getattr(child, "label", None) == label for child in view.children)


def _selected_option_labels(select: discord.ui.Select) -> list[str]:
    return [option.label for option in select.options if option.default]


class TestAdminPanelCommand:
    """The slash entrypoint should open the panel."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_create_fixture_modal_shows_preview_with_default_deadline(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "Week 1 Fixture Preview" in mock_interaction_admin.response_sent[-1]["content"]
        assert "Deadline:" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_default_deadline_rolls_to_next_friday_after_cutoff(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        with patch(
            "typer_bot.commands.admin_panel.modals.now",
            return_value=datetime(2026, 4, 17, 19, 0, tzinfo=UTC),
        ):
            await modal.on_submit(mock_interaction_admin)

        view = mock_interaction_admin.response_sent[-1]["view"]
        assert view.deadline.year == 2026
        assert view.deadline.month == 4
        assert view.deadline.day == 24
        assert view.deadline.hour == 18

    @pytest.mark.asyncio
    async def test_create_fixture_modal_warns_when_other_fixtures_are_open(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.create_fixture(
            "111111", 5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "already open" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_empty_games_list(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "   \n   "
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "No games provided" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_more_than_100_games(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(
            f"Team {index:03d} - Team {index + 1:03d}" for index in range(101)
        )
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "Too many games" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_invalid_deadline(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "tomorrow 6pm"

        await modal.on_submit(mock_interaction_admin)

        assert "Invalid date format" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_parses_supported_deadline_formats(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "20.04.2026 18:00"

        await modal.on_submit(mock_interaction_admin)

        first_view = mock_interaction_admin.response_sent[-1]["view"]
        assert first_view.deadline.year == 2026
        assert first_view.deadline.month == 4
        assert first_view.deadline.day == 20

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "21/04/2026 18:00"

        await modal.on_submit(mock_interaction_admin)

        second_view = mock_interaction_admin.response_sent[-1]["view"]
        assert second_view.deadline.year == 2026
        assert second_view.deadline.month == 4
        assert second_view.deadline.day == 21

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_creates_fixture_and_announcement(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        thread = AsyncMock()
        announcement.create_thread = AsyncMock(return_value=thread)
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(1)
        assert fixture is not None
        assert fixture["message_id"] == "999999"
        assert fixture["channel_id"] == str(mock_interaction_admin.channel.id)
        assert "Fixture Created" in mock_interaction_admin.response_sent[-1]["content"]
        mock_interaction_admin.channel.send.assert_awaited_once()
        announcement.create_thread.assert_awaited_once()
        thread.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_thread_creation_fails(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        announcement.create_thread = AsyncMock(side_effect=Exception("thread failed"))
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(1)
        assert fixture is not None
        assert fixture["message_id"] == "999999"
        assert fixture["channel_id"] == str(mock_interaction_admin.channel.id)
        assert (
            "couldn't create a prediction thread"
            in mock_interaction_admin.followup_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_announcement_fails(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        mock_interaction_admin.channel.send = AsyncMock(side_effect=Exception("send failed"))

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert await admin_cog.db.get_fixture_by_id(1) is not None
        assert (
            "couldn't announce it in the channel"
            in mock_interaction_admin.followup_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_week_changes_between_preview_and_confirm(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        thread = AsyncMock()
        announcement.create_thread = AsyncMock(return_value=thread)
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)
        await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(2)
        assert fixture is not None
        assert "Week number changed" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_cancel_leaves_database_unchanged(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        cancel_button = next(
            child for child in confirm_view.children if getattr(child, "label", None) == "Cancel"
        )
        await cancel_button.callback(mock_interaction_admin)

        assert "Fixture creation cancelled" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_current_fixture("111111") is None

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await confirm_button.callback(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_current_fixture("111111") is None

    @pytest.mark.asyncio
    async def test_panel_command_returns_view(self, admin_cog, mock_interaction_admin):
        await admin_cog.panel.callback(admin_cog, mock_interaction_admin)

        response = mock_interaction_admin.response_sent[0]
        assert "Admin Panel" in response["content"]
        assert response["ephemeral"] is True
        assert response["view"] is not None


class TestPredictionPanelFlows:
    """Prediction override flow should stay targeted and owner-restricted."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_prediction_panel_blocks_non_owner(self, admin_cog, mock_interaction_admin):
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        outsider = MockInteraction(
            user=MockUser(user_id="999999", name="Outsider"),
            guild=mock_interaction_admin.guild,
            channel=mock_interaction_admin.channel,
        )

        allowed = await view.interaction_check(outsider)

        assert allowed is False
        assert outsider.response_sent[0]["ephemeral"] is True
        assert "permission" in outsider.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_rechecks_admin_role(self, admin_cog, mock_interaction_admin):
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        allowed = await view.interaction_check(mock_interaction_admin)

        assert allowed is False
        assert "no longer have permission" in mock_interaction_admin.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_initializes_empty_user_select(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        unified_view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await unified_view.load_fixture_options()

        assert unified_view.user_select.disabled is True
        assert _get_button(unified_view, "Replace Prediction").disabled is True
        assert _get_button(unified_view, "Toggle Late Waiver").disabled is True

    @pytest.mark.asyncio
    async def test_prediction_panel_buttons_enable_as_selections_are_made(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()

        assert _has_button(view, "View Predictions") is False
        assert _get_button(view, "Replace Prediction").disabled is True
        assert _get_button(view, "Toggle Late Waiver").disabled is True

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert "Fixture: Week 1 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]
        assert (
            "Pick a user to inspect or override a stored prediction."
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert _get_button(view, "Replace Prediction").disabled is True
        assert _get_button(view, "Toggle Late Waiver").disabled is True

        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)

        assert "User: User One (on time)" in mock_interaction_admin.response_sent[-1]["content"]
        assert "1. Team A - Team B 1-0" in mock_interaction_admin.response_sent[-1]["content"]
        assert "3. Team E - Team F 0-2" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Replace Prediction").disabled is False
        assert _get_button(view, "Toggle Late Waiver").disabled is False
        assert _selected_option_labels(view.fixture_select) == ["Week 1 [OPEN]"]
        assert _selected_option_labels(view.user_select) == ["User One"]

    @pytest.mark.asyncio
    async def test_prediction_panel_shows_no_predictions_inline_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 10, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert "Fixture: Week 10 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]
        assert (
            "No predictions saved for this fixture yet."
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert _get_button(view, "Replace Prediction").disabled is True
        assert _get_button(view, "Toggle Late Waiver").disabled is True

    @pytest.mark.asyncio
    async def test_prediction_panel_keeps_view_button_for_overflowing_user_lists(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 17, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(30):
            await admin_cog.db.save_prediction(
                fixture_id,
                f"user-{index}",
                f"User {index:02d}",
                ["1-0", "1-1", "0-2"],
                False,
            )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert _has_button(view, "View Predictions") is True
        assert (
            "More than 25 users predicted this fixture."
            in mock_interaction_admin.response_sent[-1]["content"]
        )

        view_button = _get_button(view, "View Predictions")
        await view_button.callback(mock_interaction_admin)

        assert "**Week 17 Predictions**" in mock_interaction_admin.response_sent[-1]["content"]
        assert "User 29" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_view_predictions_recovers_when_fixture_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 19, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.save_prediction(
                fixture_id,
                f"user-{index}",
                f"User {index:02d}",
                ["1-0", "1-1", "0-2"],
                False,
            )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_fixture(fixture_id)

        view_button = _get_button(view, "View Predictions")
        await view_button.callback(mock_interaction_admin)

        assert view.selection.fixture_id is None
        assert "Fixture not found" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_view_predictions_recovers_when_predictions_disappear(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 22, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.save_prediction(
                fixture_id,
                f"user-{index}",
                f"User {index:02d}",
                ["1-0", "1-1", "0-2"],
                False,
            )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        for index in range(26):
            await admin_cog.db.delete_prediction(fixture_id, f"user-{index}")

        view_button = _get_button(view, "View Predictions")
        await view_button.callback(mock_interaction_admin)

        assert (
            "No predictions saved for this fixture"
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert view.user_select.disabled is True
        assert _has_button(view, "View Predictions") is False

    @pytest.mark.asyncio
    async def test_prediction_panel_view_predictions_truncates_long_summary(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        games = [
            f"Very Long Home Team {index:02d} - Very Long Away Team {index:02d}"
            for index in range(1, 21)
        ]
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 20, games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.save_prediction(
                fixture_id,
                f"user-{index}",
                f"Very Long User Name {index:02d}",
                ["1-0"] * len(games),
                False,
            )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        view_button = _get_button(view, "View Predictions")
        await view_button.callback(mock_interaction_admin)

        content = mock_interaction_admin.response_sent[-1]["content"]
        assert len(content) <= 1900
        assert "content truncated" in content

    @pytest.mark.asyncio
    async def test_prediction_panel_replace_opens_modal(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)

        replace_button = next(
            child
            for child in view.children
            if getattr(child, "label", None) == "Replace Prediction"
        )
        await replace_button.callback(mock_interaction_admin)

        assert mock_interaction_admin.modal_sent["modal"].title == "Replace Week 1 Prediction"

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_dms_affected_user(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 34, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        toggle_button = _get_button(view, "Toggle Late Waiver")
        await toggle_button.callback(mock_interaction_admin)

        assert "late waiver" in target_user.dm_sent[-1].lower()
        assert _selected_option_labels(view.fixture_select) == ["Week 34 [OPEN]"]
        assert _selected_option_labels(view.user_select) == ["User One"]

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_ignores_dm_failures(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 35, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )
        failing_user = MagicMock()
        failing_user.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "dm blocked"))
        admin_cog.bot.get_user.return_value = failing_user

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        toggle_button = _get_button(view, "Toggle Late Waiver")
        await toggle_button.callback(mock_interaction_admin)

        assert "waiver enabled" in mock_interaction_admin.response_sent[-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_fetches_uncached_user(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 40, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = None
        admin_cog.bot.fetch_user = AsyncMock(return_value=target_user)

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        toggle_button = _get_button(view, "Toggle Late Waiver")
        await toggle_button.callback(mock_interaction_admin)

        admin_cog.bot.fetch_user.assert_awaited_once()
        assert "late waiver" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    async def test_prediction_panel_replace_recovers_when_prediction_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 13, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_prediction(fixture_id, "user-1")

        replace_button = _get_button(view, "Replace Prediction")
        await replace_button.callback(mock_interaction_admin)

        assert view.selection.user_id is None
        assert "no longer available" in mock_interaction_admin.response_sent[-1]["content"].lower()
        assert _get_button(view, "Replace Prediction").disabled is True

    @pytest.mark.asyncio
    async def test_prediction_panel_recovers_when_fixture_disappears_before_user_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 11, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_fixture(fixture_id)

        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)

        assert view.selection.fixture_id is None
        assert view.selection.user_id is None
        assert "Fixture no longer exists" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Replace Prediction").disabled is True
        assert _get_button(view, "Toggle Late Waiver").disabled is True

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_updates_status(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)

        toggle_button = next(
            child
            for child in view.children
            if getattr(child, "label", None) == "Toggle Late Waiver"
        )
        await toggle_button.callback(mock_interaction_admin)

        prediction = await admin_cog.db.get_prediction(fixture_id, "user-1")
        assert prediction is not None
        assert prediction["late_penalty_waived"] == 1
        assert "waiver enabled" in mock_interaction_admin.response_sent[-1]["content"].lower()
        assert (
            "User: User One (late, waiver active)"
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert "1. Team A - Team B 1-0" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_recovers_when_prediction_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 18, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_prediction(fixture_id, "user-1")

        toggle_button = _get_button(view, "Toggle Late Waiver")
        await toggle_button.callback(mock_interaction_admin)

        assert view.selection.user_id is None
        assert "no longer available" in mock_interaction_admin.response_sent[-1]["content"].lower()
        assert _get_button(view, "Toggle Late Waiver").disabled is True


class TestFixturePanelFlows:
    """Fixture panel should load current open fixtures before deletion."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_button_populates_open_fixture_options(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.create_fixture(
            "111111", 4, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        assert view.fixture_select.disabled is False
        assert view.fixture_select.options[0].label == "Week 4 [OPEN]"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "view_cls",
        [FixturesPanelView, PredictionsPanelView, ResultsPanelView, UnifiedAdminPanelView],
    )
    async def test_admin_fixture_selectors_only_show_current_guild(
        self,
        view_cls,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await admin_cog.db.create_fixture("111111", 1, sample_games, deadline)
        await admin_cog.db.create_fixture("guild-2", 2, sample_games, deadline)

        view = view_cls(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
        )
        await view.load_fixture_options()

        option_labels = [option.label for option in view.fixture_select.options]
        assert "Week 1 [OPEN]" in option_labels
        assert "Week 2 [OPEN]" not in option_labels

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_button_enables_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = FixturesPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        assert _get_button(view, "Delete Fixture").disabled is True

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert "Fixture: Week 5 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Delete Fixture").disabled is False

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_confirmation_shows_games(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Deletion confirmation must show game list so admin can verify the right fixture."""
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        delete_button = next(
            child for child in view.children if getattr(child, "label", None) == "Delete Fixture"
        )
        await delete_button.callback(mock_interaction_admin)

        confirmation_content = mock_interaction_admin.response_sent[-1]["content"]
        assert "Delete Week 6?" in confirmation_content
        assert "Team A - Team B" in confirmation_content

    @pytest.mark.asyncio
    async def test_unified_panel_create_fixture_button_opens_modal(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = mock_interaction_admin.channel.id
        mock_interaction_admin.channel = channel
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        create_button = _get_button(view, "Create Fixture")

        await create_button.callback(mock_interaction_admin)

        assert isinstance(mock_interaction_admin.modal_sent["modal"], CreateFixtureModal)

    def test_unified_panel_exposes_admin_workflows(self, admin_cog, mock_interaction_admin):
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )

        labels = {getattr(child, "label", None) for child in view.children}
        assert labels >= {
            "Create Fixture",
            "Delete Fixture",
            "Jump To Week",
            "Enter Results",
            "Calculate Scores",
            "Correct Results",
            "Re-post Results",
            "Replace Prediction",
            "Toggle Late Waiver",
        }

    @pytest.mark.asyncio
    async def test_unified_panel_hides_review_pending_button_without_pending_partials(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view._refresh_items()

        assert _has_button(view, "Review Late") is False

    @pytest.mark.asyncio
    async def test_unified_panel_shows_review_pending_button_when_pending_partials_exist(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 55, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        assert _has_button(view, "Review Late") is True

    @pytest.mark.asyncio
    async def test_unified_panel_hides_other_guild_pending_partials(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "guild-2", 55, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        assert _has_button(view, "Review Late") is False

    @pytest.mark.asyncio
    async def test_unified_panel_review_pending_button_jumps_to_pending_submission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 56, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        review_button = _get_button(view, "Review Late")
        await review_button.callback(mock_interaction_admin)

        assert view.selection.fixture_label == "Week 56 [OPEN]"
        assert view.selection.user_id == "111"
        assert _has_button(view, "Approve Late") is True
        assert _has_button(view, "Reject Late") is True

    @pytest.mark.asyncio
    async def test_unified_panel_review_pending_button_cycles_pending_submissions(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_a = await admin_cog.db.create_fixture(
            "111111", 57, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture_b = await admin_cog.db.create_fixture(
            "111111", 58, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_a,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        await admin_cog.db.save_prediction(
            fixture_b,
            "222",
            "User Two",
            ["2-1"],
            True,
            predicted_game_indexes=[0],
            pending_partial_approval=True,
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view._refresh_items()

        review_button = _get_button(view, "Review Late")
        await review_button.callback(mock_interaction_admin)
        first_selection = (view.selection.fixture_id, view.selection.user_id)

        await review_button.callback(mock_interaction_admin)
        second_selection = (view.selection.fixture_id, view.selection.user_id)

        assert first_selection != second_selection

    @pytest.mark.asyncio
    async def test_unified_panel_create_fixture_button_uses_parent_channel_from_thread(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        parent_channel = MagicMock(spec=discord.TextChannel)
        parent_channel.id = 123456
        thread = MagicMock(spec=discord.Thread)
        thread.parent = parent_channel
        mock_interaction_admin.channel = thread

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        create_button = _get_button(view, "Create Fixture")
        await create_button.callback(mock_interaction_admin)

        assert mock_interaction_admin.modal_sent["modal"].channel is parent_channel

    @pytest.mark.asyncio
    async def test_unified_panel_create_fixture_button_rejects_invalid_context(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        mock_interaction_admin.channel = MagicMock()

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        create_button = _get_button(view, "Create Fixture")
        await create_button.callback(mock_interaction_admin)

        assert "text channel" in mock_interaction_admin.response_sent[-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_unified_panel_enter_results_button_opens_modal(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 44, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        enter_button = _get_button(view, "Enter Results")
        await enter_button.callback(mock_interaction_admin)

        assert isinstance(mock_interaction_admin.modal_sent["modal"], EnterResultsModal)

    @pytest.mark.asyncio
    async def test_unified_panel_enter_results_button_rejects_existing_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 46, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        enter_button = _get_button(view, "Enter Results")
        await enter_button.callback(mock_interaction_admin)

        assert "Correct Results" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_posts_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 45, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["2-1", "1-1", "0-2"],
            False,
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        mock_interaction_admin.channel = channel
        admin_cog._create_backup = AsyncMock()

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        calculate_button = _get_button(view, "Calculate Scores")
        await calculate_button.callback(mock_interaction_admin)

        assert admin_cog.get_calculate_cooldown(str(mock_interaction_admin.user.id)) is not None
        channel.send.assert_awaited_once()
        assert (
            "Week 45 results calculated and posted"
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert "User One" in channel.send.call_args.args[0]

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_rejects_active_cooldown(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 47, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        admin_cog.record_calculate_cooldown(
            str(mock_interaction_admin.user.id), current_time=now().timestamp()
        )
        admin_cog.service.calculate_fixture_scores = AsyncMock()

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        calculate_button = _get_button(view, "Calculate Scores")
        await calculate_button.callback(mock_interaction_admin)

        assert "Please wait" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_handles_service_error(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 48, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        admin_cog.service.calculate_fixture_scores = AsyncMock(
            side_effect=ValueError("No results entered")
        )
        admin_cog._create_backup = AsyncMock()

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        calculate_button = _get_button(view, "Calculate Scores")
        await calculate_button.callback(mock_interaction_admin)

        assert mock_interaction_admin.response_sent[-1]["content"] == "No results entered"
        admin_cog._create_backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_button_opens_confirmation(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = mock_interaction_admin.channel.id
        mock_interaction_admin.channel = channel
        admin_cog.db.get_last_fixture_scores = AsyncMock(
            return_value={
                "week_number": 1,
                "games": ["A - B"],
                "results": ["2-1"],
                "scores": [
                    {
                        "user_id": "123",
                        "user_name": "User1",
                        "points": 3,
                        "exact_scores": 1,
                        "correct_results": 1,
                    }
                ],
            }
        )
        admin_cog.db.get_standings = AsyncMock(
            return_value=[
                {
                    "user_id": "123",
                    "user_name": "User1",
                    "total_points": 3,
                    "total_exact": 1,
                    "total_correct": 1,
                }
            ]
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        post_button = _get_button(view, "Re-post Results")
        await post_button.callback(mock_interaction_admin)

        assert isinstance(mock_interaction_admin.response_sent[-1]["view"], PostResultsConfirmView)

    @pytest.mark.asyncio
    async def test_unified_panel_jump_to_week_reaches_older_open_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        for week in range(1, 28):
            await admin_cog.db.create_fixture("111111", week, sample_games, deadline)

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()

        assert all(option.label != "Week 1 [OPEN]" for option in view.fixture_select.options)

        jump_button = _get_button(view, "Jump To Week")
        await jump_button.callback(mock_interaction_admin)
        modal = mock_interaction_admin.modal_sent["modal"]
        modal.week_input._value = "1"

        await modal.on_submit(mock_interaction_admin)

        assert view.selection.fixture_label == "Week 1 [OPEN]"
        assert "Fixture: Week 1 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_unified_panel_jump_to_week_rejects_invalid_input(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        jump_button = _get_button(view, "Jump To Week")
        await jump_button.callback(mock_interaction_admin)
        modal = mock_interaction_admin.modal_sent["modal"]
        modal.week_input._value = "abc"

        await modal.on_submit(mock_interaction_admin)

        assert "whole number" in mock_interaction_admin.response_sent[-1]["content"]
        assert view.selection.fixture_id is None

    @pytest.mark.asyncio
    async def test_unified_panel_jump_to_week_rejects_duplicate_open_weeks(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await admin_cog.db.create_fixture("111111", 5, sample_games, deadline)
        await admin_cog.db.create_fixture("111111", 5, sample_games, deadline)

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        jump_button = _get_button(view, "Jump To Week")
        await jump_button.callback(mock_interaction_admin)
        modal = mock_interaction_admin.modal_sent["modal"]
        modal.week_input._value = "5"

        await modal.on_submit(mock_interaction_admin)

        assert "More than one open fixture" in mock_interaction_admin.response_sent[-1]["content"]
        assert view.selection.fixture_id is None

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_button_rejects_non_text_channel(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        admin_cog.db.get_last_fixture_scores = AsyncMock(return_value={"scores": []})
        admin_cog.db.get_standings = AsyncMock(return_value=[])

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        post_button = _get_button(view, "Re-post Results")
        await post_button.callback(mock_interaction_admin)

        assert "text channels" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_button_rejects_missing_scores(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = mock_interaction_admin.channel.id
        mock_interaction_admin.channel = channel
        admin_cog.db.get_last_fixture_scores = AsyncMock(return_value=None)

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        post_button = _get_button(view, "Re-post Results")
        await post_button.callback(mock_interaction_admin)

        assert "No completed fixtures found" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_confirm_shows_error_on_db_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Silent DB failures surface as a visible error instead of timing out the interaction."""
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        db_mock = AsyncMock(spec=Database)
        db_mock.delete_fixture.side_effect = RuntimeError("DB locked")

        confirm_view = DeleteConfirmView(
            db_mock,
            str(mock_interaction_admin.user.id),
            "111111",
            fixture_id,
            week_number=7,
        )
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Yes, Delete"
        )
        await confirm_button.callback(mock_interaction_admin)

        response = mock_interaction_admin.response_sent[-1]
        assert "Failed to delete" in response["content"]
        assert response.get("view") is None


class TestDiscordCleanup:
    """_cleanup_discord_announcement should delete thread+message, tolerating Discord errors."""

    @pytest.mark.asyncio
    async def test_cleanup_deletes_thread_and_message(self):
        bot = MagicMock(spec=discord.Client)
        mock_thread = AsyncMock()
        mock_message = AsyncMock()
        mock_message.thread = mock_thread
        channel = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=mock_message)
        bot.get_channel.return_value = channel

        await _cleanup_discord_announcement(bot, "111", "222", week_number=5)

        mock_thread.delete.assert_called_once()
        mock_message.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_thread_deletes_message_only(self):
        bot = MagicMock(spec=discord.Client)
        mock_message = AsyncMock()
        mock_message.thread = None
        channel = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=mock_message)
        bot.get_channel.return_value = channel

        await _cleanup_discord_announcement(bot, "111", "222", week_number=5)

        mock_message.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_swallows_discord_errors(self):
        bot = MagicMock(spec=discord.Client)
        channel = AsyncMock()
        channel.fetch_message.side_effect = Exception("Discord unavailable")
        bot.get_channel.return_value = channel

        await _cleanup_discord_announcement(bot, "111", "222", week_number=5)


class TestResultsPanelFlows:
    """Result correction panel should target a fixture before editing."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_results_panel_correct_opens_modal(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 3, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        correct_button = next(
            child for child in view.children if getattr(child, "label", None) == "Correct Results"
        )
        await correct_button.callback(mock_interaction_admin)

        assert mock_interaction_admin.modal_sent["modal"].title == "Correct Week 3 Results"
        assert mock_interaction_admin.modal_sent["modal"].results_input.default == (
            "1. Team A - Team B 1-0\n2. Team C - Team D 1-1\n3. Team E - Team F 0-0"
        )

    @pytest.mark.asyncio
    async def test_results_panel_buttons_enable_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 4, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()

        assert _has_button(view, "View Results") is False
        assert _get_button(view, "Correct Results").disabled is True

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert "Fixture: Week 4 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]
        assert "1. Team A - Team B 1-0" in mock_interaction_admin.response_sent[-1]["content"]
        assert "3. Team E - Team F 0-0" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Correct Results").disabled is False

    @pytest.mark.asyncio
    async def test_results_panel_requires_existing_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        correct_button = next(
            child for child in view.children if getattr(child, "label", None) == "Correct Results"
        )
        await correct_button.callback(mock_interaction_admin)

        assert (
            "Enter Results button in `/admin panel`"
            in mock_interaction_admin.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_results_panel_correct_recovers_when_fixture_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 14, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_fixture(fixture_id)

        correct_button = _get_button(view, "Correct Results")
        await correct_button.callback(mock_interaction_admin)

        assert view.selection.fixture_id is None
        assert "Fixture no longer exists" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Correct Results").disabled is True

    @pytest.mark.asyncio
    async def test_fixture_select_removes_deleted_fixture_option(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 23, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        await admin_cog.db.delete_fixture(fixture_id)

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert view.fixture_select.options[0].label == "No fixtures available"

    @pytest.mark.asyncio
    async def test_results_panel_truncates_long_inline_preview(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        games = [
            f"Very Long Home Team {index:02d} - Very Long Away Team {index:02d}"
            for index in range(1, 101)
        ]
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 12, games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0"] * len(games))

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        content = mock_interaction_admin.response_sent[-1]["content"]
        assert len(content) <= 1900
        assert "content truncated" in content

    @pytest.mark.asyncio
    async def test_unified_panel_correct_results_clears_stale_user_on_success(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 32, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        admin_cog.bot.get_user.return_value = None

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None
        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert view.selection.user_id is None
        assert view.selection.user_label == ""
        assert "User:" not in mock_interaction_admin.response_sent[-1]["content"]
        assert "1. Team A - Team B 2-1" in mock_interaction_admin.response_sent[-1]["content"]
        assert _selected_option_labels(view.user_select) == []
        assert _selected_option_labels(view.fixture_select) == ["Week 32 [OPEN]"]

    @pytest.mark.asyncio
    async def test_unified_panel_correct_results_clears_stale_user_on_deleted_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 33, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_fixture(fixture_id)

        correct_button = _get_button(view, "Correct Results")
        await correct_button.callback(mock_interaction_admin)

        assert view.selection.fixture_id is None
        assert view.selection.user_id is None
        assert view.user_select.disabled is True
        assert "Fixture no longer exists" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_unified_panel_shows_partial_approval_buttons_for_pending_prediction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 50, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        assert _has_button(view, "Approve Late") is True
        assert _has_button(view, "Reject Late") is True
        assert _has_button(view, "Replace Prediction") is False

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 51, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        approve_button = _get_button(view, "Approve Late")
        await approve_button.callback(mock_interaction_admin)

        prediction = await admin_cog.db.get_prediction(fixture_id, "111")
        assert prediction is not None
        assert prediction["pending_partial_approval"] is False
        assert prediction["is_late"] == 0
        assert "approved" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    async def test_unified_panel_reject_partial_prediction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 52, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        reject_button = _get_button(view, "Reject Late")
        await reject_button.callback(mock_interaction_admin)

        assert await admin_cog.db.get_prediction(fixture_id, "111") is None
        assert "rejected" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction_edits_public_bot_post(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 70, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.update_fixture_announcement(
            fixture_id,
            message_id="789012",
            channel_id="123456",
        )
        thread = MockThread(thread_id="789012", guild=mock_interaction_admin.guild)
        public_message = await thread.send(
            "**Prediction from <@111> · Week 70**\n\n2. Team C - Team D **1-1**\n3. Team E - Team F **0-2**\n\n⏳ Late prediction awaiting admin review."
        )
        admin_cog.bot.get_channel.side_effect = lambda channel_id: (
            thread if channel_id == 789012 else None
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
            public_message_id=str(public_message.id),
            public_message_kind="bot_post",
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        approve_button = _get_button(view, "Approve Late")
        await approve_button.callback(mock_interaction_admin)

        assert "approved by an admin" in public_message.content

    @pytest.mark.asyncio
    async def test_unified_panel_reject_partial_prediction_edits_public_bot_post(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 72, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.update_fixture_announcement(
            fixture_id,
            message_id="789012",
            channel_id="123456",
        )
        thread = MockThread(thread_id="789012", guild=mock_interaction_admin.guild)
        public_message = await thread.send(
            "**Prediction from <@111> · Week 72**\n\n2. Team C - Team D **1-1**\n3. Team E - Team F **0-2**\n\n⏳ Late prediction awaiting admin review."
        )
        admin_cog.bot.get_channel.side_effect = lambda channel_id: (
            thread if channel_id == 789012 else None
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
            public_message_id=str(public_message.id),
            public_message_kind="bot_post",
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        reject_button = _get_button(view, "Reject Late")
        await reject_button.callback(mock_interaction_admin)

        assert "rejected by an admin" in public_message.content

    @pytest.mark.asyncio
    async def test_unified_panel_reject_partial_prediction_updates_thread_reaction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 71, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.update_fixture_announcement(
            fixture_id,
            message_id="789012",
            channel_id="123456",
        )
        thread = MockThread(thread_id="789012", guild=mock_interaction_admin.guild)
        user_message = MockMessage(
            content="Team C - Team D 1-1\nTeam E - Team F 0-2",
            message_id="555555",
            author=MockUser("111", "User One"),
            channel=thread,
            guild=mock_interaction_admin.guild,
        )
        thread.register_message(user_message)
        admin_cog.bot.get_channel.side_effect = lambda channel_id: (
            thread if channel_id == 789012 else None
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
            public_message_id=str(user_message.id),
            public_message_kind="thread_message",
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        reject_button = _get_button(view, "Reject Late")
        await reject_button.callback(mock_interaction_admin)

        assert ("⏳", admin_cog.bot.user.id) in user_message.reactions_removed
        assert "❌" in user_message.reactions_added

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction_updates_thread_reaction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 73, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.update_fixture_announcement(
            fixture_id,
            message_id="789012",
            channel_id="123456",
        )
        thread = MockThread(thread_id="789012", guild=mock_interaction_admin.guild)
        user_message = MockMessage(
            content="Team C - Team D 1-1\nTeam E - Team F 0-2",
            message_id="555555",
            author=MockUser("111", "User One"),
            channel=thread,
            guild=mock_interaction_admin.guild,
        )
        thread.register_message(user_message)
        admin_cog.bot.get_channel.side_effect = lambda channel_id: (
            thread if channel_id == 789012 else None
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
            public_message_id=str(user_message.id),
            public_message_kind="thread_message",
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        approve_button = _get_button(view, "Approve Late")
        await approve_button.callback(mock_interaction_admin)

        assert ("⏳", admin_cog.bot.user.id) in user_message.reactions_removed
        assert "✅" in user_message.reactions_added

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction_ignores_bad_fixture_thread_id(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 74, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.update_fixture_announcement(
            fixture_id,
            message_id="not-a-thread-id",
            channel_id="123456",
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
            public_message_id="555555",
            public_message_kind="thread_message",
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        approve_button = _get_button(view, "Approve Late")
        await approve_button.callback(mock_interaction_admin)

        prediction = await admin_cog.db.get_prediction(fixture_id, "111")
        assert prediction is not None
        assert prediction["pending_partial_approval"] is False
        assert "approved" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction_recalculates_scores(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 53, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await admin_cog.db.save_prediction(
            fixture_id,
            "999",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await admin_cog.service.calculate_fixture_scores(fixture_id)
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        approve_button = _get_button(view, "Approve Late")
        await approve_button.callback(mock_interaction_admin)

        standings = await admin_cog.db.get_standings()
        assert {row["user_id"] for row in standings} == {"999", "111"}

    @pytest.mark.asyncio
    async def test_unified_panel_reject_partial_prediction_recalculates_scores(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 54, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await admin_cog.db.save_prediction(
            fixture_id,
            "999",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await admin_cog.service.calculate_fixture_scores(fixture_id)
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)

        reject_button = _get_button(view, "Reject Late")
        await reject_button.callback(mock_interaction_admin)

        standings = await admin_cog.db.get_standings()
        assert {row["user_id"] for row in standings} == {"999"}


class TestAdminPanelModals:
    """Modal submit paths should reject stale permissions."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_enter_results_modal_prefills_fixture_template(
        self,
        admin_cog,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 24, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)

        assert modal.results_input.default == (
            "Team A - Team B 2:0\nTeam C - Team D 2:0\nTeam E - Team F 2:0"
        )

    @pytest.mark.asyncio
    async def test_enter_results_modal_shows_parse_errors(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 25, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B\nTeam C - Team D\nTeam E - Team F"

        await modal.on_submit(mock_interaction_admin)

        assert "Could not find score" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_enter_results_modal_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 28, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_enter_results_modal_save_results_persists_fixture_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 26, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Save Results"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert "Results Saved!" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_results(fixture_id) == ["2-1", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_enter_results_modal_save_results_supports_cancelled_games(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 30, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B x\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Save Results"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert await admin_cog.db.get_results(fixture_id) == ["x", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_enter_results_confirm_shows_save_error_when_fixture_becomes_unavailable(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 31, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)
        await admin_cog.db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 1,
                }
            ],
        )

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Save Results"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert "Cannot save results" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_enter_results_modal_cancel_leaves_results_unsaved(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 27, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        cancel_button = next(
            child for child in confirm_view.children if getattr(child, "label", None) == "Cancel"
        )
        await cancel_button.callback(mock_interaction_admin)

        assert "Results entry cancelled" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_results(fixture_id) is None

    @pytest.mark.asyncio
    async def test_enter_results_confirm_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 29, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Save Results"
        )
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await confirm_button.callback(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_results(fixture_id) is None

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "user-1")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_prediction_panel_clears_actions_for_deleted_prediction(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 9, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        await admin_cog.db.delete_prediction(fixture_id, "user-1")

        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)

        assert view.selection.user_id is None
        assert "Prediction no longer exists" in mock_interaction_admin.response_sent[-1]["content"]
        assert _get_button(view, "Replace Prediction").disabled is True
        assert _get_button(view, "Toggle Late Waiver").disabled is True

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_updates_inline_panel_content(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 15, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "user-1")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        assert (
            "Replaced User One's prediction in week 15."
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert "1. Team A - Team B 2-1" in mock_interaction_admin.response_sent[-1]["content"]
        assert "User: User One (on time)" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_dms_affected_user(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 36, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = target_user

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "111")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        assert "updated your Week 36 prediction" in target_user.dm_sent[-1]
        assert _selected_option_labels(view.fixture_select) == ["Week 36 [OPEN]"]
        assert _selected_option_labels(view.user_select) == ["User One"]

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_ignores_dm_failures(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 37, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        failing_user = MagicMock()
        failing_user.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "dm blocked"))
        admin_cog.bot.get_user.return_value = failing_user

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "111")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        assert (
            "Replaced User One's prediction" in mock_interaction_admin.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_fetches_uncached_user(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 41, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        target_user = MockUser("111", "User One")
        admin_cog.bot.get_user.return_value = None
        admin_cog.bot.fetch_user = AsyncMock(return_value=target_user)

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["111"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "111")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        admin_cog.bot.fetch_user.assert_awaited_once()
        assert "updated your Week 41 prediction" in target_user.dm_sent[-1]

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_recovers_when_prediction_disappears(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 21, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        view.user_select._values = ["user-1"]
        await view.user_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        prediction = await admin_cog.db.get_prediction(fixture_id, "user-1")
        assert fixture is not None
        assert prediction is not None
        await admin_cog.db.delete_prediction(fixture_id, "user-1")

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        assert view.selection.user_id is None
        assert (
            "Prediction not found for that user"
            in mock_interaction_admin.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_correct_results_modal_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_correct_results_modal_handles_deleted_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await admin_cog.db.delete_fixture(fixture_id)

        await modal.on_submit(mock_interaction_admin)

        assert "Fixture not found" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_correct_results_modal_rejects_cross_guild_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "guild-2", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        original_results = ["0-0", "1-1", "2-2"]
        await admin_cog.db.save_results(fixture_id, original_results)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.selection.fixture_id = fixture_id
        view.selection.fixture_label = "Week 7 [OPEN]"
        modal = CorrectResultsModal(view, fixture, original_results)
        modal.results_input._value = "Team A - Team B 3-0\nTeam C - Team D 3-0\nTeam E - Team F 3-0"

        await modal.on_submit(mock_interaction_admin)

        assert "Fixture not found" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_results(fixture_id) == original_results

    @pytest.mark.asyncio
    async def test_correct_results_modal_prefills_stored_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])

        assert modal.results_input.default == (
            "1. Team A - Team B 1-0\n2. Team C - Team D 1-1\n3. Team E - Team F 0-0"
        )

    @pytest.mark.asyncio
    async def test_correct_results_modal_updates_inline_panel_content(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 16, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert (
            "Saved corrected results for week 16."
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert "1. Team A - Team B 2-1" in mock_interaction_admin.response_sent[-1]["content"]
        assert "Fixture: Week 16 [OPEN]" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_correct_results_modal_dms_all_fixture_participants(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 38, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.save_prediction(
            fixture_id, "222", "User Two", ["0-0", "2-2", "1-1"], False
        )
        user_one = MockUser("111", "User One")
        user_two = MockUser("222", "User Two")
        admin_cog.bot.get_user.side_effect = lambda user_id: {111: user_one, 222: user_two}.get(
            user_id
        )

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert "Results were corrected for Week 38." in user_one.dm_sent[-1]
        assert "Results were corrected for Week 38." in user_two.dm_sent[-1]

    @pytest.mark.asyncio
    async def test_correct_results_modal_ignores_dm_failures(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 39, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        failing_user = MagicMock()
        failing_user.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "dm blocked"))
        admin_cog.bot.get_user.return_value = failing_user

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert (
            "Saved corrected results for week 39."
            in mock_interaction_admin.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_correct_results_modal_continues_after_one_dm_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 42, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.save_prediction(
            fixture_id, "222", "User Two", ["0-0", "2-2", "1-1"], False
        )
        failing_user = MagicMock()
        failing_user.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "dm blocked"))
        user_two = MockUser("222", "User Two")
        admin_cog.bot.get_user.side_effect = lambda user_id: {111: failing_user, 222: user_two}.get(
            user_id
        )

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert "Results were corrected for Week 42." in user_two.dm_sent[-1]

    @pytest.mark.asyncio
    async def test_correct_results_modal_continues_after_fetch_user_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 43, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.save_prediction(
            fixture_id, "222", "User Two", ["0-0", "2-2", "1-1"], False
        )
        user_two = MockUser("222", "User Two")
        admin_cog.bot.get_user.return_value = None
        admin_cog.bot.fetch_user = AsyncMock(
            side_effect=[discord.HTTPException(MagicMock(), "fetch failed"), user_two]
        )

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        view.bot = admin_cog.bot
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert "Results were corrected for Week 43." in user_two.dm_sent[-1]
