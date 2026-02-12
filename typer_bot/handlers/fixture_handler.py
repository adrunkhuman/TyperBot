"""Handler for fixture creation DM workflow."""

import logging
from datetime import datetime, timedelta

import discord
from discord import ui

from typer_bot.database import Database
from typer_bot.utils import APP_TZ, format_for_discord, now

logger = logging.getLogger(__name__)

# user_id -> {"channel_id": int, "guild_id": int, "games": list, "deadline": datetime, "step": str, "created_at": datetime}
_pending_fixtures: dict = {}

MAX_MESSAGE_LENGTH = 5000
MAX_GAMES = 100
SESSION_TIMEOUT_HOURS = 1


def _cleanup_expired_sessions():
    """Remove fixture sessions older than SESSION_TIMEOUT_HOURS."""
    current_time = now()
    expired = [
        user_id
        for user_id, state in _pending_fixtures.items()
        if current_time - state.get("created_at", current_time)
        > timedelta(hours=SESSION_TIMEOUT_HOURS)
    ]
    for user_id in expired:
        _pending_fixtures.pop(user_id, None)
        logger.debug(f"Cleaned up expired fixture session for {user_id}")


class FixtureCreationHandler:
    """Handles the DM workflow for creating fixtures."""

    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db

    def start_session(self, user_id: str, channel_id: int, guild_id: int) -> None:
        """Initialize a new fixture creation session."""
        _cleanup_expired_sessions()
        _pending_fixtures[user_id] = {
            "channel_id": channel_id,
            "guild_id": guild_id,
            "step": "games",
            "created_at": now(),
        }

    def has_session(self, user_id: str) -> bool:
        """Check if user has an active fixture creation session."""
        _cleanup_expired_sessions()
        return user_id in _pending_fixtures

    async def handle_dm(self, message: discord.Message, user_id: str, is_admin_fn) -> bool:
        """Handle a DM message for fixture creation.

        Args:
            message: The DM message
            user_id: User ID as string
            is_admin_fn: Function to check if member is admin

        Returns:
            True if message was handled, False otherwise
        """
        if user_id not in _pending_fixtures:
            return False

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return True

        # Verify admin status
        state = _pending_fixtures[user_id]
        guild_id = state.get("guild_id")

        if not await self._verify_admin(message, user_id, guild_id, is_admin_fn):
            return True

        step = state.get("step", "games")

        if step == "games":
            await self._handle_games_step(message, user_id)
        elif step == "deadline":
            await self._handle_deadline_step(message, user_id)

        return True

    async def _verify_admin(
        self, message: discord.Message, user_id: str, guild_id: int | None, is_admin_fn
    ) -> bool:
        """Verify user is still an admin."""
        if not guild_id:
            logger.warning(f"No guild_id in fixture state for user {user_id}")
            _pending_fixtures.pop(user_id, None)
            await message.author.send("Permission denied or session expired.")
            return False

        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.warning(f"Guild not found for ID: {guild_id}")
            _pending_fixtures.pop(user_id, None)
            await message.author.send("Permission denied or session expired.")
            return False

        member = guild.get_member(int(user_id))
        if not member:
            logger.warning(
                f"Member not found in guild cache for user {user_id}. "
                "Members intent may not be enabled."
            )
            _pending_fixtures.pop(user_id, None)
            await message.author.send("Permission denied or session expired.")
            return False

        if not is_admin_fn(member):
            logger.warning(f"Permission denied for user {user_id}")
            _pending_fixtures.pop(user_id, None)
            await message.author.send("Permission denied or session expired.")
            return False

        return True

    async def _handle_games_step(self, message: discord.Message, user_id: str) -> None:
        """Handle games list input."""
        state = _pending_fixtures[user_id]

        games = [line.strip() for line in message.content.strip().split("\n") if line.strip()]

        if len(games) > MAX_GAMES:
            await message.author.send(f"Too many games! (max {MAX_GAMES})")
            return

        if len(games) < 1:
            await message.author.send("No games provided! Please send the fixture list again.")
            return

        state["games"] = games
        state["step"] = "deadline"

        # Calculate default deadline (Friday 18:00)
        current_time = now()
        days_until_friday = (4 - current_time.weekday()) % 7
        if days_until_friday == 0 and current_time.hour >= 18:
            days_until_friday = 7
        default_deadline = current_time + timedelta(days=days_until_friday)
        default_deadline = default_deadline.replace(hour=18, minute=0, second=0, microsecond=0)
        state["default_deadline"] = default_deadline

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
        state = _pending_fixtures[user_id]

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

        state["deadline"] = deadline
        await self._show_preview(message.author, user_id)

    async def _show_preview(self, user: discord.User, user_id: str) -> None:
        """Show fixture preview with confirmation."""
        state = _pending_fixtures[user_id]
        games = state["games"]
        deadline = state.get("deadline", state["default_deadline"])
        channel = self.bot.get_channel(state["channel_id"])

        if not channel:
            await user.send("Error: Could not find the original channel.")
            _pending_fixtures.pop(user_id, None)
            return

        max_week = await self.db.get_max_week_number()
        week_number = max_week + 1
        state["week_number"] = week_number

        lines = [f"**Week {week_number} Fixture Preview**\n"]
        for i, game in enumerate(games, 1):
            lines.append(f"{i}. {game}")

        deadline_str = format_for_discord(deadline, "F")
        relative_str = format_for_discord(deadline, "R")
        lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")

        warning = ""
        if len(games) != 9:
            warning = f"\n\n⚠️ **Warning:** Expected 9 games, got {len(games)}"

        preview_text = "\n".join(lines)
        state["preview"] = preview_text + warning
        state["step"] = "confirm"

        view = FixtureConfirmView(
            self, user_id, week_number, games, deadline, channel, preview_text + warning
        )
        await user.send(f"{preview_text}{warning}\n\nCreate this fixture?", view=view)

    async def create_fixture(
        self, user_id: str, week_number: int, games: list, deadline: datetime
    ) -> None:
        """Create the fixture in the database."""
        await self.db.create_fixture(week_number, games, deadline)
        _pending_fixtures.pop(user_id, None)

    def cancel_session(self, user_id: str) -> None:
        """Cancel the fixture creation session."""
        _pending_fixtures.pop(user_id, None)


