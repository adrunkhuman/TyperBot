"""Admin Discord commands."""

import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.utils import calculate_points

# Store pending fixture creation requests: user_id -> channel_id
pending_fixtures = {}
# Store pending results entry: user_id -> fixture_id
pending_results = {}


class AdminCommands(commands.Cog):
    """Commands for admins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    def is_admin(self, member: discord.Member) -> bool:
        """Check if member has admin role."""
        admin_roles = {"Admin", "typer-admin"}
        return any(role.name in admin_roles for role in member.roles)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs from admins."""
        # Ignore bot messages and non-DMs
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)

        # Check for pending fixture request
        if user_id in pending_fixtures:
            await self._handle_fixture_dm(message, user_id)
            return

        # Check for pending results request
        if user_id in pending_results:
            await self._handle_results_dm(message, user_id)
            return

    async def _handle_fixture_dm(self, message: discord.Message, user_id: str):
        """Handle fixture creation DM."""
        # Get the channel where the command was originally sent
        channel_id = pending_fixtures.pop(user_id)
        channel = self.bot.get_channel(channel_id)

        if not channel:
            await message.author.send(
                "❌ Error: Could not find the original channel. Please try again in the server."
            )
            return

        # Parse games from DM (supports multiline!)
        games = [line.strip() for line in message.content.strip().split("\n") if line.strip()]

        if len(games) < 1:
            await message.author.send("❌ No games provided! Please send the fixture list again.")
            # Put back in pending so they can retry
            pending_fixtures[user_id] = channel_id
            return

        # Get next week number
        current = await self.db.get_current_fixture()
        week_number = 1 if not current else current["week_number"] + 1

        # Default deadline: next Friday 18:00
        now = datetime.now()
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0 and now.hour >= 18:
            days_until_friday = 7
        deadline = now + timedelta(days=days_until_friday)
        deadline = deadline.replace(hour=18, minute=0, second=0, microsecond=0)

        # Build preview
        lines = [f"**Week {week_number} Fixture Preview**\n"]
        for i, game in enumerate(games, 1):
            lines.append(f"{i}. {game}")

        deadline_str = deadline.strftime("%A, %B %d at %H:%M")
        lines.append(f"\n**Deadline:** {deadline_str}")

        # Validation warning
        warning = ""
        if len(games) != 9:
            warning = f"\n\n⚠️ **Warning:** Expected 9 games, got {len(games)}"

        preview_text = "\n".join(lines)

        # Send preview to DM for confirmation
        view = FixtureConfirmView(
            self.db, week_number, games, deadline, channel, preview_text + warning
        )

        await message.author.send(f"{preview_text}{warning}\n\nCreate this fixture?", view=view)

    async def _handle_results_dm(self, message: discord.Message, user_id: str):
        """Handle results entry DM."""
        fixture_id = pending_results.pop(user_id)
        fixture = await self.db.get_fixture_by_id(fixture_id)

        if not fixture:
            await message.author.send("❌ Error: Fixture no longer exists.")
            return

        # Parse results from DM
        # Expected format: "Team A - Team B 2:0" or "Team A - Team B 2-1"
        lines = message.content.strip().split("\n")
        results = []
        errors = []

        if len(lines) != len(fixture["games"]):
            errors.append(f"Expected {len(fixture['games'])} lines, got {len(lines)}")
        else:
            for i, line in enumerate(lines):
                # Try to extract score at the end of the line
                match = re.search(r"\s*(\d+)\s*[-:]\s*(\d+)\s*$", line)
                if match:
                    home_score = match.group(1)
                    away_score = match.group(2)
                    # Validate single digits
                    if len(home_score) > 1 or len(away_score) > 1:
                        errors.append(
                            f"Line {i + 1}: Double-digit scores not allowed ({home_score}-{away_score})"
                        )
                    else:
                        results.append(f"{home_score}-{away_score}")
                else:
                    errors.append(
                        f"Line {i + 1}: Could not find score (expected format: '2:0' or '2-1')"
                    )

        if errors:
            error_msg = "\n".join(errors)
            await message.author.send(
                f"❌ **Invalid results:**\n```{error_msg}```\n\n"
                f"Please send the results again in this format:\n"
                f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```"
            )
            # Put back in pending so they can retry
            pending_results[user_id] = fixture_id
            return

        # Build preview
        lines = [f"**Week {fixture['week_number']} Results Preview**\n"]
        for i, (game, result) in enumerate(zip(fixture["games"], results, strict=False), 1):
            lines.append(f"{i}. {game} **{result}**")

        preview_text = "\n".join(lines)

        # Show confirmation with buttons
        view = ResultsConfirmView(self.db, fixture_id, results, preview_text)

        await message.author.send(f"{preview_text}\n\nSave these results?", view=view)

    @app_commands.command(name="admin", description="Admin commands for managing fixtures")
    @app_commands.describe(action="Action to perform")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="fixture", value="fixture"),
            app_commands.Choice(name="results", value="results"),
            app_commands.Choice(name="calculate", value="calculate"),
            app_commands.Choice(name="close", value="close"),
        ]
    )
    async def admin(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        """Admin command hub."""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to use admin commands.", ephemeral=True
            )
            return

        if action.value == "fixture":
            await self._create_fixture(interaction)
        elif action.value == "results":
            await self._enter_results(interaction)
        elif action.value == "calculate":
            await self._calculate_scores(interaction)
        elif action.value == "close":
            await self._close_fixture(interaction)

    async def _create_fixture(self, interaction: discord.Interaction):
        """Initiate fixture creation via DM."""
        # Store pending request
        pending_fixtures[str(interaction.user.id)] = interaction.channel_id

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you instructions for creating the fixture.",
            ephemeral=True,
        )

        # Send DM with instructions
        try:
            await interaction.user.send(
                "**Create New Fixture**\n\n"
                "Please send me the list of games in this format:\n"
                "```\n"
                "Team A - Team B\n"
                "Team C - Team D\n"
                "Team E - Team F\n"
                "...\n"
                "```\n"
                "One game per line. You can use either `-` or `–` as separators.\n\n"
                "I'll show you a preview before creating it."
            )
        except discord.Forbidden:
            # Can't DM user
            pending_fixtures.pop(str(interaction.user.id), None)
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    async def _enter_results(self, interaction: discord.Interaction):
        """Initiate results entry via DM."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        # Check if results already entered
        existing_results = await self.db.get_results(fixture["id"])
        if existing_results:
            await interaction.response.send_message(
                "⚠️ Results already entered for this fixture!\n"
                "Use `/admin calculate` to calculate scores.",
                ephemeral=True,
            )
            return

        # Store pending request
        pending_results[str(interaction.user.id)] = fixture["id"]

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you instructions for entering results.",
            ephemeral=True,
        )

        # Build fixture list for DM
        lines = [
            f"**Week {fixture['week_number']} - Enter Results**",
            "",
            "Reply with the actual results in this format:",
            "```",
        ]
        for game in fixture["games"]:
            lines.append(f"{game} 2:0")
        lines.extend(
            [
                "```",
                "",
                "Add the actual score (e.g., 2:0 or 2-1) at the end of each line.",
            ]
        )

        # Send DM
        try:
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            pending_results.pop(str(interaction.user.id), None)
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    async def _calculate_scores(self, interaction: discord.Interaction):
        """Calculate scores for current fixture."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        results = await self.db.get_results(fixture["id"])
        if not results:
            await interaction.response.send_message(
                "❌ No results entered for this fixture!\nUse `/admin results` first.",
                ephemeral=True,
            )
            return

        predictions = await self.db.get_all_predictions(fixture["id"])

        if not predictions:
            await interaction.response.send_message(
                "❌ No predictions found for this fixture!", ephemeral=True
            )
            return

        # Calculate scores
        scores = []
        for pred in predictions:
            score_data = calculate_points(pred["predictions"], results, pred["is_late"])
            scores.append(
                {
                    "user_id": pred["user_id"],
                    "user_name": pred["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        # Sort by points
        scores.sort(key=lambda x: x["points"], reverse=True)

        # Save scores
        await self.db.save_scores(fixture["id"], scores)

        # Build announcement
        lines = [f"🏆 **Week {fixture['week_number']} Results**\n"]

        for i, score in enumerate(scores, 1):
            lines.append(
                f"{i}. **{score['user_name']}**: {score['points']} pts "
                f"({score['exact_scores']} exact, {score['correct_results']} correct)"
            )

        await interaction.response.send_message("\n".join(lines))

    async def _close_fixture(self, interaction: discord.Interaction):
        """Manually close current fixture."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message("❌ No active fixture found!", ephemeral=True)
            return

        # This is handled automatically when calculating scores
        # But admins can force close if needed
        await interaction.response.send_message(
            f"⚠️ Week {fixture['week_number']} will be closed when you run `/admin calculate`.",
            ephemeral=True,
        )


class FixtureConfirmView(discord.ui.View):
    """View for confirming fixture creation."""

    def __init__(
        self,
        db: Database,
        week_number: int,
        games: list[str],
        deadline: datetime,
        channel: discord.TextChannel,
        preview: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.week_number = week_number
        self.games = games
        self.deadline = deadline
        self.channel = channel
        self.preview = preview

    @discord.ui.button(label="✅ Create Fixture", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save fixture to database and announce."""
        await self.db.create_fixture(self.week_number, self.games, self.deadline)

        # Update DM
        await interaction.response.edit_message(
            content=f"✅ **Week {self.week_number} Fixture Created!**\n\n{self.preview}",
            view=None,
        )

        # Announce in channel
        try:
            await self.channel.send(
                f"📢 **Week {self.week_number} Fixture is now open!**\n\n{self.preview}"
            )
        except Exception:
            # If announcement fails, DM the admin
            await interaction.followup.send(
                "⚠️ Fixture created but I couldn't announce it in the channel. "
                "Please announce it manually.",
                ephemeral=True,
            )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel fixture creation."""
        await interaction.response.edit_message(content="❌ Fixture creation cancelled.", view=None)


class ResultsConfirmView(discord.ui.View):
    """View for confirming results entry."""

    def __init__(
        self,
        db: Database,
        fixture_id: int,
        results: list[str],
        preview: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.fixture_id = fixture_id
        self.results = results
        self.preview = preview

    @discord.ui.button(label="✅ Save Results", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save results to database."""
        await self.db.save_results(self.fixture_id, self.results)

        await interaction.response.edit_message(
            content=f"✅ **Results Saved!**\n\n{self.preview}\n\nUse `/admin calculate` to calculate scores.",
            view=None,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel results entry."""
        await interaction.response.edit_message(
            content="❌ Results entry cancelled. Use `/admin results` to try again.", view=None
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
