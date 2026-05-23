from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from tests.admin_panel_helpers import selected_option_labels as _selected_option_labels
from tests.conftest import MockInteraction, MockUser
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    PredictionsPanelView,
    UnifiedAdminPanelView,
)


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
        await admin_cog.db.fixtures.create_fixture(
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
        assert _has_button(unified_view, "Replace Prediction") is False
        assert _has_button(unified_view, "Toggle Late Waiver") is False

    @pytest.mark.asyncio
    async def test_prediction_panel_buttons_enable_as_selections_are_made(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 17, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(30):
            await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 19, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.predictions.save_prediction(
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
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 22, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.predictions.save_prediction(
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
            await admin_cog.db.predictions.delete_prediction(fixture_id, f"user-{index}")

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 20, games, datetime.now(UTC) + timedelta(days=1)
        )
        for index in range(26):
            await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 34, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 35, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 40, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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

        assert "late waiver" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    async def test_prediction_panel_replace_recovers_when_prediction_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 13, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        await admin_cog.db.predictions.delete_prediction(fixture_id, "user-1")

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 11, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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

        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "user-1", "111111")
        assert prediction is not None
        assert prediction["late_penalty_waived"] == 1
        assert "waiver enabled" in mock_interaction_admin.response_sent[-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_prediction_panel_toggle_waiver_recovers_when_prediction_is_deleted(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 18, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        await admin_cog.db.predictions.delete_prediction(fixture_id, "user-1")

        toggle_button = _get_button(view, "Toggle Late Waiver")
        await toggle_button.callback(mock_interaction_admin)

        assert view.selection.user_id is None
        assert "no longer available" in mock_interaction_admin.response_sent[-1]["content"].lower()
        assert _get_button(view, "Toggle Late Waiver").disabled is True