class DeadlineChoiceView(ui.View):
    """View for choosing deadline type."""

    def __init__(self, handler: FixtureCreationHandler, user_id: str):
        super().__init__(timeout=120)
        self.handler = handler
        self.user_id = user_id

    async def on_timeout(self):
        _pending_fixtures.pop(self.user_id, None)

    @ui.button(label="Use Default (Friday 18:00)", style=discord.ButtonStyle.primary)
    async def use_default(self, interaction: discord.Interaction, _button: ui.Button):
        """Use default deadline."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return

        state = _pending_fixtures.get(self.user_id)
        if not state:
            await interaction.response.edit_message(
                content="Session expired. Please start over with `/admin fixture create`.",
                view=None,
            )
            return

        state["deadline"] = state["default_deadline"]
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
        _pending_fixtures.pop(self.user_id, None)

    @ui.button(label="Create Fixture", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, _button: ui.Button):
        """Save fixture to database and announce."""
        await self.handler.create_fixture(self.user_id, self.week_number, self.games, self.deadline)

        await interaction.response.edit_message(
            content=f"**Week {self.week_number} Fixture Created!**\n\n{self.preview}", view=None
        )

        try:
            # Send announcement message
            announcement = await self.channel.send(
                f"**Week {self.week_number} Fixture is now open!**\n\n"
                f"{self.preview}\n\n"
                f"💬 **How to predict:**\n"
                f"• Reply in this thread with your scores (one per line)\n"
                f"• Or use `/predict` for DM mode\n"
                f"• You can edit your prediction anytime before the deadline"
            )

            fixture = await self.handler.db.get_current_fixture()
            if fixture:
                await self.handler.db.update_fixture_announcement(
                    fixture["id"],
                    message_id=str(announcement.id),
                )

            # Create a thread for predictions
            try:
                thread = await announcement.create_thread(
                    name=f"Week {self.week_number} Predictions",
                    auto_archive_duration=1440,  # 24 hours
                )
                await thread.send(
                    "💬 **Post your predictions here!**\n"
                    "Reply with your scores (one per line or comma-separated).\n"
                    "You can edit your message anytime before the deadline."
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

    @ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: ui.Button):
        """Cancel fixture creation."""
        self.handler.cancel_session(self.user_id)
        await interaction.response.edit_message(content="Fixture creation cancelled.", view=None)
