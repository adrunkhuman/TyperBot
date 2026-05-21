from datetime import UTC, datetime, timedelta

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from typer_bot.commands.admin_panel import (
    NewSeasonModal,
    UnifiedAdminPanelView,
)


class TestFixturePanelSeasons:
    @pytest.mark.asyncio
    async def test_unified_panel_shows_active_season_and_new_season_button(
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

        assert "Active season: Default Season" in view.render_content()
        assert "Scoring: exact 3, outcome 1, wrong 0, late 0" in view.render_content()
        assert _has_button(view, "Scoring Rules") is True
        assert _has_button(view, "New Season") is True

    @pytest.mark.asyncio
    async def test_unified_panel_new_season_button_opens_modal(
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
        new_season_button = _get_button(view, "New Season")

        await new_season_button.callback(mock_interaction_admin)

        assert isinstance(mock_interaction_admin.modal_sent["modal"], NewSeasonModal)

    @pytest.mark.asyncio
    async def test_new_season_modal_blocks_open_fixtures(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
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
        view.current_prediction = {"pending_partial_approval": True}
        view.has_user_overflow = True
        modal = NewSeasonModal(view)
        modal.name_input._value = "2026/27"

        await modal.on_submit(mock_interaction_admin)

        assert "Close all open fixtures" in mock_interaction_admin.response_sent[-1]["content"]
        assert (await admin_cog.db.get_active_season("111111"))["name"] == "Default Season"

    @pytest.mark.asyncio
    async def test_new_season_modal_starts_season_and_refreshes_panel(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
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
        modal = NewSeasonModal(view)
        modal.name_input._value = "2026/27"

        await modal.on_submit(mock_interaction_admin)
        _new_fixture_id, new_week = await admin_cog.db.create_next_fixture(
            "111111", sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        content = mock_interaction_admin.response_sent[-1]["content"]
        assert "Active season: 2026/27" in content
        assert "Started new active season: 2026/27" in content
        assert view.current_prediction is None
        assert view.has_user_overflow is False
        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Calculate Scores") is False
        assert _has_button(view, "Correct Results") is False
        assert _has_button(view, "Delete Fixture") is False
        assert new_week == 1
