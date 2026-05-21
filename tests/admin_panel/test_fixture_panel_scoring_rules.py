from datetime import UTC, datetime, timedelta

import pytest

from tests.admin_panel_helpers import get_button as _get_button
from tests.admin_panel_helpers import has_button as _has_button
from tests.conftest import MockInteraction, MockUser
from typer_bot.commands.admin_panel import (
    ScoringRulesModal,
    UnifiedAdminPanelView,
)


class TestFixturePanelScoringRules:
    @pytest.mark.asyncio
    async def test_unified_panel_scoring_rules_button_opens_modal(
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
        scoring_button = _get_button(view, "Scoring Rules")

        await scoring_button.callback(mock_interaction_admin)

        modal = mock_interaction_admin.modal_sent["modal"]
        assert isinstance(modal, ScoringRulesModal)
        assert modal.exact_input.default == "3"
        assert modal.outcome_input.default == "1"
        assert modal.wrong_input.default == "0"
        assert modal.late_input.default == "0"

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_updates_active_season_rules(
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
        modal = ScoringRulesModal(view)
        modal.exact_input._value = "5"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"

        await modal.on_submit(mock_interaction_admin)

        assert await admin_cog.db.get_active_scoring_rules("111111") == {
            "exact_score_points": 5,
            "correct_outcome_points": 2,
            "wrong_outcome_points": 1,
            "late_prediction_points": 1,
        }
        content = mock_interaction_admin.response_sent[-1]["content"]
        assert "Updated active-season scoring rules." in content
        assert "Scoring: exact 5, outcome 2, wrong 1, late 1" in content

    @pytest.mark.asyncio
    async def test_scoring_rules_button_refreshes_modal_defaults(
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
        await admin_cog.db.update_active_scoring_rules("111111", {"exact_score_points": 5})

        scoring_button = _get_button(view, "Scoring Rules")
        await scoring_button.callback(mock_interaction_admin)

        modal = mock_interaction_admin.modal_sent["modal"]
        assert modal.exact_input.default == "5"

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_rejects_stale_season_submit(
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
        old_season_rules = view.active_season["scoring_rules"]
        modal = ScoringRulesModal(view)
        modal.exact_input._value = "5"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"
        await admin_cog.db.start_new_season("111111", "Next Season")

        await modal.on_submit(mock_interaction_admin)

        assert "active season changed" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_active_scoring_rules("111111") == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }
        seasons = await admin_cog.db.get_seasons("111111")
        assert seasons[0]["status"] == "archived"
        assert seasons[0]["scoring_rules"] == old_season_rules

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_rejects_non_owner(
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
        modal = ScoringRulesModal(view)
        modal.exact_input._value = "5"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"
        outsider = MockInteraction(
            user=MockUser(user_id="999999", name="Outsider"),
            guild=mock_interaction_admin.guild,
            channel=mock_interaction_admin.channel,
        )

        await modal.on_submit(outsider)

        assert "permission" in outsider.response_sent[-1]["content"]
        assert await admin_cog.db.get_active_scoring_rules("111111") == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_rechecks_admin_permission(
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
        modal = ScoringRulesModal(view)
        modal.exact_input._value = "5"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_active_scoring_rules("111111") == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_blocks_changes_after_scores_exist(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        fixture_id = await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await admin_cog.db.save_results(fixture_id, ["2-1", "1-1", "0-0"])
        await admin_cog.db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["2-1", "1-1", "0-0"],
            False,
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
        assert _has_button(view, "Scoring Rules") is True

        modal = ScoringRulesModal(view)
        modal.exact_input._value = "5"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"
        await admin_cog.db.recalculate_fixture_scores(fixture_id)

        await modal.on_submit(mock_interaction_admin)

        assert "Cannot change scoring rules" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_active_scoring_rules("111111") == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

    @pytest.mark.asyncio
    async def test_scoring_rules_modal_rejects_invalid_values(
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
        modal = ScoringRulesModal(view)
        modal.exact_input._value = "many"
        modal.outcome_input._value = "2"
        modal.wrong_input._value = "1"
        modal.late_input._value = "1"

        await modal.on_submit(mock_interaction_admin)

        assert "whole numbers" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_stale_scoring_rules_button_refreshes_when_scores_now_exist(
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
        view = UnifiedAdminPanelView(
            admin_cog.db,
            admin_cog.service,
            str(mock_interaction_admin.user.id),
            "111111",
            admin_commands=admin_cog,
            bot=admin_cog.bot,
        )
        await view.load_fixture_options()
        stale_button = _get_button(view, "Scoring Rules")
        await admin_cog.db.recalculate_fixture_scores(fixture_id)

        await stale_button.callback(mock_interaction_admin)

        assert not hasattr(mock_interaction_admin, "modal_sent")
        assert "Scoring rules are locked" in mock_interaction_admin.response_sent[-1]["content"]
        assert _has_button(view, "Scoring Rules") is False
