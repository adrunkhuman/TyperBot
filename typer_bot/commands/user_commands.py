"""User-facing Discord commands."""

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.utils import (
    format_for_discord,
    format_standings,
    now,
    parse_line_predictions,
)

# user_id -> (fixture_id, games)
pending_predictions = {}

MAX_MESSAGE_LENGTH = 5000


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs with predictions."""
        import logging

        logger = logging.getLogger(__name__)

        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)
        logger.info(
            f"[USER] on_message from user {user_id}, pending_predictions={user_id in pending_predictions}"
        )
        if user_id not in pending_predictions:
            return

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            pending_predictions.pop(user_id, None)
            return

        fixture_id, games = pending_predictions.pop(user_id)
        fixture = await self.db.get_fixture_by_id(fixture_id)

        if not fixture:
            await message.author.send("❌ Error: Fixture no longer exists.")
            return

        processing_msg = await message.author.send("⏳ Processing your predictions...")

        try:
            current_time = now()
            is_late = current_time > fixture["deadline"]

            lines = message.content.strip().split("\n")
            predictions, errors = parse_line_predictions(lines, games)

            if errors:
                error_msg = "\n".join(errors)
                await processing_msg.edit(
                    content=f"❌ **Invalid predictions:**\n```{error_msg}```\n\n"
                    f"Please send your predictions again in this format:\n"
                    f"```\n{games[0]} 2:0\n{games[1]} 1:1\n...\n```"
                )
                pending_predictions[user_id] = (fixture_id, games)
                return

            preview_lines = ["**Your Predictions:**\n"]
            for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
                preview_lines.append(f"{i}. {game} **{pred}**")

            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")
            preview_lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")

            late_warning = ""
            if is_late:
                late_warning = (
                    "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."
                )

            preview_text = "\n".join(preview_lines)

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
    @app_commands.checks.cooldown(1, 1.0)
    async def predict(self, interaction: discord.Interaction):
        """Initiate prediction submission via DM."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.", ephemeral=True
            )
            return

        existing = await self.db.get_prediction(fixture["id"], str(interaction.user.id))
        if existing:
            await interaction.response.send_message(
                "You already submitted predictions for this week!\n"
                "Use `/mypredictions` to view them.",
                ephemeral=True,
            )
            return

        pending_predictions[str(interaction.user.id)] = (fixture["id"], fixture["games"])

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you the fixture list to predict.", ephemeral=True
        )

        lines = [
            f"**Week {fixture['week_number']} - Submit Your Predictions**",
            "",
            "Reply with your predictions in this format:",
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
                "Add your score (e.g., 2:0 or 2-1) at the end of each line.",
                f"\n**Deadline:** {deadline_str} ({relative_str})",
            ]
        )

        try:
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            pending_predictions.pop(str(interaction.user.id), None)
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(name="help", description="Show help information")
    async def help(self, interaction: discord.Interaction):
        """Display help for users and admins."""
        is_admin_user = self._is_admin(interaction.user)

        user_help = """## 📖 User Commands

**For Players:**
• `/predict` - Submit your predictions (bot will DM you)
• `/fixtures` - View this week's games
• `/standings` - See overall leaderboard
• `/mypredictions` - Check your submitted predictions

**How to Predict:**
1. Type `/predict` in the channel
2. Bot sends you a DM with the fixture list
3. Reply with your predictions:
   ```
   Team A - Team B 2:0
   Team C - Team D 1:1
   ...
   ```
4. Confirm to save your predictions

**Scoring:**
• Exact score: 3 points
• Correct result (win/loss/draw): 1 point
• Wrong: 0 points
• Late predictions: 0 points (submit before deadline!)

**Input formats:** Use `2:0`, `2-0`, or `2 : 0`"""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
• `/admin fixture` - Create new fixture (DM workflow)
• `/admin results` - Enter actual scores (DM workflow)
• `/admin calculate` - Calculate and post scores
• `/admin delete` - Delete current fixture (use with caution!)

**Admin Workflow:**
1. **Create Fixture:**
   - `/admin` → "fixture"
   - Bot DMs you
   - Send game list (one per line)
   - Choose deadline (default or custom)
   - Confirm

2. **Enter Results:**
   - `/admin` → "results"
   - Bot DMs you
   - Send actual scores:
     ```
     Team A - Team B 1:0
     Team C - Team D 2:2
     ...
     ```
   - Confirm

3. **Calculate Scores:**
   - `/admin` → "calculate"
   - Bot posts results automatically

**Custom Deadline Format:**
• `2024-02-15 18:00`
• `15.02.2024 18:00`
• `15/02/2024 18:00`

⚠️ Make sure your Discord allows DMs from server members!"""

        help_text = user_help
        if is_admin_user:
            help_text += admin_help

        await interaction.response.send_message(help_text, ephemeral=True)

    def _is_admin(self, member: discord.Member) -> bool:
        """Check if member has admin role (case-insensitive)."""
        admin_roles = {"admin", "typer-admin"}
        return any(role.name.lower() in admin_roles for role in member.roles)

    @app_commands.command(name="fixtures", description="View this week's fixtures")
    async def fixtures(self, interaction: discord.Interaction):
        """Display current fixtures."""
        fixture = await self.db.get_current_fixture()

        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        lines = [f"### Week {fixture['week_number']} Fixtures\n"]

        for i, game in enumerate(fixture["games"], 1):
            lines.append(f"{i}. {game}")

        deadline_str = format_for_discord(fixture["deadline"], "F")
        relative_str = format_for_discord(fixture["deadline"], "R")
        lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")

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

    async def on_timeout(self):
        pending_predictions.pop(str(self.user_id), None)

    @discord.ui.button(label="✅ Submit Predictions", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save predictions."""
        pending_predictions.pop(str(self.user_id), None)

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
        pending_predictions.pop(str(self.user_id), None)

        await interaction.response.edit_message(
            content="❌ Predictions cancelled. Use `/predict` to try again.", view=None
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(UserCommands(bot))
