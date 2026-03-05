"""User-facing Discord commands."""

import logging
import re
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.handlers.results_handler import has_results_session
from typer_bot.utils import (
    format_for_discord,
    format_standings,
    is_admin,
    now,
    parse_line_predictions,
)
from typer_bot.utils.logger import LogContextManager, log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000
SESSION_TIMEOUT_HOURS = 1
WEEK_SELECTION_PATTERN = re.compile(r"^\s*(?:week\s+)?(\d+)\s*$", re.IGNORECASE)
YES_REPLIES = {"y", "yes"}
NO_REPLIES = {"n", "no"}


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # TyperBot sets db attr dynamically; discord.py typing doesn't track custom attrs
        self.db: Database = bot.db  # type: ignore
        self._prediction_sessions: dict[str, dict] = {}

    def _cleanup_expired_prediction_sessions(self):
        """Remove prediction sessions older than SESSION_TIMEOUT_HOURS."""
        current_time = now()
        expiry = timedelta(hours=SESSION_TIMEOUT_HOURS)
        expired_users = [
            user_id
            for user_id, state in self._prediction_sessions.items()
            if current_time - state.get("created_at", current_time) > expiry
        ]

        for user_id in expired_users:
            self._prediction_sessions.pop(user_id, None)

    def _get_prediction_session(self, user_id: str) -> dict | None:
        """Get active prediction session for a user."""
        self._cleanup_expired_prediction_sessions()
        return self._prediction_sessions.get(user_id)

    def _set_prediction_session(
        self,
        user_id: str,
        *,
        step: str,
        fixture_ids: list[int] | None = None,
        fixture_id: int | None = None,
        completed_fixture_ids: list[int] | None = None,
    ):
        """Create or update prediction flow state for a user."""
        self._prediction_sessions[user_id] = {
            "step": step,
            "fixture_ids": fixture_ids or [],
            "fixture_id": fixture_id,
            "completed_fixture_ids": completed_fixture_ids or [],
            "created_at": now(),
        }

    def _clear_prediction_session(self, user_id: str):
        """Clear prediction flow state for a user."""
        self._prediction_sessions.pop(user_id, None)

    @staticmethod
    def _parse_week_selection(content: str) -> tuple[int | None, str]:
        """Parse a week selection from DM text.

        Accepts first-line values like "12" or "week 12".
        Returns the selected week and any remaining text after the first line.
        """
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

        lines.extend(
            [
                "",
                "Reply with the week number (for example: `12`).",
            ]
        )
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
        lines.extend(
            [
                "```",
                "",
                "Or comma-separated:",
                "```",
            ]
        )

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

    @staticmethod
    def _chunk_message(content: str, limit: int = 2000) -> list[str]:
        """Split long responses into Discord-safe chunks."""
        if len(content) <= limit:
            return [content]

        chunks: list[str] = []
        current = ""
        for line in content.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue

            if current:
                chunks.append(current)

            if len(line) <= limit:
                current = line
                continue

            start = 0
            while start < len(line):
                end = min(start + limit, len(line))
                chunks.append(line[start:end])
                start = end
            current = ""

        if current:
            chunks.append(current)
        return chunks

    async def _send_chunked_ephemeral(self, interaction: discord.Interaction, content: str):
        """Send an ephemeral response split across followups if needed."""
        chunks = self._chunk_message(content)
        if not chunks:
            return

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    async def _process_prediction_message(self, message: discord.Message):
        """Process predictions from DMs."""
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)

        # Prevent admin's existing predictions being marked late during results entry
        if has_results_session(user_id):
            return

        # Check message length first (before fetching fixture)
        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return

        open_fixtures = await self.db.get_open_fixtures()
        if not open_fixtures:
            self._clear_prediction_session(user_id)
            await message.author.send(
                "ℹ️ No active fixture at the moment. "
                "Ask an admin to create one, or check back later!"
            )
            return

        fixture_by_id = {fixture["id"]: fixture for fixture in open_fixtures}
        session = self._get_prediction_session(user_id)
        message_content = message.content.strip()

        # Continue prompt: user chooses whether to predict additional open fixtures.
        if session and session.get("step") == "continue":
            reply = message_content.lower()
            completed_fixture_ids = session.get("completed_fixture_ids", [])
            remaining_fixture_ids = session.get("fixture_ids", [])
            remaining_open_fixtures = [
                fixture for fixture in open_fixtures if fixture["id"] in remaining_fixture_ids
            ]

            if reply in YES_REPLIES:
                if not remaining_open_fixtures:
                    self._clear_prediction_session(user_id)
                    await message.author.send("ℹ️ There are no other open fixtures right now.")
                    return

                if len(remaining_open_fixtures) == 1:
                    next_fixture = remaining_open_fixtures[0]
                    self._set_prediction_session(
                        user_id,
                        step="predict",
                        fixture_id=next_fixture["id"],
                        completed_fixture_ids=completed_fixture_ids,
                    )
                    await message.author.send(self._build_prediction_prompt(next_fixture))
                    return

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
                return

            if reply in NO_REPLIES:
                self._clear_prediction_session(user_id)
                await message.author.send("👍 Got it. You're done for now.")
                return

            await message.author.send("Please reply with `yes` or `no`.")
            return

        target_fixture: dict | None = None
        content_for_parsing = message.content
        completed_fixture_ids: list[int] = []

        if session and session.get("step") == "select":
            allowed_fixture_ids = set(session.get("fixture_ids", []))
            completed_fixture_ids = session.get("completed_fixture_ids", [])
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
                return

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
                return

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
                return

        elif session and session.get("step") == "predict":
            completed_fixture_ids = session.get("completed_fixture_ids", [])
            target_fixture = fixture_by_id.get(session.get("fixture_id"))
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
                return

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
                return

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
                    return

                await self.db.save_prediction(
                    fixture_id,
                    user_id,
                    message.author.display_name,
                    predictions,
                    is_late,
                )

                event_type = "prediction.saved"
                log_event(
                    logger,
                    event_type=event_type,
                    message="DM prediction saved successfully",
                    user_id=user_id,
                    fixture_id=fixture_id,
                    source="dm",
                    predictions_count=len(predictions),
                    is_late=is_late,
                )

                preview_lines = ["**Predictions saved!**\n"]
                for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
                    preview_lines.append(f"{i}. {game} **{pred}**")

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

            except Exception as e:
                logger.error(
                    f"Error processing predictions: {e}",
                    exc_info=True,
                    extra={
                        "event_type": "prediction.save_failed",
                        "user_id": user_id,
                        "fixture_id": fixture_id,
                        "source": "dm",
                        "error_type": type(e).__name__,
                    },
                )
                await processing_msg.edit(
                    content=f"❌ Error processing predictions: {e}\n\nPlease try again."
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs with predictions."""
        await self._process_prediction_message(message)

    @app_commands.command(name="predict", description="Submit your predictions for open fixtures")
    @app_commands.checks.cooldown(1, 1.0)
    async def predict(self, interaction: discord.Interaction):
        """Send fixture list to user via DM."""
        open_fixtures = await self.db.get_open_fixtures()
        if not open_fixtures:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you the prediction flow.", ephemeral=True
        )

        try:
            user_id = str(interaction.user.id)

            if len(open_fixtures) == 1:
                fixture = open_fixtures[0]
                self._set_prediction_session(
                    user_id,
                    step="predict",
                    fixture_id=fixture["id"],
                    completed_fixture_ids=[],
                )
                await interaction.user.send(self._build_prediction_prompt(fixture))
            else:
                self._set_prediction_session(
                    user_id,
                    step="select",
                    fixture_ids=[fixture["id"] for fixture in open_fixtures],
                    completed_fixture_ids=[],
                )
                await interaction.user.send(
                    self._build_fixture_selection_prompt(
                        open_fixtures,
                        "Multiple fixtures are open. Which week do you want to predict first?",
                    )
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(name="help", description="Show help information")
    async def help(self, interaction: discord.Interaction):
        """Display help for users and admins."""
        is_admin_user = is_admin(interaction)

        user_help = """## 📖 User Commands

**For Players:**
• `/predict` - Submit predictions via DM
• `/fixtures` - View all open fixtures
• `/standings` - See overall leaderboard
• `/mypredictions` - Check your submitted predictions for open fixtures

**How to Predict (Two Methods):**

**Method 1: Thread Predictions (NEW)**
1. Look for the fixture announcement thread (created when admin posts fixtures)
2. Reply in the thread with your predictions:
   ```
   Team A - Team B 2:0
   Team C - Team D 1:1
   ...
   ```
3. Bot will react ✅ when saved

**Method 2: DM Predictions**
1. Type `/predict` in the channel (or DM the bot directly)
2. If multiple fixtures are open, bot asks which week you want first
3. Reply with your predictions - they are saved immediately
4. Bot can guide you through other open fixtures in the same DM flow

**Scoring:**
• Exact score: 3 points
• Correct result (win/loss/draw): 1 point
• Wrong: 0 points
• Late predictions: 0 points (submit before deadline!)

**Input formats:** Use `2:0`, `2-0`, or `2 : 0`

**To change a prediction:** Just post again (it replaces your old one)."""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
**Fixture Management:**
• `/admin fixture create` - Create new fixture (DM workflow, auto-creates thread)
• `/admin fixture delete [week]` - Delete an open fixture

**Results Management:**
• `/admin results enter [week]` - Enter actual scores (DM workflow)
• `/admin results calculate [week]` - Calculate and post scores
• `/admin results post` - Re-post results with optional mentions

**Admin Workflow:**
1. **Create Fixture:**
   - `/admin fixture create`
   - Bot DMs you
   - Send game list (one per line)
   - Choose deadline (default or custom)
   - Confirm
   - Bot auto-creates thread for predictions

2. **Enter Results:**
   - `/admin results enter` (add `week:` if multiple fixtures are open)
   - Bot DMs you
   - Send actual scores:
     ```
     Team A - Team B 1:0
     Team C - Team D 2:2
     ...
     ```
   - Confirm

3. **Calculate Scores:**
   - `/admin results calculate` (add `week:` if multiple fixtures are open)
   - Bot posts results (overall + week) to channel

4. **Re-post Results:**
   - `/admin results post`
   - Choose whether to mention users

**Custom Deadline Format:**
• `2024-02-15 18:00`
• `15.02.2024 18:00`
• `15/02/2024 18:00`

⚠️ Make sure your Discord allows DMs from server members!"""

        await interaction.response.send_message(user_help, ephemeral=True)

        if is_admin_user:
            await interaction.followup.send(admin_help, ephemeral=True)

    @app_commands.command(name="fixtures", description="View open fixtures")
    async def fixtures(self, interaction: discord.Interaction):
        """Display current fixtures."""
        open_fixtures = await self.db.get_open_fixtures()

        if not open_fixtures:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        if len(open_fixtures) == 1:
            fixture = open_fixtures[0]
            lines = [f"### Week {fixture['week_number']} Fixtures\n"]

            for i, game in enumerate(fixture["games"], 1):
                lines.append(f"{i}. {game}")

            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")
            lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")
        else:
            lines = ["### Open Fixtures\n"]
            for fixture in open_fixtures:
                lines.append(f"**Week {fixture['week_number']}**")
                for i, game in enumerate(fixture["games"], 1):
                    lines.append(f"{i}. {game}")
                deadline_str = format_for_discord(fixture["deadline"], "F")
                relative_str = format_for_discord(fixture["deadline"], "R")
                lines.append(f"Deadline: {deadline_str} ({relative_str})")
                lines.append("")

        await self._send_chunked_ephemeral(interaction, "\n".join(lines))

    @app_commands.command(
        name="standings", description="View overall standings and last week's results"
    )
    async def standings(self, interaction: discord.Interaction):
        """Display overall standings."""
        standings = await self.db.get_standings()
        last_fixture = await self.db.get_last_fixture_scores()

        message = format_standings(standings, last_fixture)

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="mypredictions", description="View your predictions for open fixtures"
    )
    async def my_predictions(self, interaction: discord.Interaction):
        """Show user's current predictions."""
        open_fixtures = await self.db.get_open_fixtures()

        if not open_fixtures:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        if len(open_fixtures) == 1:
            fixture = open_fixtures[0]
            prediction = await self.db.get_prediction(fixture["id"], str(interaction.user.id))

            if not prediction:
                await interaction.response.send_message(
                    "You haven't submitted predictions for this week yet!\n"
                    "Use `/predict` to enter your scores.",
                    ephemeral=True,
                )
                return

            lines = ["**Your Predictions:**\n"]
            for i, (game, pred) in enumerate(
                zip(fixture["games"], prediction["predictions"], strict=False), 1
            ):
                lines.append(f"{i}. {game} **{pred}**")

            late_status = "⚠️ **LATE**" if prediction["is_late"] else "✅ On time"
            submitted = format_for_discord(prediction["submitted_at"], "f")
            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")

            lines.extend(
                [
                    f"\n**Deadline:** {deadline_str} ({relative_str})",
                    f"**Status:** {late_status}",
                    f"**Submitted:** {submitted}",
                ]
            )

            await self._send_chunked_ephemeral(interaction, "\n".join(lines))
            return

        user_id = str(interaction.user.id)
        lines = ["**Your Predictions (Open Fixtures):**", ""]
        has_any_prediction = False

        for fixture in open_fixtures:
            prediction = await self.db.get_prediction(fixture["id"], user_id)
            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")

            lines.append(f"**Week {fixture['week_number']}**")
            lines.append(f"Deadline: {deadline_str} ({relative_str})")

            if not prediction:
                lines.append("No prediction submitted yet.")
                lines.append("")
                continue

            has_any_prediction = True
            for i, (game, pred) in enumerate(
                zip(fixture["games"], prediction["predictions"], strict=False), 1
            ):
                lines.append(f"{i}. {game} **{pred}**")

            late_status = "⚠️ **LATE**" if prediction["is_late"] else "✅ On time"
            submitted = format_for_discord(prediction["submitted_at"], "f")
            lines.append(f"Status: {late_status}")
            lines.append(f"Submitted: {submitted}")
            lines.append("")

        if not has_any_prediction:
            lines.append("Use `/predict` to submit your scores.")

        await self._send_chunked_ephemeral(interaction, "\n".join(lines))


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(UserCommands(bot))
