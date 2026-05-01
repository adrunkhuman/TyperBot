"""Tests for admin Discord commands."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.commands.admin_commands import (
    CALCULATE_COOLDOWN,
    AdminCommands,
)
from typer_bot.commands.admin_panel import PostResultsConfirmView, UnifiedAdminPanelView
from typer_bot.utils import now
from typer_bot.utils.permissions import is_admin


class TestAdminOnlyDecorator:
    """Test suite for admin permission checking."""

    @pytest.mark.asyncio
    async def test_rejects_non_admin_users(self, mock_interaction):
        """Non-admin users are blocked from admin commands."""
        result = is_admin(mock_interaction)
        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_admin_users(self, mock_interaction_admin):
        """Admin users have access to league management commands."""
        result = is_admin(mock_interaction_admin)
        assert result is True

    @pytest.mark.asyncio
    async def test_rejects_dm_interactions(self, mock_interaction_admin):
        """DM interactions without guild context are rejected - role verification requires server membership."""
        mock_interaction_admin.guild = None
        result = is_admin(mock_interaction_admin)
        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_typer_admin_role(self, mock_interaction_admin):
        """The typer-admin role grants league management access without requiring full server admin privileges."""
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        mock_role = MagicMock()
        mock_role.name = "typer-admin"
        member.roles = [mock_role]
        result = is_admin(mock_interaction_admin)
        assert result is True


class TestAdminPanelEntry:
    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_admin_panel_opens_unified_view(self, admin_cog, mock_interaction_admin):
        await admin_cog.panel.callback(admin_cog, mock_interaction_admin)

        assert isinstance(mock_interaction_admin.response_sent[0]["view"], UnifiedAdminPanelView)

    def test_admin_group_exposes_panel_command(self, admin_cog):
        assert any(command.name == "panel" for command in admin_cog.admin.commands)


class TestResultsPostFlow:
    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_post_results_view_posts_without_mentions(
        self, mock_text_channel, mock_interaction_admin
    ):
        """NO branch posts standings without pinging participants."""
        fixture_data = {
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
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 3,
                "total_exact": 1,
                "total_correct": 1,
            }
        ]
        view = PostResultsConfirmView(fixture_data, standings, mock_text_channel)
        no_button = next(child for child in view.children if child.label == "No Mentions")

        await no_button.callback(mock_interaction_admin)

        assert (
            mock_interaction_admin.response_sent[-1]["content"]
            == "Results posted without mentions!"
        )
        assert len(mock_text_channel.messages_sent) == 1
        assert "Participants" not in mock_text_channel.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_post_results_view_posts_with_mentions(
        self, mock_text_channel, mock_interaction_admin
    ):
        """YES branch appends participant mentions to the public post."""
        fixture_data = {
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
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 3,
                "total_exact": 1,
                "total_correct": 1,
            }
        ]
        view = PostResultsConfirmView(fixture_data, standings, mock_text_channel)
        yes_button = next(child for child in view.children if child.label == "Mention Users")

        await yes_button.callback(mock_interaction_admin)

        assert (
            mock_interaction_admin.response_sent[-1]["content"] == "Results posted with mentions!"
        )
        assert "<@123>" in mock_text_channel.messages_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_post_results_view_uses_followup_when_channel_send_fails(
        self, mock_text_channel, mock_interaction_admin
    ):
        """Post failures happen after the interaction is acknowledged, so errors go to followup."""
        fixture_data = {
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
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 3,
                "total_exact": 1,
                "total_correct": 1,
            }
        ]
        mock_text_channel.send = AsyncMock(side_effect=RuntimeError("boom"))
        view = PostResultsConfirmView(fixture_data, standings, mock_text_channel)
        no_button = next(child for child in view.children if child.label == "No Mentions")

        await no_button.callback(mock_interaction_admin)

        assert (
            mock_interaction_admin.response_sent[-1]["content"]
            == "Results posted without mentions!"
        )
        assert mock_interaction_admin.followup_sent[-1]["content"] == "Failed to post results: boom"

    @pytest.mark.asyncio
    async def test_post_results_view_aborts_when_acknowledgement_fails(
        self, mock_text_channel, mock_interaction_admin
    ):
        fixture_data = {
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
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 3,
                "total_exact": 1,
                "total_correct": 1,
            }
        ]
        mock_interaction_admin.response.edit_message = AsyncMock(
            side_effect=RuntimeError("expired")
        )
        view = PostResultsConfirmView(fixture_data, standings, mock_text_channel)
        no_button = next(child for child in view.children if child.label == "No Mentions")

        await no_button.callback(mock_interaction_admin)

        assert mock_text_channel.messages_sent == []

    @pytest.mark.asyncio
    async def test_post_results_view_mentions_branch_aborts_when_acknowledgement_fails(
        self, mock_text_channel, mock_interaction_admin
    ):
        fixture_data = {
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
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 3,
                "total_exact": 1,
                "total_correct": 1,
            }
        ]
        mock_interaction_admin.response.edit_message = AsyncMock(
            side_effect=RuntimeError("expired")
        )
        view = PostResultsConfirmView(fixture_data, standings, mock_text_channel)
        yes_button = next(child for child in view.children if child.label == "Mention Users")

        await yes_button.callback(mock_interaction_admin)

        assert mock_text_channel.messages_sent == []


class TestCalculationPostFormat:
    """Test that the calculation announcement includes entered match results."""

    def test_format_fixture_results_included_in_post(self, sample_games):
        from typer_bot.utils import format_fixture_results

        games = sample_games
        results = ["2-1", "1-1", "0-2"]
        output = format_fixture_results(games, results, week_number=3)

        assert "Week 3 Results" in output
        for game, result in zip(games, results, strict=False):
            assert game in output
            assert result in output


class TestCooldownLogic:
    """Test suite for rate limiting cooldown."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    def test_cooldown_enforced(self, admin_cog):
        """Rate limiting prevents leaderboard recalculation spam."""
        import time

        user_id = "user123"
        current_time = time.time()
        admin_cog.record_calculate_cooldown(user_id, current_time=current_time)

        remaining = admin_cog.get_calculate_cooldown_remaining(
            user_id,
            current_time=current_time,
            cooldown_seconds=CALCULATE_COOLDOWN,
        )
        assert remaining > 0

    def test_cooldown_expires(self, admin_cog):
        """Cooldown expires after 30 seconds."""
        import time

        user_id = "user123"
        current_time = time.time()
        admin_cog.record_calculate_cooldown(user_id, current_time=current_time - 31)

        remaining = admin_cog.get_calculate_cooldown_remaining(
            user_id,
            current_time=current_time,
            cooldown_seconds=CALCULATE_COOLDOWN,
        )
        assert remaining == 0.0

    def test_cleanup_expired_state_removes_stale_entries(self, admin_cog):
        admin_cog.record_calculate_cooldown(
            "user123",
            current_time=now().timestamp() - timedelta(hours=2).total_seconds(),
        )

        removed = admin_cog.cleanup_expired_state()

        assert removed == 1
