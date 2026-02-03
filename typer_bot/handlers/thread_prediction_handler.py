"""Handler for thread-based predictions."""

import logging
import re

import discord

from typer_bot.database import Database
from typer_bot.utils import now, parse_line_predictions

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000


class ThreadPredictionHandler:
    """Handles predictions posted in fixture announcement threads."""

    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db

    async def on_message(self, message: discord.Message):
        """Handle messages in fixture threads.

        Returns True if message was handled, False otherwise.
        """
        # Ignore bot messages and DMs
        if message.author.bot or message.guild is None:
            return False

        # Check if this is a thread
        if not isinstance(message.channel, discord.Thread):
            return False

        # Check if this thread belongs to a fixture
        thread_id = str(message.channel.id)
        fixture = await self.db.get_fixture_by_thread_id(thread_id)
        if not fixture:
            return False

        # Check message length
        if len(message.content) > MAX_MESSAGE_LENGTH:
            await self._handle_error(
                message,
                f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)",
            )
            return True

        # Parse predictions
        predictions, errors = parse_line_predictions(message.content, fixture["games"])

        # Chatty Thread fix: If no valid scores found, ignore silently
        if len(predictions) == 0:
            logger.debug(f"Ignoring message with no valid scores from {message.author.id}")
            return False

        # If there are parsing errors, DM the user
        if errors:
            error_msg = "\n".join(errors)
            await self._handle_error(
                message,
                f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                f"Please post your predictions again in this format:\n"
                f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```",
            )
            return True

        # Check deadline
        current_time = now()
        is_late = current_time > fixture["deadline"]

        try:
            # Save prediction
            await self.db.save_prediction(
                fixture["id"],
                str(message.author.id),
                message.author.display_name,
                predictions,
                is_late,
            )

            # Add success reaction
            await message.add_reaction("✅")

            logger.info(
                f"Saved thread prediction from {message.author.id} for fixture {fixture['id']}"
            )

            if is_late:
                try:
                    await message.author.send(
                        f"⚠️ **Late prediction!** Your prediction was saved but you will receive "
                        f"0 points for this round since the deadline has passed."
                    )
                except discord.Forbidden:
                    pass  # User has DMs disabled

            return True

        except Exception as e:
            logger.error(f"Error saving thread prediction: {e}", exc_info=True)
            await self._handle_error(
                message,
                "❌ Error saving predictions. Please try again or use `/predict` instead.",
            )
            return True

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Handle message edits in fixture threads.

        Returns True if message was handled, False otherwise.
        """
        # Ignore bot messages and DMs
        if after.author.bot or after.guild is None:
            return False

        # Check if this is a thread
        if not isinstance(after.channel, discord.Thread):
            return False

        # Check if this thread belongs to a fixture
        thread_id = str(after.channel.id)
        fixture = await self.db.get_fixture_by_thread_id(thread_id)
        if not fixture:
            return False

        # Check message length
        if len(after.content) > MAX_MESSAGE_LENGTH:
            await self._handle_error(
                after,
                f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)",
            )
            return True

        # Parse predictions
        predictions, errors = parse_line_predictions(after.content, fixture["games"])

        # Chatty Thread fix: If no valid scores found, ignore silently
        if len(predictions) == 0:
            logger.debug(f"Ignoring edited message with no valid scores from {after.author.id}")
            return False

        # If there are parsing errors, DM the user
        if errors:
            error_msg = "\n".join(errors)
            await self._handle_error(
                after,
                f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                f"Please edit your message to use the correct format:\n"
                f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```",
            )
            return True

        # Check deadline (Sneaky Edit fix: always re-check deadline on edits)
        current_time = now()
        is_late = current_time > fixture["deadline"]

        try:
            # Save updated prediction
            await self.db.save_prediction(
                fixture["id"],
                str(after.author.id),
                after.author.display_name,
                predictions,
                is_late,
            )

            # Remove any existing reactions and add success reaction
            try:
                await after.clear_reactions()
            except discord.Forbidden:
                pass  # Bot might not have permission to clear reactions

            await after.add_reaction("✅")

            logger.info(
                f"Updated thread prediction from {after.author.id} for fixture {fixture['id']}"
            )

            if is_late:
                try:
                    await after.author.send(
                        f"⚠️ **Late prediction!** Your edited prediction was saved but you will "
                        f"receive 0 points for this round since the deadline has passed."
                    )
                except discord.Forbidden:
                    pass  # User has DMs disabled

            return True

        except Exception as e:
            logger.error(f"Error updating thread prediction: {e}", exc_info=True)
            await self._handle_error(
                after,
                "❌ Error updating predictions. Please try again or use `/predict` instead.",
            )
            return True

    async def on_message_delete(self, message: discord.Message):
        """Handle message deletions in fixture threads.

        Returns True if message was handled, False otherwise.
        """
        # Ignore bot messages and DMs
        if message.author.bot or message.guild is None:
            return False

        # Check if this is a thread
        if not isinstance(message.channel, discord.Thread):
            return False

        # Check if this thread belongs to a fixture
        thread_id = str(message.channel.id)
        fixture = await self.db.get_fixture_by_thread_id(thread_id)
        if not fixture:
            return False

        try:
            # Delete the prediction
            deleted = await self.db.delete_prediction(fixture["id"], str(message.author.id))

            if deleted:
                logger.info(
                    f"Deleted prediction from {message.author.id} for fixture {fixture['id']}"
                )

                try:
                    await message.author.send(
                        f"🗑️ **Your prediction has been deleted.**\n\n"
                        f"Week {fixture['week_number']} prediction removed. "
                        f"Submit a new prediction before the deadline if you want to participate."
                    )
                except discord.Forbidden:
                    pass  # User has DMs disabled

            return True

        except Exception as e:
            logger.error(f"Error deleting thread prediction: {e}", exc_info=True)
            return True

    async def _handle_error(self, message: discord.Message, error_text: str):
        """Handle errors by DMing the user and optionally reacting to the message."""
        try:
            await message.author.send(error_text)
        except discord.Forbidden:
            # User has DMs disabled, add reaction to indicate error
            try:
                await message.add_reaction("❌")
            except discord.Forbidden:
                pass  # Can't react either

        logger.warning(f"Sent error DM to {message.author.id}: {error_text[:100]}...")
