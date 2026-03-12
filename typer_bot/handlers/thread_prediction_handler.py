"""Handler for thread-based predictions."""

import logging
from contextlib import suppress

import discord

from typer_bot.database import Database
from typer_bot.services.workflow_state import WorkflowStateStore
from typer_bot.utils import now, parse_line_predictions
from typer_bot.utils.logger import LogContextManager, log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000

PREDICTION_RATE_LIMIT_SECONDS = 1


class ThreadPredictionHandler:
    """Handles predictions posted in fixture announcement threads."""

    def __init__(self, bot: discord.Client, db: Database, workflow_state: WorkflowStateStore):
        self.bot = bot
        self.db = db
        self.workflow_state = workflow_state

    async def on_message(self, message: discord.Message):
        """Handle messages in fixture threads.

        Returns True if message was handled, False otherwise.
        """
        if message.author.bot or message.guild is None:
            return False

        if not isinstance(message.channel, discord.Thread):
            return False

        message_id = str(message.channel.id)
        fixture = await self.db.get_fixture_by_message_id(message_id)
        if not fixture:
            return False

        user_id = str(message.author.id)
        with LogContextManager(user_id=user_id, fixture_id=fixture["id"], source="thread"):
            # Update and check the cooldown in one step so rapid reposts cannot race each other.
            current_time = now()
            last_time = self.workflow_state.record_thread_prediction_attempt(user_id, current_time)
            if (
                last_time
                and (current_time - last_time).total_seconds() < PREDICTION_RATE_LIMIT_SECONDS
            ):
                logger.debug(f"Rate limiting prediction from {user_id}")
                return True

            if len(message.content) > MAX_MESSAGE_LENGTH:
                await self._handle_error(
                    message,
                    f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)",
                )
                return True

            predictions, errors = parse_line_predictions(message.content, fixture["games"])

            # Threads also contain chatter; only treat messages with score lines as submissions.
            if len(predictions) == 0:
                logger.debug(f"Ignoring message with no valid scores from {message.author.id}")
                return False

            if errors:
                error_msg = "\n".join(errors)
                log_event(
                    logger,
                    event_type="prediction.parse_failed",
                    message="Invalid prediction format",
                    user_id=user_id,
                    fixture_id=fixture["id"],
                    source="thread",
                    errors_count=len(errors),
                    level=logging.WARNING,
                )
                await self._handle_error(
                    message,
                    f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                    f"Please post your predictions again in this format:\n"
                    f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```",
                )
                return True

            current_time = now()
            is_late = current_time > fixture["deadline"]

            # DM and thread submissions can land at the same time; keep the first stored pick.
            existing = await self.db.get_prediction(fixture["id"], user_id)
            if existing:
                log_event(
                    logger,
                    event_type="prediction.duplicate_blocked",
                    message="Duplicate prediction blocked (race condition prevention)",
                    user_id=user_id,
                    fixture_id=fixture["id"],
                    source="thread",
                )
                await message.author.send(
                    "ℹ️ You already submitted predictions for this fixture. "
                    "Use `/predict` if you want to update them."
                )
                return True

        try:
            await self.db.save_prediction(
                fixture["id"],
                user_id,
                message.author.display_name,
                predictions,
                is_late,
            )

            try:
                await message.add_reaction("✅")
            except discord.Forbidden:
                logger.warning(
                    f"Could not add reaction to thread prediction from {message.author.id}. "
                    "Missing 'Add Reactions' permission."
                )
                # Fall back to DM so the user still gets confirmation.
                with suppress(discord.Forbidden):
                    await message.author.send(
                        "✅ **Prediction saved!**\n"
                        "(I couldn't react to your message in the thread due to missing permissions, "
                        "but your prediction has been recorded.)"
                    )

            log_event(
                logger,
                event_type="prediction.saved",
                message="Thread prediction saved successfully",
                user_id=user_id,
                fixture_id=fixture["id"],
                source="thread",
                predictions_count=len(predictions),
                is_late=is_late,
            )

            if is_late:
                with suppress(discord.Forbidden):
                    await message.author.send(
                        "⚠️ **Late prediction!** Your prediction was saved but you will receive "
                        "0 points for this round since the deadline has passed."
                    )

            return True

        except Exception as e:
            logger.error(
                f"Error saving thread prediction: {e}",
                exc_info=True,
                extra={
                    "event_type": "prediction.save_failed",
                    "user_id": user_id,
                    "fixture_id": fixture["id"],
                    "source": "thread",
                    "error_type": type(e).__name__,
                },
            )
            await self._handle_error(
                message,
                "❌ Error saving predictions. Please try again or use `/predict` instead.",
            )
            return True

    async def _handle_error(self, message: discord.Message, error_text: str):
        """Handle errors by DMing the user and optionally reacting to the message."""
        with suppress(discord.Forbidden):
            await message.author.send(error_text)
        # Fall back to a reaction when DMs are closed.
        with suppress(discord.Forbidden):
            await message.add_reaction("❌")

        logger.warning(f"Sent error DM to {message.author.id}: {error_text[:100]}...")
