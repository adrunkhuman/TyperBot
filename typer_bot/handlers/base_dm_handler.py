"""Base class for admin DM workflow handlers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

import discord

from typer_bot.database import Database
from typer_bot.services.workflow_state import WorkflowStateStore

logger = logging.getLogger(__name__)


class AdminDMHandler[S](ABC):
    """Base for DM handlers that require admin verification before processing."""

    def __init__(
        self, bot: discord.Client, db: Database, workflow_state: WorkflowStateStore
    ) -> None:
        self.bot = bot
        self.db = db
        self.workflow_state = workflow_state

    @abstractmethod
    def get_session(self, user_id: str) -> S | None:
        """Return the active session for a user, or None.

        Implementations must evict expired sessions before returning so that
        `has_session` (which delegates here) never returns True for stale entries.
        """

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active session."""
        return self.get_session(user_id) is not None

    @abstractmethod
    def clear_session(self, user_id: str) -> None:
        """Remove the active session for a user."""

    async def _verify_admin(
        self,
        message: discord.Message,
        user_id: str,
        guild_id: int | None,
        is_admin_fn: Callable[[discord.Member | None], bool],
    ) -> bool:
        """Verify the user still holds admin permissions.

        Clears the session and sends a denial message on any failure path.
        """
        if not guild_id:
            logger.warning(f"No guild_id in session for user {user_id}")
            self.clear_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Guild not found for ID: {guild_id}")
            self.clear_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        member = guild.get_member(int(user_id))
        if not member:
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                logger.warning(
                    f"Member {user_id} not found in guild {guild_id} (cache miss + fetch miss)"
                )
                self.clear_session(user_id)
                await message.author.send("Permission denied or session expired.")
                return False
            except discord.Forbidden as e:
                # Missing intent or inaccessible member looks the same here.
                logger.error(
                    f"fetch_member forbidden for user {user_id} — check GUILD_MEMBERS intent: {e}"
                )
                self.clear_session(user_id)
                await message.author.send("Permission denied or session expired.")
                return False
            except discord.HTTPException as e:
                logger.warning(f"fetch_member transient failure for user {user_id}: {e}")
                await message.author.send("Could not verify permissions, please try again.")
                return False

        if not is_admin_fn(member):
            logger.warning(f"Permission denied for user {user_id}")
            self.clear_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        return True
