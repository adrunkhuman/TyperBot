"""Admin Discord commands."""

from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ..database import Database
from ..utils import calculate_points


class AdminCommands(commands.Cog):
    """Commands for admins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    def is_admin(self, member: discord.Member) -> bool:
        """Check if member has admin role."""
        admin_roles = {"Admin", "typer-admin"}
        return any(role.name in admin_roles for role in member.roles)

    @app_commands.command(name="admin", description="Admin commands for managing fixtures")
    @app_commands.describe(
        action="Action to perform",
        data="Game data or results"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="fixture", value="fixture"),
        app_commands.Choice(name="results", value="results"),
        app_commands.Choice(name="calculate", value="calculate"),
        app_commands.Choice(name="close", value="close"),
    ])
    async def admin(
        self, 
        interaction: discord.Interaction, 
        action: app_commands.Choice[str],
        data: str = None
    ):
        """Admin command hub."""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to use admin commands.",
                ephemeral=True
            )
            return

        if action.value == "fixture":
            await self._create_fixture(interaction, data)
        elif action.value == "results":
            await self._enter_results(interaction, data)
        elif action.value == "calculate":
            await self._calculate_scores(interaction)
        elif action.value == "close":
            await self._close_fixture(interaction)

    async def _create_fixture(self, interaction: discord.Interaction, data: str):
        """Create a new fixture."""
        if not data:
            await interaction.response.send_message(
                "❌ Please provide fixture data. Format:\n"
                "```\n/admin fixture Lech - Legia\nPogoń - Arka\n...```",
                ephemeral=True
            )
            return

        # Parse games (one per line)
        games = [line.strip() for line in data.strip().split("\n") if line.strip()]
        
        if len(games) < 1:
            await interaction.response.send_message(
                "❌ No games provided!",
                ephemeral=True
            )
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

        fixture_id = await self.db.create_fixture(week_number, games, deadline)

        # Build display
        lines = [f"✅ **Week {week_number} Fixture Created**\n"]
        for i, game in enumerate(games, 1):
            lines.append(f"{i}. {game}")
        
        deadline_str = deadline.strftime("%A, %B %d at %H:%M")
        lines.append(f"\n**Deadline:** {deadline_str}")
        lines.append(f"**Fixture ID:** {fixture_id}")

        await interaction.response.send_message("\n".join(lines))

    async def _enter_results(self, interaction: discord.Interaction, data: str):
        """Enter results for current fixture."""
        if not data:
            await interaction.response.send_message(
                "❌ Please provide results. Format:\n"
                "```\n/admin results 2-1\n1-0\n3-3\n...```",
                ephemeral=True
            )
            return

        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found!",
                ephemeral=True
            )
            return

        # Parse results (one per line)
        results = [line.strip() for line in data.strip().split("\n") if line.strip()]
        expected_count = len(fixture["games"])

        if len(results) != expected_count:
            await interaction.response.send_message(
                f"❌ Expected {expected_count} results, got {len(results)}",
                ephemeral=True
            )
            return

        # Validate format
        from ..utils import parse_predictions
        parsed, errors = parse_predictions(" ".join(results), expected_count)
        
        if errors:
            await interaction.response.send_message(
                f"❌ Invalid results format:\n```\n{chr(10).join(errors)}```",
                ephemeral=True
            )
            return

        # Save results
        await self.db.save_results(fixture["id"], parsed)

        # Build display
        lines = [f"✅ **Results saved for Week {fixture['week_number']}**\n"]
        for i, (game, result) in enumerate(zip(fixture["games"], parsed, strict=False), 1):
            lines.append(f"{i}. {game}: **{result}**")

        lines.append("\nUse `/admin calculate` to calculate scores.")

        await interaction.response.send_message("\n".join(lines))

    async def _calculate_scores(self, interaction: discord.Interaction):
        """Calculate scores for current fixture."""
        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found!",
                ephemeral=True
            )
            return

        results = await self.db.get_results(fixture["id"])
        if not results:
            await interaction.response.send_message(
                "❌ No results entered for this fixture!\n"
                "Use `/admin results` first.",
                ephemeral=True
            )
            return

        predictions = await self.db.get_all_predictions(fixture["id"])
        
        if not predictions:
            await interaction.response.send_message(
                "❌ No predictions found for this fixture!",
                ephemeral=True
            )
            return

        # Calculate scores
        scores = []
        for pred in predictions:
            score_data = calculate_points(
                pred["predictions"],
                results,
                pred["is_late"]
            )
            scores.append({
                "user_id": pred["user_id"],
                "user_name": pred["user_name"],
                "points": score_data["points"],
                "exact_scores": score_data["exact_scores"],
                "correct_results": score_data["correct_results"]
            })

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
            await interaction.response.send_message(
                "❌ No active fixture found!",
                ephemeral=True
            )
            return

        # This is handled automatically when calculating scores
        # But admins can force close if needed
        await interaction.response.send_message(
            f"⚠️ Week {fixture['week_number']} will be closed when you run `/admin calculate`.",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))