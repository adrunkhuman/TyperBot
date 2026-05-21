from unittest.mock import MagicMock

import discord
import pytest

from tests.admin_panel_helpers import get_button as _get_button
from typer_bot.commands.admin_panel import (
    CreateFixtureModal,
    UnifiedAdminPanelView,
)


class TestFixturePanelCreation:
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
