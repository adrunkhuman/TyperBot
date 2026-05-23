from datetime import UTC, datetime, timedelta

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from typer_bot.commands.admin_panel import (
    UnifiedAdminPanelView,
)


class TestFixturePanelPendingPartials:
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 55, sample_games, datetime.now(UTC) + timedelta(days=1)
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

        assert _has_button(view, "Review Late") is True

    @pytest.mark.asyncio
    async def test_unified_panel_hides_other_guild_pending_partials(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "guild-2", 55, sample_games, datetime.now(UTC) + timedelta(days=1)
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

        assert _has_button(view, "Review Late") is False

    @pytest.mark.asyncio
    async def test_unified_panel_review_pending_button_jumps_to_pending_submission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 56, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
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

        review_button = _get_button(view, "Review Late")
        await review_button.callback(mock_interaction_admin)

        assert view.selection.fixture_label == "Week 56 [OPEN]"
        assert view.selection.user_id == "111"
        assert _has_button(view, "Approve Late") is True
        assert _has_button(view, "Reject Late") is True
        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Correct Results") is True

    @pytest.mark.asyncio
    async def test_unified_panel_review_pending_button_cycles_pending_submissions(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_a = await admin_cog.db.fixtures.create_fixture(
            "111111", 57, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture_b = await admin_cog.db.fixtures.create_fixture(
            "111111", 58, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.predictions.save_prediction(
            fixture_a,
            "111",
            "User One",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )
        await admin_cog.db.predictions.save_prediction(
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
