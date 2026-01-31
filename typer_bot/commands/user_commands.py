"""User-facing Discord commands."""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from ..database import Database
from ..utils import parse_predictions, format_standings


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    @app_commands.command(name="predict", description="Submit your predictions for this week's fixtures")
    @app_commands.describe(scores="Your predictions (e.g., '2-1 1-0 3-3...')")
    async def predict(self, interaction: discord.Interaction, scores: str):
        """Submit predictions for the current fixture."""
        # Get current fixture
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.",
                ephemeral=True
            )
            return

        # Check deadline
        now = datetime.now()
        is_late = now > fixture["deadline"]
        
        # Parse predictions
        expected_count = len(fixture["games"])
        predictions, errors = parse_predictions(scores, expected_count)
        
        if errors:
            error_msg = "\n".join(errors)
            await interaction.response.send_message(
                f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                f"Expected {expected_count} scores. Example: `2-1 1-0 3-3...`",
                ephemeral=True
            )
            return

        # Show preview
        from ..utils.prediction_parser import format_predictions_preview
        preview = format_predictions_preview(fixture["games"], predictions)
        
        late_warning = "\n\n⚠️ **Late prediction!** You will receive 0 points for this round." if is_late else ""
        
        # Create confirmation view
        view = PredictionConfirmView(
            self.db, fixture["id"], interaction.user.id, 
            interaction.user.display_name, predictions, is_late,
            preview + late_warning
        )
        
        await interaction.response.send_message(
            f"{preview}{late_warning}\n\nConfirm your predictions?",
            view=view,
            ephemeral=True
        )

    @app_commands.command(name="fixtures", description="View this week's fixtures")
    async def fixtures(self, interaction: discord.Interaction):
        """Display current fixtures."""
        fixture = await self.db.get_current_fixture()
        
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found!",
                ephemeral=True
            )
            return

        # Build fixture display
        lines = [f"### Week {fixture['week_number']} Fixtures\n"]
        
        for i, game in enumerate(fixture["games"], 1):
            lines.append(f"{i}. {game}")
        
        deadline_str = fixture["deadline"].strftime("%A, %B %d at %H:%M")
        lines.append(f"\n**Deadline:** {deadline_str}")
        
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="standings", description="View overall standings and last week's results")
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
            await interaction.response.send_message(
                "❌ No active fixture found!",
                ephemeral=True
            )
            return

        prediction = await self.db.get_prediction(fixture["id"], str(interaction.user.id))
        
        if not prediction:
            await interaction.response.send_message(
                "You haven't submitted predictions for this week yet!\n"
                "Use `/predict` to enter your scores.",
                ephemeral=True
            )
            return

        from ..utils.prediction_parser import format_predictions_preview
        preview = format_predictions_preview(fixture["games"], prediction["predictions"])
        
        late_status = "⚠️ **LATE**" if prediction["is_late"] else "✅ On time"
        submitted = prediction["submitted_at"].strftime("%Y-%m-%d %H:%M")
        
        await interaction.response.send_message(
            f"{preview}\n\n"
            f"Status: {late_status}\n"
            f"Submitted: {submitted}",
            ephemeral=True
        )


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
        preview: str
    ):
        super().__init__(timeout=60)
        self.db = db
        self.fixture_id = fixture_id
        self.user_id = user_id
        self.user_name = user_name
        self.predictions = predictions
        self.is_late = is_late
        self.preview = preview

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save predictions."""
        await self.db.save_prediction(
            self.fixture_id, 
            str(self.user_id), 
            self.user_name,
            self.predictions,
            self.is_late
        )
        
        status = " with late penalty" if self.is_late else ""
        await interaction.response.edit_message(
            content=f"✅ **Predictions saved{status}!**\n\n{self.preview}",
            view=None
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel predictions."""
        await interaction.response.edit_message(
            content="❌ Predictions cancelled.",
            view=None
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(UserCommands(bot))