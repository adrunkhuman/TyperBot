"""User-facing Discord commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.utils import (
    format_for_discord,
    format_standings,
    is_admin,
    now,
    parse_line_predictions,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 5000


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # TyperBot sets db attr dynamically; discord.py typing doesn't track custom attrs
        self.db: Database = bot.db  # type: ignore

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs with predictions."""
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)
        logger.info(f"[USER] on_message from user {user_id}")

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return

        fixture = await self.db.get_current_fixture()
        if not fixture:
            await message.author.send(
                "ℹ️ No active fixture at the moment. "
                "Ask an admin to create one, or check back later!"
            )
            return

        games = fixture["games"]
        fixture_id = fixture["id"]

        processing_msg = await message.author.send("⏳ Processing your predictions...")

        try:
            current_time = now()
            is_late = current_time > fixture["deadline"]

            predictions, errors = parse_line_predictions(message.content, games)

            if errors:
                error_msg = "\n".join(errors)
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

            preview_lines = ["**Predictions saved!**\n"]
            for i, (game, pred) in enumerate(zip(games, predictions, strict=False), 1):
                preview_lines.append(f"{i}. {game} **{pred}**")

            deadline_str = format_for_discord(fixture["deadline"], "F")
            relative_str = format_for_discord(fixture["deadline"], "R")
            preview_lines.append(f"\n**Deadline:** {deadline_str} ({relative_str})")
            preview_lines.append("\n*Edit your message to update before deadline.*")

            late_warning = ""
            if is_late:
                late_warning = (
                    "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."
                )

            preview_text = "\n".join(preview_lines)

            await processing_msg.edit(content=f"{preview_text}{late_warning}", view=None)

        except Exception as e:
            logger.error(f"Error processing predictions: {e}", exc_info=True)
            await processing_msg.edit(
                content=f"❌ Error processing predictions: {e}\n\nPlease try again."
            )

    @app_commands.command(
        name="predict", description="Submit your predictions for this week's fixtures"
    )
    @app_commands.checks.cooldown(1, 1.0)
    async def predict(self, interaction: discord.Interaction):
        """Send fixture list to user via DM."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you the fixture list to predict.", ephemeral=True
        )

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

        try:
            await interaction.user.send("\n".join(lines))
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
• `/fixtures` - View this week's games
• `/standings` - See overall leaderboard
• `/mypredictions` - Check your submitted predictions

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
4. Edit your message anytime before deadline to update

**Method 2: DM Predictions**
1. Type `/predict` in the channel (or DM the bot directly)
2. Bot sends you a DM with the fixture list
3. Reply with your predictions - they are saved immediately!
4. Edit your message to update before deadline

**Scoring:**
• Exact score: 3 points
• Correct result (win/loss/draw): 1 point
• Wrong: 0 points
• Late predictions: 0 points (submit before deadline!)

**Input formats:** Use `2:0`, `2-0`, or `2 : 0`

**Editing:** You can edit your prediction message before the deadline. Deleting your message removes your prediction."""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
**Fixture Management:**
• `/admin fixture create` - Create new fixture (DM workflow, auto-creates thread)
• `/admin fixture delete` - Delete current fixture

**Results Management:**
• `/admin results enter` - Enter actual scores (DM workflow)
• `/admin results calculate` - Calculate and post scores
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
   - `/admin results enter`
   - Bot DMs you
   - Send actual scores:
     ```
     Team A - Team B 1:0
     Team C - Team D 2:2
     ...
     ```
   - Confirm

3. **Calculate Scores:**
   - `/admin results calculate`
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


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(UserCommands(bot))
