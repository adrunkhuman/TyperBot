from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from tests.admin_panel_helpers import option_values as _option_values
from typer_bot.commands.admin_panel import (
    DeleteConfirmView,
    FixturesPanelView,
    PredictionsPanelView,
    ResultsPanelView,
    UnifiedAdminPanelView,
)
from typer_bot.database import Database


class TestFixturePanelSelection:
    @pytest.mark.asyncio
    async def test_fixture_button_populates_open_fixture_options(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.fixtures.create_fixture(
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
        current_guild_fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 1, sample_games, deadline
        )
        other_guild_fixture_id = await admin_cog.db.fixtures.create_fixture(
            "guild-2", 2, sample_games, deadline
        )

        view = view_cls(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
        )
        await view.load_fixture_options()

        option_values = _option_values(view.fixture_select)
        assert str(current_guild_fixture_id) in option_values
        assert str(other_guild_fixture_id) not in option_values

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_button_enables_after_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
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
        fixture_id = await admin_cog.db.fixtures.create_fixture(
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
    async def test_unified_panel_hides_contextual_actions_until_fixture_selection(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.fixtures.create_fixture(
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

        assert _has_button(view, "Calculate Scores") is False
        assert _has_button(view, "Delete Fixture") is False

        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert _has_button(view, "Enter Results") is True
        assert _has_button(view, "Calculate Scores") is True
        assert _has_button(view, "Correct Results") is False
        assert _has_button(view, "Delete Fixture") is True

        await admin_cog.db.results.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Calculate Scores") is True
        assert _has_button(view, "Correct Results") is True

        await admin_cog.db.scores.save_scores(
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
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Calculate Scores") is False
        assert _has_button(view, "Correct Results") is True
        assert _has_button(view, "Delete Fixture") is False

    @pytest.mark.asyncio
    async def test_fixture_panel_delete_confirm_shows_error_on_db_failure(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Silent DB failures surface as a visible error instead of timing out the interaction."""
        fixture_id = await admin_cog.db.fixtures.create_fixture(
            "111111", 7, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        db_mock = AsyncMock(spec=Database)
        db_mock.guild_config = MagicMock()
        db_mock.guild_config.get_guild_config = AsyncMock(
            return_value={
                "admin_role_id": str(
                    mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
                    .roles[0]
                    .id
                ),
                "league_channel_id": "123456",
            }
        )
        db_mock.fixtures = MagicMock()
        db_mock.fixtures.delete_fixture = AsyncMock(side_effect=RuntimeError("DB locked"))

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
