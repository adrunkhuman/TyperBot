"""Handler for fixture creation DM workflow."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import discord
from discord import ui

from typer_bot.database import Database
from typer_bot.services.workflow_state import FixtureSession, WorkflowStateStore
from typer_bot.utils import APP_TZ, format_for_discord, now
from typer_bot.utils.logger import log_event

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000
MAX_GAMES = 100


class FixtureCreationHandler:
    """Handles the DM workflow for creating fixtures."""

    def __init__(self, bot: discord.Client, db: Database, workflow_state: WorkflowStateStore):
        self.bot = bot
        self.db = db
        self.workflow_state = workflow_state

    def get_session(self, user_id: str) -> FixtureSession | None:
        """Return the active fixture session for a user."""
        return self.workflow_state.get_fixture_session(user_id)

    def start_session(self, user_id: str, channel_id: int, guild_id: int) -> None:
        """Initialize a new fixture creation session."""
        self.workflow_state.start_fixture_session(user_id, channel_id, guild_id)
        log_event(
            logger,
            event_type="session.fixture.started",
            message="Fixture creation session started",
            level=logging.DEBUG,
            user_id=user_id,
            guild_id=guild_id,
            step="games",
        )

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active fixture creation session."""
        return self.workflow_state.has_fixture_session(user_id)

    async def handle_dm(
        self,
        message: discord.Message,
        user_id: str,
        is_admin_fn: Callable[[discord.Member | None], bool],
    ) -> bool:
        """Handle a DM message for fixture creation.

        Args:
            message: The DM message
            user_id: User ID as string
            is_admin_fn: Function to check if member is admin

        Returns:
            True if message was handled, False otherwise
        """
        if not self.has_session(user_id):
            return False

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return True

        state = self.get_session(user_id)
        if state is None:
            return False

        guild_id = state.guild_id

        if not await self._verify_admin(message, user_id, guild_id, is_admin_fn):
            return True

        step = state.step

        if step == "games":
            await self._handle_games_step(message, user_id)
        elif step == "deadline":
            await self._handle_deadline_step(message, user_id)

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
            logger.warning(f"No guild_id in fixture state for user {user_id}")
            self.workflow_state.clear_fixture_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Guild not found for ID: {guild_id}")
            self.workflow_state.clear_fixture_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        member = guild.get_member(int(user_id))
        if not member:
            logger.warning(
                f"Member not found in guild cache for user {user_id}. "
                "Members intent may not be enabled."
            )
            self.workflow_state.clear_fixture_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        if not is_admin_fn(member):
            logger.warning(f"Permission denied for user {user_id}")
            self.workflow_state.clear_fixture_session(user_id)
            await message.author.send("Permission denied or session expired.")
            return False

        return True

    async def _handle_games_step(self, message: discord.Message, user_id: str) -> None:
        """Handle games list input."""
        state = self.get_session(user_id)
        if state is None:
            return

        games = [line.strip() for line in message.content.strip().split("\n") if line.strip()]

        if len(games) > MAX_GAMES:
            await message.author.send(f"Too many games! (max {MAX_GAMES})")
            return

        if len(games) < 1:
            await message.author.send("No games provided! Please send the fixture list again.")
            return

        state.games = games

        # Deadline already set means admin is editing games from the confirmation screen
        if state.deadline is not None:
            await self._show_preview(message.author, user_id)
            return

        state.step = "deadline"

        current_time = now()
        days_until_friday = (4 - current_time.weekday()) % 7
        if days_until_friday == 0 and current_time.hour >= 18:
            days_until_friday = 7
        default_deadline = current_time + timedelta(days=days_until_friday)
        default_deadline = default_deadline.replace(hour=18, minute=0, second=0, microsecond=0)
        state.default_deadline = default_deadline

        default_str = format_for_discord(default_deadline, "F")
        relative_str = format_for_discord(default_deadline, "R")

        view = DeadlineChoiceView(self, user_id)
        await message.author.send(
            f"**Choose Deadline**\n\n"
            f"Default: **{default_str}** ({relative_str})\n\n"
            f"Or type a custom deadline in format: `YYYY-MM-DD HH:MM`\n"
            f"Example: `{current_time.strftime('%Y-%m-%d')} 20:00` for today at 8 PM",
            view=view,
        )

    async def _handle_deadline_step(self, message: discord.Message, user_id: str) -> None:
        """Handle deadline input."""
        state = self.get_session(user_id)
        if state is None:
            return

        deadline = None
        formats = [
            "%Y-%m-%d %H:%M",
            "%d.%m.%Y %H:%M",
            "%d/%m/%Y %H:%M",
        ]

        for fmt in formats:
            try:
                naive_deadline = datetime.strptime(message.content.strip(), fmt)
                deadline = naive_deadline.replace(tzinfo=APP_TZ)
                break
            except ValueError:
                continue

        if deadline is None:
            await message.author.send(
                "Invalid date format. Please use one of these formats:\n"
                "```\n"
                "2024-02-15 18:00\n"
                "15.02.2024 18:00\n"
                "15/02/2024 18:00\n"
                "```\n"
                "Or click the 'Use Default' button above."
            )
            return

        state.deadline = deadline
        await self._show_preview(message.author, user_id)

    async def _show_preview(self, user: discord.User | discord.Member, user_id: str) -> None:
        """Show fixture preview with confirmation."""
        state = self.get_session(user_id)
        if state is None:
            await user.send("Session expired. Please start over with `/admin fixture create`.")
            return

        games = state.games
        deadline = state.deadline or state.default_deadline
        channel = self.bot.get_channel(state.channel_id)

        if deadline is None:
            await user.send("Session expired. Please start over with `/admin fixture create`.")
            self.workflow_state.clear_fixture_session(user_id)
            return

        if not channel or not isinstance(channel, discord.TextChannel):
            await user.send("Error: Could not find the original channel.")
            self.workflow_state.clear_fixture_session(user_id)
            return

        max_week = await self.db.get_max_week_number()
        week_number = max_week + 1
        state.week_number = week_number

        preview_text = self._build_fixture_preview_text(week_number, games, deadline)
        state.preview = preview_text
        state.step = "confirm"

        view = FixtureConfirmView(
            self, user_id, week_number, games, deadline, channel, preview_text
        )
        await user.send(f"{preview_text}\n\nCreate this fixture?", view=view)

    @staticmethod
    def _build_fixture_preview_text(week_number: int, games: list[str], deadline: datetime) -> str:
        """Build a fixture preview block for DMs and announcements."""
        lines = [f"**Week {week_number} Fixture Preview**\n"]
        for i, game in enumerate(games, 1):
            lines.append(f"{i}. {game}")

        deadline_str = format_for_discord(deadline, "F")
        relative_str = format_for_discord(deadline, "R")
        lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")

        if len(games) != 9:
            lines.append(f"\n⚠️ **Warning:** Expected 9 games, got {len(games)}")

        return "\n".join(lines)

    async def create_fixture(
        self, user_id: str, games: list, deadline: datetime
    ) -> tuple[int, int]:
        """Create the fixture in the database and return ID + allocated week."""
        fixture_id, allocated_week = await self.db.create_next_fixture(games, deadline)
        self.workflow_state.clear_fixture_session(user_id)
        log_event(
            logger,
            event_type="fixture.created",
            message=f"Fixture created: Week {allocated_week}",
            user_id=user_id,
            fixture_id=fixture_id,
            week_number=allocated_week,
            games_count=len(games),
        )
        return fixture_id, allocated_week

    def cancel_session(self, user_id: str, reason: str = "cancelled") -> None:
        """Cancel the fixture creation session."""
        self.workflow_state.clear_fixture_session(user_id)
        log_event(
            logger,
            event_type="session.fixture.completed",
            message=f"Fixture creation session {reason}",
            level=logging.DEBUG,
            user_id=user_id,
            reason=reason,
        )


