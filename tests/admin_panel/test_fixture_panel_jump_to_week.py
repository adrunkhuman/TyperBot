from datetime import UTC, datetime, timedelta

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from typer_bot.commands.admin_panel import (
    UnifiedAdminPanelView,
)


class TestFixturePanelJumpToWeek:
    @pytest.mark.asyncio
    async def test_unified_panel_jump_to_week_reaches_older_open_fixture(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        first_fixture_id = None
        for week in range(1, 28):
            fixture_id = await admin_cog.db.create_fixture("111111", week, sample_games, deadline)
            if week == 1:
                first_fixture_id = fixture_id
        assert first_fixture_id is not None
        await admin_cog.db.save_results(first_fixture_id, ["1-0", "1-1", "0-0"])

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
        assert _has_button(view, "Enter Results") is False
        assert _has_button(view, "Calculate Scores") is True
        assert _has_button(view, "Correct Results") is True

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
