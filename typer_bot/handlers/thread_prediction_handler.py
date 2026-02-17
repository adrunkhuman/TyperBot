"""Handler for thread-based predictions."""

import logging
from contextlib import suppress
from datetime import datetime, timedelta

import discord

from typer_bot.database import Database
from typer_bot.utils import now, parse_line_predictions
from typer_bot.utils.logger import LogContextManager, log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000

# Rate limiting: user_id -> last prediction timestamp
PREDICTION_RATE_LIMIT_SECONDS = 1
_prediction_cooldowns: dict[str, datetime] = {}


class ThreadPredictionHandler:
    """Handles predictions posted in fixture announcement threads."""

    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db

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
            # Rate limiting check - atomic update to prevent race conditions
            current_time = now()
            last_time = _prediction_cooldowns.get(user_id)
            _prediction_cooldowns[user_id] = current_time  # Always update atomically
            if (
                last_time
                and (current_time - last_time).total_seconds() < PREDICTION_RATE_LIMIT_SECONDS
            ):
                logger.debug(f"Rate limiting prediction from {user_id}")
                return True  # Silently ignore rate-limited messages

            # Cleanup old rate limit entries (prevent unbounded memory growth)
            cutoff = current_time - timedelta(hours=1)
            expired = [uid for uid, ts in list(_prediction_cooldowns.items()) if ts < cutoff]
            for uid in expired:
                _prediction_cooldowns.pop(uid, None)

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

            # Check deadline
            current_time = now()
            is_late = current_time > fixture["deadline"]

            # Check if already submitted via DM (race condition prevention)
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
            # Save prediction
            await self.db.save_prediction(
                fixture["id"],
                user_id,
                message.author.display_name,
                predictions,
                is_late,
            )

            # Add success reaction
            try:
                await message.add_reaction("✅")
            except discord.Forbidden:
                logger.warning(
                    f"Could not add reaction to thread prediction from {message.author.id}. "
                    "Missing 'Add Reactions' permission."
                )
                # Fallback: DM the user so they know it worked
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
        # If DM failed (DMs disabled), add reaction to indicate error
        with suppress(discord.Forbidden):
            await message.add_reaction("❌")

        logger.warning(f"Sent error DM to {message.author.id}: {error_text[:100]}...")
