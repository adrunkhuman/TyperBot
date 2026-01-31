"""User-facing Discord commands."""

import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.utils import format_standings

# Store pending predictions: user_id -> (fixture_id, games)
pending_predictions = {}


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs with predictions."""
        # Ignore bot messages and non-DMs
        if message.author.bot or message.guild is not None:
            return

        # Check if this user has a pending prediction
        user_id = str(message.author.id)
        if user_id not in pending_predictions:
            return

        fixture_id, games = pending_predictions.pop(user_id)
        fixture = await self.db.get_fixture_by_id(fixture_id)

        if not fixture:
            await message.author.send("❌ Error: Fixture no longer exists.")
            return

        # Send acknowledgment first
        processing_msg = await message.author.send("⏳ Processing your predictions...")

        try:
            # Check deadline
            now = datetime.now()
            is_late = now > fixture["deadline"]

            # Parse predictions from user's message
            # Expected format: "Team A - Team B 2:0" or "Team A - Team B 2-1"
            lines = message.content.strip().split("\n")
            predictions = []
            errors = []

            if len(lines) != len(games):
                errors.append(f"Expected {len(games)} lines, got {len(lines)}")
            else:
                for i, (line, _game) in enumerate(zip(lines, games, strict=False)):
                    # Try to extract score at the end of the line
                    # Pattern: anything followed by score like "2:0" or "2-1"
                    match = re.search(r"(\d+)\s*[-:]\s*(\d+)\s*$", line.strip())
                    if match:
                        home_score = match.group(1)
                        away_score = match.group(2)
                        # Validate single digits
                        if len(home_score) > 1 or len(away_score) > 1:
                            errors.append(
                                f"Line {i + 1}: Double-digit scores not allowed ({home_score}-{away_score})"
                            )
                        else:
                            predictions.append(f"{home_score}-{away_score}")
                    else:
                        errors.append(
                            f"Line {i + 1}: Could not find score (expected format: '2:0' or '2-1')"
                        )

            if errors:
                error_msg = "\n".join(errors)
                await processing_msg.edit(
                    content=f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                    f"Please send your predictions again in this format:\n"
                    f"```\n{games[0]} 2:0\n{games[1]} 1:1\n...\n```"
                )
                # Put back in pending so they can retry
                pending_predictions[user_id] = (fixture_id, games)
                return

            # Build preview
            preview_lines = ["**Your Predictions:**\n"]
            for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
                preview_lines.append(f"{i}. {game} **{pred}**")

            deadline_str = fixture["deadline"].strftime("%A, %B %d at %H:%M")
            preview_lines.append(f"\n**Deadline:** {deadline_str}")

            late_warning = ""
            if is_late:
                late_warning = (
                    "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."
                )

            preview_text = "\n".join(preview_lines)

            # Show confirmation with buttons
            view = PredictionConfirmView(
                self.db,
                fixture_id,
                message.author.id,
                message.author.display_name,
                predictions,
                is_late,
                preview_text,
            )

            await processing_msg.edit(
                content=f"{preview_text}{late_warning}\n\nSubmit these predictions?", view=view
            )

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error processing predictions: {e}", exc_info=True)
            await processing_msg.edit(
                content=f"❌ Error processing predictions: {e}\n\nPlease try again."
            )
            pending_predictions[user_id] = (fixture_id, games)

    @app_commands.command(
        name="predict", description="Submit your predictions for this week's fixtures"
    )
    async def predict(self, interaction: discord.Interaction):
        """Initiate prediction submission via DM."""
        # Get current fixture
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.", ephemeral=True
            )
            return

        # Check if user already submitted
        existing = await self.db.get_prediction(fixture["id"], str(interaction.user.id))
        if existing:
            await interaction.response.send_message(
                "You already submitted predictions for this week!\n"
                "Use `/mypredictions` to view them.",
                ephemeral=True,
            )
            return

        # Store pending request
        pending_predictions[str(interaction.user.id)] = (fixture["id"], fixture["games"])

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you the fixture list to predict.", ephemeral=True
        )

        # Build fixture list for DM
        lines = [
            f"**Week {fixture['week_number']} - Submit Your Predictions**",
            "",
            "Reply with your predictions in this format:",
            "```",
        ]
        for game in fixture["games"]:
            lines.append(f"{game} 2:0")
        lines.extend(
            [
                "```",
                "",
                "Add your score (e.g., 2:0 or 2-1) at the end of each line.",
                f"\n**Deadline:** {fixture['deadline'].strftime('%A, %B %d at %H:%M')}",
            ]
        )

        # Send DM
        try:
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            pending_predictions.pop(str(interaction.user.id), None)
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(name="fixtures", description="View this week's fixtures")
    async def fixtures(self, interaction: discord.Interaction):
        """Display current fixtures."""
        fixture = await self.db.get_current_fixture()

        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        # Build fixture display
        lines = [f"### Week {fixture['week_number']} Fixtures\n"]

        for i, game in enumerate(fixture["games"], 1):
            lines.append(f"{i}. {game}")

        deadline_str = fixture["deadline"].strftime("%A, %B %d at %H:%M")
        lines.append(f"\n**Deadline:** {deadline_str}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(
        name="standings", description="View overall standings and last week's results"
    )
    async def standings(self, interaction: discord.Interaction):
        """Display overall standings."""
        standings = await self.db.get_standings()
        last_fixture = await self.db.get_last_fixture_scores()

        message = format_standings(standings, last_fixture)

        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="mypredictions", description="View your predictions for this week")
    async def my_predictions(self, interaction: discord.Interaction):
        """Show user's current predictions."""
        fixture = await self.db.get_current_fixture()

        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        prediction = await self.db.get_prediction(fixture["id"], str(interaction.user.id))

        if not prediction:
            await interaction.response.send_message(
                "You haven't submitted predictions for this week yet!\n"
                "Use `/predict` to enter your scores.",
                ephemeral=True,
            )
            return

        # Build display
        lines = ["**Your Predictions:**\n"]
        for i, (game, pred) in enumerate(
            zip(fixture["games"], prediction["predictions"], strict=False), 1
        ):
            lines.append(f"{i}. {game} **{pred}**")

        late_status = "⚠️ **LATE**" if prediction["is_late"] else "✅ On time"
        submitted = prediction["submitted_at"].strftime("%Y-%m-%d %H:%M")
        deadline_str = fixture["deadline"].strftime("%A, %B %d at %H:%M")

        lines.extend(
            [
                f"\n**Deadline:** {deadline_str}",
                f"**Status:** {late_status}",
                f"**Submitted:** {submitted}",
            ]
        )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class PredictionConfirmView(discord.ui.View):
    """View for confirming predictions."""

    def __init__(
        self,
        db: Database,
        fixture_id: int,
        user_id: int,
        user_name: str,
        predictions: list[str],
        is_late: bool,
        preview: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.fixture_id = fixture_id
        self.user_id = user_id
        self.user_name = user_name
        self.predictions = predictions
        self.is_late = is_late
        self.preview = preview

    @discord.ui.button(label="✅ Submit Predictions", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save predictions."""
        await self.db.save_prediction(
            self.fixture_id, str(self.user_id), self.user_name, self.predictions, self.is_late
        )

        status = " (late penalty applied)" if self.is_late else ""
        await interaction.response.edit_message(
            content=f"✅ **Predictions saved{status}!**\n\n{self.preview}", view=None
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel predictions."""
        await interaction.response.edit_message(
            content="❌ Predictions cancelled. Use `/predict` to try again.", view=None
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(UserCommands(bot))
