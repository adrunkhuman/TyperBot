"""User-facing Discord commands."""

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.handlers import DMPredictionHandler
from typer_bot.services import WorkflowStateStore
from typer_bot.utils import (
    format_for_discord,
    format_standings,
    is_admin,
)


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # TyperBot sets db attr dynamically; discord.py typing doesn't track custom attrs
        self.db: Database = bot.db  # type: ignore
        self.workflow_state: WorkflowStateStore = bot.workflow_state  # type: ignore[attr-defined]
        self.prediction_handler = DMPredictionHandler(self.db, self.workflow_state)

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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs with predictions."""
        await self.prediction_handler.handle_dm(message)

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
            await self.prediction_handler.start_flow(interaction.user, open_fixtures)
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

**To change a prediction:** Use `/predict` or DM the bot again. Thread posts do not edit existing picks."""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
• `/admin panel` - Open the admin hub for fixture deletion, overrides, waivers, and result correction
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

5. **Corrections / Exceptions:**
   - `/admin panel`
   - View fixture predictions
   - Replace a stored prediction without changing original submit time
   - Toggle a late-penalty waiver for an approved late pick
   - Correct stored results and auto-recalculate scored fixtures

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

            late_status = "✅ On time"
            if prediction["is_late"]:
                late_status = "⚠️ **LATE**"
                if prediction["late_penalty_waived"]:
                    late_status += " (waiver active)"
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

            late_status = "✅ On time"
            if prediction["is_late"]:
                late_status = "⚠️ **LATE**"
                if prediction["late_penalty_waived"]:
                    late_status += " (waiver active)"
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
