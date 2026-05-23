from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import typer_bot.commands.admin_panel.unified_actions as unified_actions
from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from typer_bot.commands.admin_panel import (
    EnterResultsModal,
    PostResultsConfirmView,
    UnifiedAdminPanelView,
)
from typer_bot.utils import now


class TestFixturePanelResultsActions:
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
    async def test_unified_panel_hides_enter_results_button_after_results_are_saved(
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

        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Correct Results") is True

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_posts_results(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
        monkeypatch,
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
        mock_interaction_admin.message = MagicMock()
        mock_interaction_admin.message.edit = AsyncMock()
        post_calculation_result = AsyncMock()
        monkeypatch.setattr(unified_actions, "post_calculation_result", post_calculation_result)

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

        assert (
            admin_cog.get_calculate_cooldown("111111", str(mock_interaction_admin.user.id))
            is not None
        )
        post_calculation_result.assert_awaited_once()
        assert post_calculation_result.call_args.args[:3] == (
            admin_cog.bot,
            admin_cog.db,
            mock_interaction_admin,
        )
        assert view.selection.fixture_label == "Week 45 [CLOSED]"
        assert _has_button(view, "Calculate Scores") is False
        assert _has_button(view, "Delete Fixture") is False
        assert mock_interaction_admin.message.edit.await_count == 1

    @pytest.mark.asyncio
    async def test_stale_calculate_scores_button_refreshes_when_fixture_already_scored(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
        monkeypatch,
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
        mock_interaction_admin.message = MagicMock()
        mock_interaction_admin.message.edit = AsyncMock()
        post_calculation_result = AsyncMock()
        monkeypatch.setattr(unified_actions, "post_calculation_result", post_calculation_result)
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
        stale_button = _get_button(view, "Calculate Scores")
        await admin_cog.db.recalculate_fixture_scores(fixture_id)

        await stale_button.callback(mock_interaction_admin)

        assert "no longer open" in mock_interaction_admin.response_sent[-1]["content"]
        post_calculation_result.assert_not_awaited()
        assert view.selection.fixture_label == "Week 45 [CLOSED]"
        assert _has_button(view, "Calculate Scores") is False
        assert _has_button(view, "Delete Fixture") is False
        assert mock_interaction_admin.message.edit.await_count == 1

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_rejects_active_cooldown(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
        monkeypatch,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 47, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        admin_cog.record_calculate_cooldown(
            "111111", str(mock_interaction_admin.user.id), current_time=now().timestamp()
        )
        admin_cog.service.calculate_fixture_scores = AsyncMock()
        post_calculation_result = AsyncMock()
        monkeypatch.setattr(unified_actions, "post_calculation_result", post_calculation_result)

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
        post_calculation_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_handles_service_error(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
        monkeypatch,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 48, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        post_calculation_result = AsyncMock()
        monkeypatch.setattr(unified_actions, "post_calculation_result", post_calculation_result)
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

        assert (
            mock_interaction_admin.response_sent[-1]["content"]
            == "No predictions found for this fixture"
        )
        post_calculation_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unified_panel_calculate_scores_button_requires_bot_context(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
        monkeypatch,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 49, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        admin_cog.service.calculate_fixture_scores = AsyncMock()
        post_calculation_result = AsyncMock()
        monkeypatch.setattr(unified_actions, "post_calculation_result", post_calculation_result)

        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=None,
        )
        await view.load_fixture_options()
        view.fixture_select._values = [str(fixture_id)]
        await view.fixture_select.callback(mock_interaction_admin)

        calculate_button = _get_button(view, "Calculate Scores")
        await calculate_button.callback(mock_interaction_admin)

        assert "unavailable" in mock_interaction_admin.response_sent[-1]["content"]
        admin_cog.service.calculate_fixture_scores.assert_not_awaited()
        post_calculation_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_button_opens_confirmation(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        command_channel = MagicMock(spec=discord.TextChannel)
        command_channel.id = 999999
        league_channel = MagicMock(spec=discord.TextChannel)
        league_channel.id = 123456
        league_channel.send = AsyncMock()
        mock_interaction_admin.channel = command_channel
        admin_cog.bot.get_channel.return_value = None
        admin_cog.bot.fetch_channel = AsyncMock(return_value=league_channel)
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

        admin_cog.bot.get_channel.assert_called_with(123456)
        admin_cog.bot.fetch_channel.assert_awaited_once_with(123456)
        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        assert isinstance(confirm_view, PostResultsConfirmView)
        assert confirm_view.channel is league_channel

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_only_previews_current_guild_scores(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = mock_interaction_admin.channel.id
        channel.send = AsyncMock()
        mock_interaction_admin.channel = channel
        admin_cog.bot.get_channel.return_value = channel
        deadline = datetime.now(UTC) - timedelta(days=1)
        current_fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, ["Team A - Team B"], deadline
        )
        other_fixture_id = await admin_cog.db.create_fixture(
            "guild-2", 2, ["Team C - Team D"], deadline
        )
        await admin_cog.db.save_scores(
            current_fixture_id,
            [
                {
                    "user_id": "current-user",
                    "user_name": "Current Guild",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )
        await admin_cog.db.save_scores(
            other_fixture_id,
            [
                {
                    "user_id": "other-user",
                    "user_name": "Other Guild",
                    "points": 9,
                    "exact_scores": 3,
                    "correct_results": 3,
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
        post_button = _get_button(view, "Re-post Results")
        await post_button.callback(mock_interaction_admin)

        content = mock_interaction_admin.response_sent[-1]["content"]
        assert "Current Guild" in content
        assert "Other Guild" not in content

    @pytest.mark.asyncio
    async def test_unified_panel_post_results_button_rejects_unavailable_league_channel(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        admin_cog.db.get_last_fixture_scores = AsyncMock(return_value={"scores": []})
        admin_cog.db.get_standings = AsyncMock(return_value=[])
        admin_cog.bot.get_channel.return_value = None
        admin_cog.bot.fetch_channel = AsyncMock(
            side_effect=discord.InvalidData("unknown channel type")
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

        assert (
            "configured league channel is unavailable"
            in mock_interaction_admin.response_sent[-1]["content"].lower()
        )

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
