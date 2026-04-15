"""Tests for admin panel interactions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.conftest import MockInteraction, MockUser
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    AdminPanelHomeView,
    CorrectResultsModal,
    DeleteConfirmView,
    EnterResultsModal,
    FixturesPanelView,
    PredictionsPanelView,
    ReplacePredictionModal,
    ResultsPanelView,
)
from typer_bot.commands.admin_panel.fixtures import _cleanup_discord_announcement
from typer_bot.database import Database


def _get_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    return next(child for child in view.children if getattr(child, "label", None) == label)


def _has_button(view: discord.ui.View, label: str) -> bool:
    return any(getattr(child, "label", None) == label for child in view.children)


class TestAdminPanelCommand:
    """The slash entrypoint should open the panel."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
        await admin_cog.db.create_fixture(1, sample_games, datetime.now(UTC) + timedelta(days=1))

        home_view = AdminPanelHomeView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), bot=admin_cog.bot
        )
        predictions_button = next(
            child for child in home_view.children if getattr(child, "label", None) == "Predictions"
        )
        await predictions_button.callback(mock_interaction_admin)

        edited_view = mock_interaction_admin.response_sent[-1]["view"]
        assert edited_view.user_select.disabled is True
        assert len(edited_view.user_select.options) == 1
        assert edited_view.user_select.options[0].label == "No predictions available"

    @pytest.mark.asyncio
    async def test_prediction_panel_buttons_enable_as_selections_are_made(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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

    @pytest.mark.asyncio
    async def test_prediction_panel_shows_no_predictions_inline_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            10, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            17, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            19, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            22, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            20, games, datetime.now(UTC) + timedelta(days=1)
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
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
    async def test_prediction_panel_replace_recovers_when_prediction_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            13, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            11, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            18, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            True,
        )

        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
        await admin_cog.db.create_fixture(4, sample_games, datetime.now(UTC) + timedelta(days=1))

        view = AdminPanelHomeView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), bot=admin_cog.bot
        )
        fixture_button = next(
            child for child in view.children if getattr(child, "label", None) == "Fixtures"
        )
        await fixture_button.callback(mock_interaction_admin)

        edited_view = mock_interaction_admin.response_sent[-1]["view"]
        assert edited_view.fixture_select.disabled is False
        assert edited_view.fixture_select.options[0].label == "Week 4 [OPEN]"

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_button_enables_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = FixturesPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), bot=admin_cog.bot
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
            6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = AdminPanelHomeView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), bot=admin_cog.bot
        )
        fixture_button = next(
            child for child in view.children if getattr(child, "label", None) == "Fixtures"
        )
        await fixture_button.callback(mock_interaction_admin)

        fixtures_view = mock_interaction_admin.response_sent[-1]["view"]
        fixtures_view.fixture_select._values = [str(fixture_id)]
        await fixtures_view.fixture_select.callback(mock_interaction_admin)

        delete_button = next(
            child
            for child in fixtures_view.children
            if getattr(child, "label", None) == "Delete Fixture"
        )
        await delete_button.callback(mock_interaction_admin)

        confirmation_content = mock_interaction_admin.response_sent[-1]["content"]
        assert "Delete Week 6?" in confirmation_content
        assert "Team A - Team B" in confirmation_content

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_confirm_shows_error_on_db_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Silent DB failures surface as a visible error instead of timing out the interaction."""
        fixture_id = await admin_cog.db.create_fixture(
            7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        db_mock = AsyncMock(spec=Database)
        db_mock.delete_fixture.side_effect = RuntimeError("DB locked")

        confirm_view = DeleteConfirmView(
            db_mock,
            str(mock_interaction_admin.user.id),
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
    async def test_cleanup_skips_when_channel_not_in_cache(self):
        bot = MagicMock(spec=discord.Client)
        bot.get_channel.return_value = None

        await _cleanup_discord_announcement(bot, "111", "222", week_number=5)

        bot.get_channel.assert_called_once_with(111)

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

    @pytest.mark.asyncio
    async def test_cleanup_logs_warning_on_error(self):
        bot = MagicMock(spec=discord.Client)
        channel = AsyncMock()
        channel.fetch_message.side_effect = Exception("Discord unavailable")
        bot.get_channel.return_value = channel

        with patch("typer_bot.commands.admin_panel.fixtures.logger") as mock_logger:
            await _cleanup_discord_announcement(bot, "111", "222", week_number=5)

        mock_logger.warning.assert_called_once()


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
            3, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            4, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
        )
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        correct_button = next(
            child for child in view.children if getattr(child, "label", None) == "Correct Results"
        )
        await correct_button.callback(mock_interaction_admin)

        assert (
            "Use `/admin results enter` first"
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
            14, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            23, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            12, games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0"] * len(games))

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        content = mock_interaction_admin.response_sent[-1]["content"]
        assert len(content) <= 1900
        assert "content truncated" in content


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
            24, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            25, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            28, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            26, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            30, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            31, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            27, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            29, sample_games, datetime.now(UTC) + timedelta(days=1)
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
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            9, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            15, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
    async def test_replace_prediction_modal_recovers_when_prediction_disappears(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            21, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        view = PredictionsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
    async def test_correct_results_modal_prefills_stored_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
            16, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id)
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
