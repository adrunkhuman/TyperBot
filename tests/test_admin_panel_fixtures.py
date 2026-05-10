from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from tests.admin_panel_helpers import option_values as _option_values
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    CreateFixtureModal,
    DeleteConfirmView,
    EnterResultsModal,
    FixturesPanelView,
    NewSeasonModal,
    PostResultsConfirmView,
    PredictionsPanelView,
    ResultsPanelView,
    UnifiedAdminPanelView,
)
from typer_bot.commands.admin_panel.fixtures import _cleanup_discord_announcement
from typer_bot.database import Database
from typer_bot.utils import now


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
        current_guild_fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, deadline
        )
        other_guild_fixture_id = await admin_cog.db.create_fixture(
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
        assert new_week == 1

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

        assert (
            admin_cog.get_calculate_cooldown("111111", str(mock_interaction_admin.user.id))
            is not None
        )
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
            "111111", str(mock_interaction_admin.user.id), current_time=now().timestamp()
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
    async def test_unified_panel_post_results_only_previews_current_guild_scores(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = mock_interaction_admin.channel.id
        mock_interaction_admin.channel = channel
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
        db_mock.get_guild_config.return_value = {
            "admin_role_id": str(
                mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id).roles[0].id
            ),
            "league_channel_id": "123456",
        }
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
