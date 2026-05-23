from datetime import UTC, datetime, timedelta

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from tests.admin_panel_helpers import selected_option_labels as _selected_option_labels
from tests.conftest import MockMessage, MockThread, MockUser
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    CorrectResultsModal,
    ResultsPanelView,
    UnifiedAdminPanelView,
)


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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 3, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])

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

    @pytest.mark.asyncio
    async def test_results_panel_buttons_enable_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 4, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 14, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])

        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 23, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        view = ResultsPanelView(
            admin_cog.db, admin_cog.service, str(mock_interaction_admin.user.id), "111111"
        )
        await view.load_fixture_options()
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 12, games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0"] * len(games))

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 32, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
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

        fixture = await admin_cog.db.fixtures.get_fixture_by_id(fixture_id, "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 33, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
            fixture_id,
            "111",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])

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
        await admin_cog.db.fixtures.delete_fixture(fixture_id)

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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 50, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 51, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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

        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 52, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
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

        assert await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111") is None
        assert "rejected" in target_user.dm_sent[-1].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("button_label", "expected_text"),
        [("Approve Late", "approved by an admin"), ("Reject Late", "rejected by an admin")],
    )
    async def test_unified_panel_partial_review_edits_public_bot_post(
        self,
        button_label,
        expected_text,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 70, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.fixtures.update_fixture_announcement(
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
        await admin_cog.db.predictions.save_prediction(
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

        review_button = _get_button(view, button_label)
        await review_button.callback(mock_interaction_admin)

        assert expected_text in public_message.content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("button_label", "expected_added_reaction"),
        [("Reject Late", "❌"), ("Approve Late", "✅")],
    )
    async def test_unified_panel_partial_review_updates_thread_reaction(
        self,
        button_label,
        expected_added_reaction,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 71, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.fixtures.update_fixture_announcement(
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
        await admin_cog.db.predictions.save_prediction(
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

        review_button = _get_button(view, button_label)
        await review_button.callback(mock_interaction_admin)

        assert ("⏳", admin_cog.bot.user.id) in user_message.reactions_removed
        assert expected_added_reaction in user_message.reactions_added

    @pytest.mark.asyncio
    async def test_unified_panel_approve_partial_prediction_ignores_bad_fixture_thread_id(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 74, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.fixtures.update_fixture_announcement(
            fixture_id,
            message_id="not-a-thread-id",
            channel_id="123456",
        )
        await admin_cog.db.predictions.save_prediction(
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

        prediction = await admin_cog.db.predictions.get_prediction(fixture_id, "111", "111111")
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 53, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await admin_cog.db.predictions.save_prediction(
            fixture_id,
            "999",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await admin_cog.service.calculate_fixture_scores(fixture_id, "111111")
        await admin_cog.db.predictions.save_prediction(
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

        standings = await admin_cog.db.scores.get_standings("111111")
        assert {row["user_id"] for row in standings} == {"999", "111"}

    @pytest.mark.asyncio
    async def test_unified_panel_reject_partial_prediction_recalculates_scores(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 54, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await admin_cog.db.predictions.save_prediction(
            fixture_id,
            "999",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await admin_cog.service.calculate_fixture_scores(fixture_id, "111111")
        await admin_cog.db.predictions.save_prediction(
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

        standings = await admin_cog.db.scores.get_standings("111111")
        assert {row["user_id"] for row in standings} == {"999"}
