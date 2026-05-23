"""Handler for thread-based predictions."""

import logging
import re
from contextlib import suppress
from datetime import datetime, timedelta

import discord

from typer_bot.database import Database, SaveResult
from typer_bot.utils import (
    build_prediction_submission,
    get_configured_admin_role_mention,
    now,
    parse_prediction_lines,
)
from typer_bot.utils.logger import LogContextManager, log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000

PREDICTION_RATE_LIMIT_SECONDS = 1
COOLDOWN_ENTRY_EXPIRY = timedelta(hours=1)


class ThreadPredictionHandler:
    """Accept one-shot predictions posted in fixture announcement threads.

    Only threads tied to an open fixture announcement are treated as prediction
    surfaces. Valid submissions are first-write-wins, rapid reposts are rate
    limited, and duplicate or permission-edge-case feedback falls back to DMs.
    """

    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db
        self._thread_prediction_cooldowns: dict[tuple[str, str], datetime] = {}

    def record_thread_prediction_attempt(
        self, guild_id: str, user_id: str, current_time: datetime
    ) -> datetime | None:
        """Record a thread prediction attempt and return the previous timestamp.

        Also prunes cooldown entries older than `COOLDOWN_ENTRY_EXPIRY` so the
        rate-limit check does not require a separate cleanup pass.
        """
        cooldown_key = (guild_id, user_id)
        previous_attempt = self._thread_prediction_cooldowns.get(cooldown_key)
        self._thread_prediction_cooldowns[cooldown_key] = current_time

        cutoff = current_time - COOLDOWN_ENTRY_EXPIRY
        expired_users = [
            stored_key
            for stored_key, timestamp in self._thread_prediction_cooldowns.items()
            if timestamp < cutoff
        ]
        for stored_key in expired_users:
            self._thread_prediction_cooldowns.pop(stored_key, None)

        return previous_attempt

    def is_rate_limited(self, guild_id: str, user_id: str, current_time: datetime) -> bool:
        """Return whether a recognized prediction attempt should be rate limited."""
        last_time = self._thread_prediction_cooldowns.get((guild_id, user_id))
        if last_time is None:
            return False
        return (current_time - last_time).total_seconds() < PREDICTION_RATE_LIMIT_SECONDS

    def get_thread_prediction_cooldown(self, guild_id: str, user_id: str) -> datetime | None:
        return self._thread_prediction_cooldowns.get((guild_id, user_id))

    def clear_thread_prediction_cooldowns(self) -> None:
        self._thread_prediction_cooldowns.clear()

    async def on_message(self, message: discord.Message):
        """Handle a possible prediction posted inside a fixture thread.

        Chatter that contains no score lines is ignored so prediction threads can
        still be used conversationally. Returns ``True`` when the message hit a
        known fixture thread and was processed, rejected, or rate limited;
        returns ``False`` when the message is outside this workflow.
        """
        if message.author.bot or message.guild is None:
            return False

        if not isinstance(message.channel, discord.Thread):
            return False

        message_id = str(message.channel.id)
        fixture = await self.db.fixtures.get_fixture_by_message_id(
            message_id, str(message.guild.id)
        )
        if not fixture:
            return False

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        with LogContextManager(
            user_id=user_id,
            fixture_id=fixture["id"],
            week_number=fixture["week_number"],
            source="thread",
        ):
            current_time = now()
            has_score_like_content = any(
                re.search(r"(\d+\s*[-:]\s*\d+|[xX])\s*$", line.strip())
                for line in message.content.splitlines()
            )

            if len(message.content) > MAX_MESSAGE_LENGTH or has_score_like_content:
                if self.is_rate_limited(guild_id, user_id, current_time):
                    logger.debug(f"Rate limiting prediction from {user_id}")
                    return True

                self.record_thread_prediction_attempt(guild_id, user_id, current_time)

            if len(message.content) > MAX_MESSAGE_LENGTH:
                await self._handle_error(
                    message,
                    f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)",
                )
                return True

            if not has_score_like_content:
                logger.debug(
                    f"Ignoring message with no score-like content from {message.author.id}"
                )
                return False

            predictions, predicted_game_indexes, errors = parse_prediction_lines(
                message.content,
                fixture["games"],
                allow_partial=True,
            )

            if errors:
                error_msg = "\n".join(errors)
                log_event(
                    logger,
                    event_type="prediction.parse_failed",
                    message="Invalid prediction format",
                    user_id=user_id,
                    fixture_id=fixture["id"],
                    week_number=fixture["week_number"],
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

            # Threads also contain chatter; only treat messages with score lines as submissions.
            if len(predictions) == 0:
                logger.debug(f"Ignoring message with no valid scores from {message.author.id}")
                return False

            submission = build_prediction_submission(
                fixture=fixture,
                predicted_game_indexes=predicted_game_indexes,
                submitted_at=current_time,
                public_message_id=str(message.id),
                public_message_kind="thread_message",
            )

        try:
            result = await self.db.predictions.try_save_prediction(
                fixture["id"],
                user_id,
                message.author.display_name,
                predictions,
                submission.is_late,
                predicted_game_indexes=predicted_game_indexes,
                pending_partial_approval=submission.pending_partial_approval,
                public_message_id=submission.public_message_id,
                public_message_kind=submission.public_message_kind,
            )

            if result == SaveResult.FIXTURE_CLOSED:
                log_event(
                    logger,
                    event_type="prediction.fixture_closed",
                    message="Prediction rejected: fixture closed before atomic write",
                    user_id=user_id,
                    fixture_id=fixture["id"],
                    week_number=fixture["week_number"],
                    source="thread",
                )
                with suppress(discord.Forbidden):
                    await message.author.send(
                        "ℹ️ This fixture was closed before your prediction could be saved. "
                        "Use `/predict` to check if another fixture is still open."
                    )
                return True

            if result == SaveResult.DUPLICATE:
                log_event(
                    logger,
                    event_type="prediction.duplicate_blocked",
                    message="Duplicate prediction blocked (race condition prevention)",
                    user_id=user_id,
                    fixture_id=fixture["id"],
                    week_number=fixture["week_number"],
                    source="thread",
                )
                with suppress(discord.Forbidden):
                    await message.author.send(
                        "ℹ️ You already submitted predictions for this fixture. "
                        "Use `/predict` if you want to update them."
                    )
                return True

            try:
                await message.add_reaction("⏳" if submission.pending_partial_approval else "✅")
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
                week_number=fixture["week_number"],
                source="thread",
                predictions_count=len(predictions),
                is_late=submission.is_late,
            )

            if submission.is_late:
                with suppress(discord.Forbidden):
                    if submission.pending_partial_approval:
                        admin_role_mention = (
                            await get_configured_admin_role_mention(str(message.guild.id), self.db)
                            if message.guild
                            else None
                        )
                        await message.author.send(
                            "⏳ **Late prediction received.** It was saved and is now awaiting admin review because it arrived late with missing games."
                        )
                        await message.channel.send(
                            f"⏳ Late prediction from <@{message.author.id}> with missing games is awaiting admin review. {admin_role_mention}"
                            if admin_role_mention
                            else f"⏳ Late prediction from <@{message.author.id}> with missing games is awaiting admin review."
                        )
                    else:
                        await message.author.send(
                            "⚠️ **Late prediction!** Your prediction was saved but you will receive "
                            "the active season's late penalty unless an admin waives it."
                        )
            elif submission.is_partial:
                with suppress(discord.Forbidden):
                    await message.author.send(
                        "ℹ️ **Partial prediction saved.** Any missing games will count as no prediction. If the deadline has not passed yet, use `/predict` again to fill the rest."
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
