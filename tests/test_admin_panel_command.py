from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.admin_panel_helpers import has_button as _has_button
from typer_bot.commands.admin_commands import AdminCommands
from typer_bot.commands.admin_panel import (
    CreateFixtureModal,
)


class TestAdminPanelCommand:
    """The slash entrypoint should open the panel."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_create_fixture_modal_shows_preview_with_default_deadline(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "Week 1 Fixture Preview" in mock_interaction_admin.response_sent[-1]["content"]
        assert "Deadline:" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_default_deadline_rolls_to_next_friday_after_cutoff(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        with patch(
            "typer_bot.commands.admin_panel.modals.now",
            return_value=datetime(2026, 4, 17, 19, 0, tzinfo=UTC),
        ):
            await modal.on_submit(mock_interaction_admin)

        view = mock_interaction_admin.response_sent[-1]["view"]
        assert view.deadline.year == 2026
        assert view.deadline.month == 4
        assert view.deadline.day == 24
        assert view.deadline.hour == 18

    @pytest.mark.asyncio
    async def test_create_fixture_modal_warns_when_other_fixtures_are_open(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.create_fixture(
            "111111", 5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "already open" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_empty_games_list(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "   \n   "
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "No games provided" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_more_than_100_games(
        self,
        admin_cog,
        mock_interaction_admin,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(
            f"Team {index:03d} - Team {index + 1:03d}" for index in range(101)
        )
        modal.deadline_input._value = ""

        await modal.on_submit(mock_interaction_admin)

        assert "Too many games" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rejects_invalid_deadline(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "tomorrow 6pm"

        await modal.on_submit(mock_interaction_admin)

        assert "Invalid date format" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_modal_parses_supported_deadline_formats(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "20.04.2026 18:00"

        await modal.on_submit(mock_interaction_admin)

        first_view = mock_interaction_admin.response_sent[-1]["view"]
        assert first_view.deadline.year == 2026
        assert first_view.deadline.month == 4
        assert first_view.deadline.day == 20

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "21/04/2026 18:00"

        await modal.on_submit(mock_interaction_admin)

        second_view = mock_interaction_admin.response_sent[-1]["view"]
        assert second_view.deadline.year == 2026
        assert second_view.deadline.month == 4
        assert second_view.deadline.day == 21

    @pytest.mark.asyncio
    async def test_create_fixture_modal_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await modal.on_submit(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_creates_fixture_and_announcement(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        thread = AsyncMock()
        announcement.create_thread = AsyncMock(return_value=thread)
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(1, "111111")
        assert fixture is not None
        assert fixture["message_id"] == "999999"
        assert fixture["channel_id"] == str(mock_interaction_admin.channel.id)
        assert "Fixture Created" in mock_interaction_admin.response_sent[-1]["content"]
        mock_interaction_admin.channel.send.assert_awaited_once()
        announcement.create_thread.assert_awaited_once()
        thread.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_thread_creation_fails(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        announcement.create_thread = AsyncMock(side_effect=Exception("thread failed"))
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(1, "111111")
        assert fixture is not None
        assert fixture["message_id"] == "999999"
        assert fixture["channel_id"] == str(mock_interaction_admin.channel.id)
        assert (
            "couldn't create a prediction thread"
            in mock_interaction_admin.followup_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_announcement_fails(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        mock_interaction_admin.channel.send = AsyncMock(side_effect=Exception("send failed"))

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert await admin_cog.db.get_fixture_by_id(1, "111111") is not None
        assert (
            "couldn't announce it in the channel"
            in mock_interaction_admin.followup_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_warns_when_week_changes_between_preview_and_confirm(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        announcement = MagicMock()
        announcement.id = 999999
        thread = AsyncMock()
        announcement.create_thread = AsyncMock(return_value=thread)
        mock_interaction_admin.channel.send = AsyncMock(return_value=announcement)

        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)
        await admin_cog.db.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        fixture = await admin_cog.db.get_fixture_by_id(2, "111111")
        assert fixture is not None
        assert "Week number changed" in mock_interaction_admin.response_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_posts_to_configured_league_channel(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.upsert_guild_config(
            "111111",
            str(
                mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id).roles[0].id
            ),
            "654321",
        )
        announcement = MagicMock()
        announcement.id = 999999
        announcement.create_thread = AsyncMock(return_value=AsyncMock())
        configured_channel = MagicMock()
        configured_channel.id = 654321
        configured_channel.send = AsyncMock(return_value=announcement)
        admin_cog.bot.get_channel.return_value = configured_channel
        mock_interaction_admin.channel.send = AsyncMock()

        modal = CreateFixtureModal(
            admin_cog.db,
            mock_interaction_admin.channel,
            str(mock_interaction_admin.user.id),
            admin_cog.bot,
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)
        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        configured_channel.send.assert_awaited_once()
        mock_interaction_admin.channel.send.assert_not_awaited()
        fixture = await admin_cog.db.get_current_fixture("111111")
        assert fixture["channel_id"] == "654321"

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_rejects_unavailable_configured_channel(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        await admin_cog.db.upsert_guild_config(
            "111111",
            str(
                mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id).roles[0].id
            ),
            "654321",
        )
        admin_cog.bot.get_channel.return_value = None
        admin_cog.bot.fetch_channel.side_effect = discord.NotFound(MagicMock(), "missing")

        modal = CreateFixtureModal(
            admin_cog.db,
            mock_interaction_admin.channel,
            str(mock_interaction_admin.user.id),
            admin_cog.bot,
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)
        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        await confirm_button.callback(mock_interaction_admin)

        assert "league channel" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_current_fixture("111111") is None

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_cancel_leaves_database_unchanged(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        cancel_button = next(
            child for child in confirm_view.children if getattr(child, "label", None) == "Cancel"
        )
        await cancel_button.callback(mock_interaction_admin)

        assert "Fixture creation cancelled" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_current_fixture("111111") is None

    @pytest.mark.asyncio
    async def test_create_fixture_confirm_rechecks_admin_permission(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        modal = CreateFixtureModal(
            admin_cog.db, mock_interaction_admin.channel, str(mock_interaction_admin.user.id)
        )
        modal.games_input._value = "\n".join(sample_games)
        modal.deadline_input._value = "2026-04-20 18:00"

        await modal.on_submit(mock_interaction_admin)

        confirm_view = mock_interaction_admin.response_sent[-1]["view"]
        confirm_button = next(
            child
            for child in confirm_view.children
            if getattr(child, "label", None) == "Create Fixture"
        )
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        member.roles = []

        await confirm_button.callback(mock_interaction_admin)

        assert "no longer have permission" in mock_interaction_admin.response_sent[-1]["content"]
        assert await admin_cog.db.get_current_fixture("111111") is None

    @pytest.mark.asyncio
    async def test_panel_command_returns_view(self, admin_cog, mock_interaction_admin):
        await admin_cog.panel.callback(admin_cog, mock_interaction_admin)

        response = mock_interaction_admin.response_sent[0]
        assert "Admin Panel" in response["content"]
        assert response["ephemeral"] is True
        assert response["view"] is not None
        assert _has_button(response["view"], "Setup TyperBot") is True
