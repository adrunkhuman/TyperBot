"""Handler for the user DM prediction workflow."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import discord

from typer_bot.database import Database
from typer_bot.handlers.results_handler import has_results_session
from typer_bot.utils import format_for_discord, now, parse_line_predictions
from typer_bot.utils.logger import LogContextManager, log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000
SESSION_TIMEOUT_HOURS = 1
WEEK_SELECTION_PATTERN = re.compile(r"^\s*(?:week\s+)?(\d+)\s*$", re.IGNORECASE)
YES_REPLIES = {"y", "yes"}
NO_REPLIES = {"n", "no"}
PredictionStep = Literal["select", "predict", "continue"]


@dataclass(slots=True)
class PredictionSession:
    """Active state for one user's DM prediction flow."""

    step: PredictionStep
    fixture_ids: list[int]
    fixture_id: int | None
    completed_fixture_ids: list[int]
    created_at: datetime


class DMPredictionHandler:
    """Handles the DM workflow for user predictions."""

    def __init__(self, db: Database):
        self.db = db
        self._prediction_sessions: dict[str, PredictionSession] = {}

    def _cleanup_expired_prediction_sessions(self) -> None:
        """Remove prediction sessions older than SESSION_TIMEOUT_HOURS."""
        current_time = now()
        expiry = timedelta(hours=SESSION_TIMEOUT_HOURS)
        expired_users = [
            user_id
            for user_id, state in self._prediction_sessions.items()
            if current_time - state.created_at > expiry
        ]

        for user_id in expired_users:
            self._prediction_sessions.pop(user_id, None)

    def _get_prediction_session(self, user_id: str) -> PredictionSession | None:
        """Get active prediction session for a user."""
        self._cleanup_expired_prediction_sessions()
        return self._prediction_sessions.get(user_id)

    def _set_prediction_session(
        self,
        user_id: str,
        *,
        step: PredictionStep,
        fixture_ids: list[int] | None = None,
        fixture_id: int | None = None,
        completed_fixture_ids: list[int] | None = None,
    ) -> None:
        """Create or update prediction flow state for a user."""
        self._prediction_sessions[user_id] = PredictionSession(
            step=step,
            fixture_ids=fixture_ids or [],
            fixture_id=fixture_id,
            completed_fixture_ids=completed_fixture_ids or [],
            created_at=now(),
        )

    def _clear_prediction_session(self, user_id: str) -> None:
        """Clear prediction flow state for a user."""
        self._prediction_sessions.pop(user_id, None)

    @staticmethod
    def _parse_week_selection(content: str) -> tuple[int | None, str]:
        """Parse a week selection from DM text."""
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        if not lines:
            return None, ""

        match = WEEK_SELECTION_PATTERN.fullmatch(lines[0])
        if not match:
            return None, ""

        remainder = "\n".join(lines[1:]).strip()
        return int(match.group(1)), remainder

    def _build_fixture_selection_prompt(self, fixtures: list[dict], intro: str) -> str:
        """Build DM prompt asking user which fixture/week to target."""
        lines = [intro, ""]

        for fixture in fixtures:
            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")
            lines.append(
                f"• Week {fixture['week_number']} - Deadline: {deadline_str} ({relative_str})"
            )

        lines.extend(["", "Reply with the week number (for example: `12`)."])
        return "\n".join(lines)

    def _build_prediction_prompt(self, fixture: dict) -> str:
        """Build DM instructions for submitting one fixture's predictions."""
        lines = [
            f"**Week {fixture['week_number']} - Submit Your Predictions**",
            "",
            "Reply with your predictions in this format (one per line OR comma-separated):",
            "```",
        ]
        for game in fixture["games"]:
            lines.append(f"{game} 2:0")

        deadline_str = format_for_discord(fixture["deadline"], "F")
        relative_str = format_for_discord(fixture["deadline"], "R")
        lines.extend(["```", "", "Or comma-separated:", "```"])

        example_games = fixture["games"][:2] if len(fixture["games"]) >= 2 else fixture["games"]
        example_preds = [f"{game} 2:0" for game in example_games]
        if len(fixture["games"]) > 2:
            lines.append(", ".join(example_preds) + ", ...")
        else:
            lines.append(", ".join(example_preds))

        lines.extend(
            [
                "```",
                "",
                "Add your score (e.g., 2:0 or 2-1) at the end of each game.",
                f"\n**Deadline:** {deadline_str} ({relative_str})",
            ]
        )

        return "\n".join(lines)

    async def start_flow(
        self, user: discord.User | discord.Member, open_fixtures: list[dict]
    ) -> None:
        """Start the DM prediction flow for a user."""
        user_id = str(user.id)

        if len(open_fixtures) == 1:
            fixture = open_fixtures[0]
            self._set_prediction_session(
                user_id,
                step="predict",
                fixture_id=fixture["id"],
                completed_fixture_ids=[],
            )
            await user.send(self._build_prediction_prompt(fixture))
            return

        self._set_prediction_session(
            user_id,
            step="select",
            fixture_ids=[fixture["id"] for fixture in open_fixtures],
            completed_fixture_ids=[],
        )
        await user.send(
            self._build_fixture_selection_prompt(
                open_fixtures,
                "Multiple fixtures are open. Which week do you want to predict first?",
            )
        )

    async def handle_dm(self, message: discord.Message) -> bool:
        """Handle a DM message for prediction submission."""
        if message.author.bot or message.guild is not None:
            return False

        user_id = str(message.author.id)

        # Prevent admin's existing predictions being marked late during results entry
        if has_results_session(user_id):
            return False

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return True

        open_fixtures = await self.db.get_open_fixtures()
        if not open_fixtures:
            self._clear_prediction_session(user_id)
            await message.author.send(
                "ℹ️ No active fixture at the moment. "
                "Ask an admin to create one, or check back later!"
            )
            return True

        fixture_by_id = {fixture["id"]: fixture for fixture in open_fixtures}
        session = self._get_prediction_session(user_id)
        message_content = message.content.strip()

        if session and session.step == "continue":
            reply = message_content.lower()
            completed_fixture_ids = session.completed_fixture_ids
            remaining_fixture_ids = session.fixture_ids
            remaining_open_fixtures = [
                fixture for fixture in open_fixtures if fixture["id"] in remaining_fixture_ids
            ]

            if reply in YES_REPLIES:
                if not remaining_open_fixtures:
                    self._clear_prediction_session(user_id)
                    await message.author.send("ℹ️ There are no other open fixtures right now.")
                    return True

                if len(remaining_open_fixtures) == 1:
                    next_fixture = remaining_open_fixtures[0]
                    self._set_prediction_session(
                        user_id,
                        step="predict",
                        fixture_id=next_fixture["id"],
                        completed_fixture_ids=completed_fixture_ids,
                    )
                    await message.author.send(self._build_prediction_prompt(next_fixture))
                    return True

                self._set_prediction_session(
                    user_id,
                    step="select",
                    fixture_ids=[fixture["id"] for fixture in remaining_open_fixtures],
                    completed_fixture_ids=completed_fixture_ids,
                )
                await message.author.send(
                    self._build_fixture_selection_prompt(
                        remaining_open_fixtures,
                        "Multiple fixtures are still open. Which week do you want to predict next?",
                    )
                )
                return True

            if reply in NO_REPLIES:
                self._clear_prediction_session(user_id)
                await message.author.send("👍 Got it. You're done for now.")
                return True

            await message.author.send("Please reply with `yes` or `no`.")
            return True

        target_fixture: dict | None = None
        content_for_parsing = message.content
        completed_fixture_ids: list[int] = []

        if session and session.step == "select":
            allowed_fixture_ids = set(session.fixture_ids)
            completed_fixture_ids = session.completed_fixture_ids
            selected_week, inline_predictions = self._parse_week_selection(message_content)

            selectable_fixtures = [
                fixture
                for fixture in open_fixtures
                if not allowed_fixture_ids or fixture["id"] in allowed_fixture_ids
            ]

            if selected_week is None:
                await message.author.send(
                    self._build_fixture_selection_prompt(
                        selectable_fixtures,
                        "Please choose which week you want to predict.",
                    )
                )
                return True

            target_fixture = next(
                (
                    fixture
                    for fixture in selectable_fixtures
                    if fixture["week_number"] == selected_week
                ),
                None,
            )

            if not target_fixture:
                await message.author.send(
                    self._build_fixture_selection_prompt(
                        selectable_fixtures,
                        f"Week {selected_week} is not currently available. Please choose one of these open weeks:",
                    )
                )
                return True

            self._set_prediction_session(
                user_id,
                step="predict",
                fixture_id=target_fixture["id"],
                completed_fixture_ids=completed_fixture_ids,
            )

            if inline_predictions:
                content_for_parsing = inline_predictions
            else:
                await message.author.send(self._build_prediction_prompt(target_fixture))
                return True

        elif session and session.step == "predict":
            completed_fixture_ids = session.completed_fixture_ids
            target_fixture = fixture_by_id.get(session.fixture_id)
            if not target_fixture:
                self._set_prediction_session(
                    user_id,
                    step="select",
                    fixture_ids=[fixture["id"] for fixture in open_fixtures],
                    completed_fixture_ids=completed_fixture_ids,
                )
                await message.author.send(
                    self._build_fixture_selection_prompt(
                        open_fixtures,
                        "The fixture you selected is no longer open. Please choose another open week.",
                    )
                )
                return True

        if target_fixture is None:
            if len(open_fixtures) == 1:
                target_fixture = open_fixtures[0]
            else:
                self._set_prediction_session(
                    user_id,
                    step="select",
                    fixture_ids=[fixture["id"] for fixture in open_fixtures],
                    completed_fixture_ids=completed_fixture_ids,
                )
                await message.author.send(
                    self._build_fixture_selection_prompt(
                        open_fixtures,
                        "Multiple fixtures are open. Which week do you want to predict first?",
                    )
                )
                return True

        games = target_fixture["games"]
        fixture_id = target_fixture["id"]

        with LogContextManager(user_id=user_id, fixture_id=fixture_id, source="dm"):
            logger.debug(f"Processing DM from user {user_id}")

            processing_msg = await message.author.send("⏳ Processing your predictions...")

            try:
                current_time = now()
                is_late = current_time > target_fixture["deadline"]

                predictions, errors = parse_line_predictions(content_for_parsing, games)

                if errors:
                    error_msg = "\n".join(errors)
                    log_event(
                        logger,
                        event_type="prediction.dm_parse_failed",
                        message="Invalid prediction format in DM",
                        level=logging.WARNING,
                        user_id=user_id,
                        fixture_id=fixture_id,
                        source="dm",
                        errors_count=len(errors),
                    )
                    await processing_msg.edit(
                        content=f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                        f"Please send your predictions again in this format:\n"
                        f"```\n{games[0]} 2:0\n{games[1]} 1:1\n...\n```"
                    )
                    return True

                await self.db.save_prediction(
                    fixture_id,
                    user_id,
                    message.author.display_name,
                    predictions,
                    is_late,
                )

                log_event(
                    logger,
                    event_type="prediction.saved",
                    message="DM prediction saved successfully",
                    user_id=user_id,
                    fixture_id=fixture_id,
                    source="dm",
                    predictions_count=len(predictions),
                    is_late=is_late,
                )

                preview_lines = ["**Predictions saved!**\n"]
                for index, (game, prediction) in enumerate(
                    zip(games, predictions, strict=False),
                    1,
                ):
                    preview_lines.append(f"{index}. {game} **{prediction}**")

                deadline_str = format_for_discord(target_fixture["deadline"], "F")
                relative_str = format_for_discord(target_fixture["deadline"], "R")
                preview_lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")

                late_warning = ""
                if is_late:
                    late_warning = (
                        "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."
                    )

                preview_text = "\n".join(preview_lines)

                completed = set(completed_fixture_ids)
                completed.add(fixture_id)
                remaining_fixture_ids = [
                    fixture["id"] for fixture in open_fixtures if fixture["id"] not in completed
                ]

                if remaining_fixture_ids:
                    self._set_prediction_session(
                        user_id,
                        step="continue",
                        fixture_ids=remaining_fixture_ids,
                        completed_fixture_ids=sorted(completed),
                    )
                    await processing_msg.edit(
                        content=(
                            f"{preview_text}{late_warning}\n\n"
                            "Would you like to predict another open fixture? "
                            "Reply `yes` or `no`."
                        ),
                        view=None,
                    )
                else:
                    self._clear_prediction_session(user_id)
                    await processing_msg.edit(content=f"{preview_text}{late_warning}", view=None)

            except Exception as exc:
                logger.error(
                    f"Error processing predictions: {exc}",
                    exc_info=True,
                    extra={
                        "event_type": "prediction.save_failed",
                        "user_id": user_id,
                        "fixture_id": fixture_id,
                        "source": "dm",
                        "error_type": type(exc).__name__,
                    },
                )
                await processing_msg.edit(
                    content=f"❌ Error processing predictions: {exc}\n\nPlease try again."
                )

        return True
