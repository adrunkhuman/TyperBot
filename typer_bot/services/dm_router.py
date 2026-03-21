"""Explicit DM routing coordinator.

Routing precedence (highest → lowest):
1. Admin fixture creation session
2. Admin results entry session
3. User prediction session

All three handlers are checked against the same WorkflowStateStore, so precedence
is determined by the order of checks here — not by listener registration order across cogs.
"""

from __future__ import annotations

import discord

from typer_bot.handlers.dm_prediction_handler import DMPredictionHandler
from typer_bot.handlers.fixture_handler import FixtureCreationHandler
from typer_bot.handlers.results_handler import ResultsEntryHandler
from typer_bot.utils.permissions import is_admin_member


class DMRouter:
    """Routes incoming DMs to the correct workflow handler."""

    def __init__(
        self,
        fixture_handler: FixtureCreationHandler,
        results_handler: ResultsEntryHandler,
        prediction_handler: DMPredictionHandler,
    ) -> None:
        self._fixture_handler = fixture_handler
        self._results_handler = results_handler
        self._prediction_handler = prediction_handler

    async def route(self, message: discord.Message) -> bool:
        """Route a DM to the correct handler. Returns True if consumed."""
        if message.author.bot or message.guild is not None:
            return False

        user_id = str(message.author.id)

        if self._fixture_handler.has_session(user_id):
            await self._fixture_handler.handle_dm(message, user_id, is_admin_member)
            return True

        if self._results_handler.has_session(user_id):
            await self._results_handler.handle_dm(message, user_id, is_admin_member)
            return True

        return await self._prediction_handler.handle_dm(message)
