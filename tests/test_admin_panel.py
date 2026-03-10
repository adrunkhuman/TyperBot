"""Tests for admin panel interactions."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import MockInteraction, MockUser
from typer_bot.commands.admin_commands import (
    AdminCommands,
    AdminPanelHomeView,
    CorrectResultsModal,
    PredictionsPanelView,
    ReplacePredictionModal,
    ResultsPanelView,
)


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
        view = PredictionsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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
        view = PredictionsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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

        home_view = AdminPanelHomeView(admin_cog, str(mock_interaction_admin.user.id))
        predictions_button = next(
            child for child in home_view.children if getattr(child, "label", None) == "Predictions"
        )
        await predictions_button.callback(mock_interaction_admin)

        edited_view = mock_interaction_admin.response_sent[-1]["view"]
        assert edited_view.user_select.disabled is True
        assert len(edited_view.user_select.options) == 1
        assert edited_view.user_select.options[0].label == "No predictions available"

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

        view = PredictionsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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

        view = PredictionsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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

        view = AdminPanelHomeView(admin_cog, str(mock_interaction_admin.user.id))
        fixture_button = next(
            child for child in view.children if getattr(child, "label", None) == "Fixtures"
        )
        await fixture_button.callback(mock_interaction_admin)

        edited_view = mock_interaction_admin.response_sent[-1]["view"]
        assert edited_view.fixture_select.disabled is False
        assert edited_view.fixture_select.options[0].label == "Week 4 [OPEN]"


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

        view = ResultsPanelView(admin_cog, str(mock_interaction_admin.user.id))
        await view.load_fixture_options()

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        correct_button = next(
            child for child in view.children if getattr(child, "label", None) == "Correct Results"
        )
        await correct_button.callback(mock_interaction_admin)

        assert mock_interaction_admin.modal_sent["modal"].title == "Correct Week 3 Results"

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

        view = ResultsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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


class TestAdminPanelModals:
    """Modal submit paths should reject stale permissions."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

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
        view = PredictionsPanelView(admin_cog, str(mock_interaction_admin.user.id))
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
        view = ResultsPanelView(admin_cog, str(mock_interaction_admin.user.id))
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture)
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
        view = ResultsPanelView(admin_cog, str(mock_interaction_admin.user.id))
        fixture = await admin_cog.db.get_fixture_by_id(fixture_id)
        assert fixture is not None

        modal = CorrectResultsModal(view, fixture)
        modal.results_input._value = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await admin_cog.db.delete_fixture(fixture_id)

        await modal.on_submit(mock_interaction_admin)

        assert "Fixture not found" in mock_interaction_admin.response_sent[-1]["content"]
