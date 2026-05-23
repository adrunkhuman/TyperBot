from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import selected_option_labels as _selected_option_labels
from tests.conftest import MockUser
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    CorrectResultsModal,
    EnterResultsModal,
    PredictionsPanelView,
    ReplacePredictionModal,
    ResultsPanelView,
)


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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 24, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 25, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 28, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 26, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        assert await admin_cog.db.results.get_results(fixture_id) == ["2-1", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_enter_results_modal_save_results_supports_cancelled_games(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 30, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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

        assert await admin_cog.db.results.get_results(fixture_id) == ["x", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_enter_results_confirm_shows_save_error_when_fixture_becomes_unavailable(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 31, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None

        modal = EnterResultsModal(fixture, admin_cog.db)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)
        await admin_cog.db.scores.save_scores(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 27, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        assert await admin_cog.db.results.get_results(fixture_id) is None

    @pytest.mark.asyncio
    async def test_enter_results_confirm_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 29, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        assert await admin_cog.db.results.get_results(fixture_id) is None

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_rechecks_admin_permission(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "user-1", "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 9, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        await admin_cog.db.predictions.delete_prediction(fixture_id, "user-1")

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 15, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "user-1", "111111")
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
        updated_prediction = await admin_cog.db.predictions.get_prediction(
            fixture_id, "user-1", "111111"
        )
        assert updated_prediction is not None
        assert updated_prediction["predictions"] == ["2-1", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_dms_affected_user(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 36, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 37, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 41, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111")
        assert fixture is not None
        assert prediction is not None

        modal = ReplacePredictionModal(view, fixture, prediction)
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        await modal.on_submit(mock_interaction_admin)

        assert "updated your Week 41 prediction" in target_user.dm_sent[-1]

    @pytest.mark.asyncio
    async def test_replace_prediction_modal_recovers_when_prediction_disappears(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 21, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "user-1", "111111")
        assert fixture is not None
        assert prediction is not None
        await admin_cog.db.predictions.delete_prediction(fixture_id, "user-1")

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 2, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

        await modal.on_submit(mock_interaction_admin)

        assert "Fixture not found" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_correct_results_modal_rejects_cross_guild_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "guild-2", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        original_results = ["0-0", "1-1", "2-2"]
        await admin_cog.db.results.save_results(fixture_id, original_results)
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "guild-2")
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
        assert await admin_cog.db.results.get_results(fixture_id) == original_results

    @pytest.mark.asyncio
    async def test_correct_results_modal_prefills_stored_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 16, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert (
            "Saved corrected results for week 16."
            in mock_interaction_admin.response_sent[-1]["content"]
        )
        assert await admin_cog.db.results.get_results(fixture_id) == ["2-1", "1-1", "0-2"]

    @pytest.mark.asyncio
    async def test_correct_results_modal_dms_all_fixture_participants(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 38, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.predictions.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert "Results were corrected for Week 38." in user_one.dm_sent[-1]
        assert "Results were corrected for Week 38." in user_two.dm_sent[-1]

    @pytest.mark.asyncio
    async def test_correct_results_modal_continues_after_one_dm_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 42, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.predictions.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 43, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await admin_cog.db.predictions.save_prediction(
            fixture_id, "111", "User One", ["1-0", "1-1", "0-0"], False
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture, ["1-0", "1-1", "0-0"])
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        await modal.on_submit(mock_interaction_admin)

        assert "Results were corrected for Week 43." in user_two.dm_sent[-1]
