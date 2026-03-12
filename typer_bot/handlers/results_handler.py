"""Handler for results entry DM workflow."""

import logging
from collections.abc import Callable

import discord
from discord import ui

from typer_bot.database import Database
from typer_bot.services.workflow_state import ResultsSession, WorkflowStateStore
from typer_bot.utils import parse_line_predictions
from typer_bot.utils.logger import log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000


class ResultsEntryHandler:
    """Handles the DM workflow for entering results."""

    def __init__(self, bot: discord.Client, db: Database, workflow_state: WorkflowStateStore):
        self.bot = bot
        self.db = db
        self.workflow_state = workflow_state

    def get_session(self, user_id: str) -> ResultsSession | None:
        """Return the active results session for a user."""
        return self.workflow_state.get_results_session(user_id)

    def start_session(self, user_id: str, fixture_id: int, guild_id: int) -> None:
        """Initialize a new results entry session."""
        self.workflow_state.start_results_session(user_id, fixture_id, guild_id)
        log_event(
            logger,
            event_type="session.results.started",
            message="Results entry session started",
            level=logging.DEBUG,
            user_id=user_id,
            fixture_id=fixture_id,
            guild_id=guild_id,
        )

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active results entry session."""
        return self.workflow_state.has_results_session(user_id)

    async def handle_dm(
        self,
        message: discord.Message,
        user_id: str,
        is_admin_fn: Callable[[discord.Member | None], bool],
    ) -> bool:
        """Handle a DM message for results entry.

        Args:
            message: The DM message
            user_id: User ID as string
            is_admin_fn: Function to check if member is admin

        Returns:
            True if message was handled, False otherwise
        """
        session = self.get_session(user_id)
        if session is None:
            return False

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return True

        logger.info(f"Processing results DM from user {user_id}")

        # Verify admin status
        guild_id = session.guild_id

        if not await self._verify_admin(message, user_id, guild_id, is_admin_fn):
            return True

        fixture_id = session.fixture_id
        fixture = await self.db.get_fixture_by_id(fixture_id)

        if not fixture:
            await message.author.send("Error: Fixture no longer exists.")
            self.workflow_state.clear_results_session(user_id)
            return True

        processing_msg = await message.author.send("Processing your results...")

        try:
            results, errors = parse_line_predictions(message.content, fixture["games"])

            if results:
                logger.info(f"Successfully parsed {len(results)} scores")

            if errors:
                error_msg = "\n".join(errors)
                logger.warning(f"Validation errors: {error_msg}")
                await processing_msg.edit(
                    content=f"**Invalid results:**\n```{error_msg}```\n\n"
                    f"Please send the results again in this format:\n"
                    f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```"
                )
                return True

            preview_lines = [f"**Week {fixture['week_number']} Results Preview**\n"]
            for i, (game, result) in enumerate(zip(fixture["games"], results, strict=False), 1):
                preview_lines.append(f"{i}. {game} **{result}**")

            preview_text = "\n".join(preview_lines)

            logger.info("Results parsed successfully, showing preview")
            view = ResultsConfirmView(self, user_id, fixture_id, results, preview_text)
            await processing_msg.edit(content=f"{preview_text}\n\nSave these results?", view=view)

        except Exception as e:
            logger.error(f"Error processing results: {e}", exc_info=True)
            await processing_msg.edit(content="Error processing results. Please try again.")

        return True

    async def _verify_admin(
        self,
        message: discord.Message,
        user_id: str,
        guild_id: int | None,
        is_admin_fn: Callable[[discord.Member | None], bool],
    ) -> bool:
        """Verify user is still an admin."""
        if not guild_id:
            logger.warning(f"No guild_id in result data for user {user_id}")
            self.workflow_state.clear_results_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Guild not found for ID: {guild_id}")
            self.workflow_state.clear_results_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        member = guild.get_member(int(user_id))
        if not member:
            logger.warning(
                f"Member not found in guild cache for user {user_id}. "
                "Members intent may not be enabled."
            )
            self.workflow_state.clear_results_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        if not is_admin_fn(member):
            logger.warning(f"Permission denied for user {user_id}")
            self.workflow_state.clear_results_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        return True

    async def save_results(self, user_id: str, fixture_id: int, results: list[str]) -> None:
        """Save results to the database."""
        await self.db.save_results(fixture_id, results)
        self.workflow_state.clear_results_session(user_id)
        log_event(
            logger,
            event_type="results.entered",
            message=f"Results entered for fixture {fixture_id}",
            user_id=user_id,
            fixture_id=fixture_id,
            results_count=len(results),
        )

    def cancel_session(self, user_id: str, reason: str = "cancelled") -> None:
        """Cancel the results entry session."""
        self.workflow_state.clear_results_session(user_id)
        logger.debug(
            f"Results session {reason}",
            extra={
                "event_type": "session.results.completed",
                "user_id": user_id,
                "reason": reason,
            },
        )


class ResultsConfirmView(ui.View):
    """View for confirming results entry."""

    def __init__(
        self,
        handler: ResultsEntryHandler,
        user_id: str,
        fixture_id: int,
        results: list[str],
        preview: str,
    ):
        super().__init__(timeout=120)
        self.handler = handler
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.results = results
        self.preview = preview

    async def on_timeout(self):
        self.handler.cancel_session(self.user_id, reason="timeout")

    @ui.button(label="Save Results", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, _button: ui.Button):
        """Save results to database."""
        await self.handler.save_results(self.user_id, self.fixture_id, self.results)
        await interaction.response.edit_message(
            content=f"**Results Saved!**\n\n{self.preview}\n\nUse `/admin results calculate` to calculate scores.",
            view=None,
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: ui.Button):
        """Cancel results entry."""
        self.handler.cancel_session(self.user_id, reason="user_cancelled")
        await interaction.response.edit_message(
            content="Results entry cancelled. Use `/admin results enter` to try again.", view=None
        )
