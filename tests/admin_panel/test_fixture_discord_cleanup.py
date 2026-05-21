from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from typer_bot.commands.admin_panel.fixtures import _cleanup_discord_announcement


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