class DeadlineChoiceView(ui.View):
    """View for choosing deadline type."""

    def __init__(self, handler: FixtureCreationHandler, user_id: str):
        super().__init__(timeout=120)
        self.handler = handler
        self.user_id = user_id

    async def on_timeout(self):
        self.handler.cancel_session(self.user_id, reason="timeout")

    @ui.button(label="Use Default (Friday 18:00)", style=discord.ButtonStyle.primary)
    async def use_default(self, interaction: discord.Interaction, _button: ui.Button):
        """Use default deadline."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return

        state = self.handler.get_session(self.user_id)
        if not state:
            await interaction.response.edit_message(
                content="Session expired. Please start over with `/admin fixture create`.",
                view=None,
            )
            return

        if state.default_deadline is None:
            await interaction.response.edit_message(
                content="Session expired. Please start over with `/admin fixture create`.",
                view=None,
            )
            self.handler.cancel_session(self.user_id, reason="missing_default_deadline")
            return

        state.deadline = state.default_deadline
        await interaction.response.edit_message(
            content="Using default deadline. Showing preview...", view=None
        )
        await self.handler._show_preview(interaction.user, self.user_id)


class FixtureConfirmView(ui.View):
    """View for confirming fixture creation."""

    def __init__(
        self,
        handler: FixtureCreationHandler,
        user_id: str,
        week_number: int,
        games: list,
        deadline: datetime,
        channel: discord.TextChannel,
        preview: str,
    ):
        super().__init__(timeout=120)
        self.handler = handler
        self.user_id = user_id
        self.week_number = week_number
        self.games = games
        self.deadline = deadline
        self.channel = channel
        self.preview = preview

    async def on_timeout(self):
        self.handler.cancel_session(self.user_id, reason="timeout")

    @ui.button(label="Create Fixture", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, _button: ui.Button):
        """Save fixture to database and announce."""
        fixture_id, allocated_week = await self.handler.create_fixture(
            self.user_id,
            self.games,
            self.deadline,
        )

        final_preview = self.handler._build_fixture_preview_text(
            allocated_week,
            self.games,
            self.deadline,
        )

        created_text = f"**Week {allocated_week} Fixture Created!**\n\n{final_preview}"
        if allocated_week != self.week_number:
            created_text += (
                f"\n\n⚠️ **Week number changed:** preview showed Week {self.week_number} but "
                f"this was created as **Week {allocated_week}** because the fixture set "
                f"changed between steps."
            )

        await interaction.response.edit_message(
            content=created_text,
            view=None,
        )

        try:
            announcement = await self.channel.send(
                f"**Week {allocated_week} Fixture is now open!**\n\n"
                f"{final_preview}\n\n"
                f"💬 **How to predict:**\n"
                f"• Reply in this thread with your scores (one per line)\n"
                f"• Or use `/predict` for DM mode"
            )

            await self.handler.db.update_fixture_announcement(
                fixture_id,
                message_id=str(announcement.id),
                channel_id=str(self.channel.id),
            )

            try:
                thread = await announcement.create_thread(
                    name=f"Week {allocated_week} Predictions",
                    auto_archive_duration=1440,  # 24 hours
                )
                await thread.send(
                    "💬 **Post your predictions here!**\n"
                    "Reply with your scores (one per line or comma-separated).\n"
                    "Predictions are one-shot here. To change one, use `/predict`."
                )
            except Exception as e:
                logger.warning(f"Could not create thread for fixture: {e}")
                await interaction.followup.send(
                    "⚠️ Fixture created but I couldn't create a prediction thread. Users can still use `/predict`.",
                    ephemeral=True,
                )
        except Exception:
            await interaction.followup.send(
                "⚠️ Fixture created but I couldn't announce it in the channel. Please announce it manually.",
                ephemeral=True,
            )

    @ui.button(label="Edit Games", style=discord.ButtonStyle.secondary)
    async def edit_games(self, interaction: discord.Interaction, _button: ui.Button):
        """Go back to the games entry step, keeping the chosen deadline."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return

        state = self.handler.get_session(self.user_id)
        if state is None:
            await interaction.response.edit_message(
                content="Session expired. Please start over with `/admin fixture create`.",
                view=None,
            )
            return

        state.step = "games"
        await interaction.response.edit_message(
            content="Send me the corrected games list (one game per line).",
            view=None,
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: ui.Button):
        """Cancel fixture creation."""
        self.handler.cancel_session(self.user_id, reason="user_cancelled")
        await interaction.response.edit_message(content="Fixture creation cancelled.", view=None)
