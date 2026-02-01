"""Admin Discord commands."""

import logging
from datetime import datetime, timedelta

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.utils import (
    APP_TZ,
    calculate_points,
    format_for_discord,
    now,
    parse_line_predictions,
)
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

# user_id -> {"channel_id": int, "guild_id": int, "games": list, "deadline": datetime, "step": str}
pending_fixtures = {}

# user_id -> {"fixture_id": int, "guild_id": int}
pending_results = {}

# Rate limiting: user_id -> timestamp
_calculate_cooldowns = {}

# Security limits
MAX_MESSAGE_LENGTH = 5000
MAX_GAMES = 100
CALCULATE_COOLDOWN = 30.0

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    """Commands for admins."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    def is_admin(self, member: discord.Member) -> bool:
        """Check if member has admin role (case-insensitive)."""
        admin_roles = {"admin", "typer-admin"}
        return any(role.name.lower() in admin_roles for role in member.roles)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs from admins."""
        if message.author.bot or message.guild is not None:
            return

        user_id = str(message.author.id)
        logger.info(
            f"[ADMIN] on_message from user {user_id}, pending_fixtures={user_id in pending_fixtures}, pending_results={user_id in pending_results}"
        )

        if user_id in pending_fixtures:
            await self._handle_fixture_dm(message, user_id)
            return

        if user_id in pending_results:
            await self._handle_results_dm(message, user_id)
            return

    async def _handle_fixture_dm(self, message: discord.Message, user_id: str):
        """Handle fixture creation DM."""
        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return

        # Re-verify admin status before processing
        state = pending_fixtures[user_id]
        guild_id = state.get("guild_id")
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(int(user_id))
                if not member or not self.is_admin(member):
                    pending_fixtures.pop(user_id, None)
                    await message.author.send("❌ Permission denied or session expired.")
                    return

        step = state.get("step", "games")

        if step == "games":
            games = [line.strip() for line in message.content.strip().split("\n") if line.strip()]

            if len(games) > MAX_GAMES:
                await message.author.send(f"❌ Too many games! (max {MAX_GAMES})")
                return

            if len(games) < 1:
                await message.author.send(
                    "❌ No games provided! Please send the fixture list again."
                )
                return

            state["games"] = games
            state["step"] = "deadline"

            current_time = now()
            days_until_friday = (4 - current_time.weekday()) % 7
            if days_until_friday == 0 and current_time.hour >= 18:
                days_until_friday = 7
            default_deadline = current_time + timedelta(days=days_until_friday)
            default_deadline = default_deadline.replace(hour=18, minute=0, second=0, microsecond=0)
            state["default_deadline"] = default_deadline

            default_str = format_for_discord(default_deadline, "F")
            relative_str = format_for_discord(default_deadline, "R")
            view = DeadlineChoiceView(self.db, user_id)
            await message.author.send(
                f"**Choose Deadline**\n\n"
                f"Default: **{default_str}** ({relative_str})\n\n"
                f"Or type a custom deadline in format: `YYYY-MM-DD HH:MM`\n"
                f"Example: `{current_time.strftime('%Y-%m-%d')} 20:00` for today at 8 PM",
                view=view,
            )

        elif step == "deadline":
            try:
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
                        "❌ Invalid date format. Please use one of these formats:\n"
                        "```\n"
                        "2024-02-15 18:00\n"
                        "15.02.2024 18:00\n"
                        "15/02/2024 18:00\n"
                        "```\n"
                        "Or click the 'Use Default' button above."
                    )
                    return

                state["deadline"] = deadline
                await self._show_fixture_preview(message.author, user_id)

            except Exception as e:
                logger.error(f"Error parsing date: {e}", exc_info=True)
                await message.author.send("❌ Error parsing date. Please try again.")

    async def _show_fixture_preview(self, user: discord.User, user_id: str):
        """Show fixture preview with confirmation."""
        state = pending_fixtures[user_id]
        games = state["games"]
        deadline = state.get("deadline", state["default_deadline"])
        channel = self.bot.get_channel(state["channel_id"])

        if not channel:
            await user.send("❌ Error: Could not find the original channel.")
            pending_fixtures.pop(user_id, None)
            return

        current = await self.db.get_current_fixture()
        week_number = 1 if not current else current["week_number"] + 1
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
            self.db, user_id, week_number, games, deadline, channel, preview_text + warning
        )

        await user.send(f"{preview_text}{warning}\n\nCreate this fixture?", view=view)

    async def _handle_results_dm(self, message: discord.Message, user_id: str):
        """Handle results entry DM."""
        import logging

        logger = logging.getLogger(__name__)

        if len(message.content) > MAX_MESSAGE_LENGTH:
            await message.author.send(f"❌ Message too long! (max {MAX_MESSAGE_LENGTH} characters)")
            return

        logger.info(f"Processing results DM from user {user_id}")

        result_data = pending_results.pop(user_id)
        fixture_id = result_data["fixture_id"]
        guild_id = result_data.get("guild_id")

        # Re-verify admin status
        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(int(user_id))
                if not member or not self.is_admin(member):
                    pending_results[user_id] = result_data
                    await message.author.send("❌ Permission denied or session expired.")
                    return
        fixture = await self.db.get_fixture_by_id(fixture_id)

        if not fixture:
            await message.author.send("❌ Error: Fixture no longer exists.")
            return

        processing_msg = await message.author.send("⏳ Processing your results...")

        try:
            content = message.content.strip()
            lines = content.split("\n")

            logger.info(f"Received {len(lines)} lines, expected {len(fixture['games'])}")

            results, errors = parse_line_predictions(lines, fixture["games"])

            if results:
                logger.info(f"Successfully parsed {len(results)} scores")

            if errors:
                error_msg = "\n".join(errors)
                logger.warning(f"Validation errors: {error_msg}")
                await processing_msg.edit(
                    content=f"❌ **Invalid results:**\n```{error_msg}```\n\n"
                    f"Please send the results again in this format:\n"
                    f"```\n{fixture['games'][0]} 2:0\n{fixture['games'][1]} 1:1\n...\n```"
                )
                pending_results[user_id] = result_data
                return

            preview_lines = [f"**Week {fixture['week_number']} Results Preview**\n"]
            for i, (game, result) in enumerate(zip(fixture["games"], results, strict=False), 1):
                preview_lines.append(f"{i}. {game} **{result}**")

            preview_text = "\n".join(preview_lines)

            logger.info("Results parsed successfully, showing preview")
            view = ResultsConfirmView(self.db, user_id, fixture_id, results, preview_text)
            await processing_msg.edit(content=f"{preview_text}\n\nSave these results?", view=view)

        except Exception as e:
            logger.error(f"Error processing results: {e}", exc_info=True)
            await processing_msg.edit(content="❌ Error processing results. Please try again.")
            pending_results[user_id] = result_data

    @app_commands.command(name="admin", description="Admin commands for managing fixtures")
    @app_commands.describe(action="Action to perform")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="fixture", value="fixture"),
            app_commands.Choice(name="results", value="results"),
            app_commands.Choice(name="calculate", value="calculate"),
            app_commands.Choice(name="delete", value="delete"),
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
        elif action.value == "delete":
            await self._delete_fixture(interaction)

    async def _create_fixture(self, interaction: discord.Interaction):
        """Initiate fixture creation via DM."""
        pending_fixtures[str(interaction.user.id)] = {
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id,
            "step": "games",
        }

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you instructions for creating the fixture.",
            ephemeral=True,
        )

        try:
            await interaction.user.send(
                "**Create New Fixture**\n\n"
                "Step 1/2: Send me the list of games in this format:\n"
                "```\n"
                "Team A - Team B\n"
                "Team C - Team D\n"
                "Team E - Team F\n"
                "...\n"
                "```\n"
                "One game per line."
            )
        except discord.Forbidden:
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

        existing_results = await self.db.get_results(fixture["id"])
        if existing_results:
            await interaction.response.send_message(
                "⚠️ Results already entered for this fixture!\n"
                "Use `/admin calculate` to calculate scores.",
                ephemeral=True,
            )
            return

        pending_results[str(interaction.user.id)] = {
            "fixture_id": fixture["id"],
            "guild_id": interaction.guild_id,
        }

        await interaction.response.send_message(
            "📩 Check your DMs! I've sent you instructions for entering results.",
            ephemeral=True,
        )

        try:
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
            await interaction.user.send("\n".join(lines))
        except discord.Forbidden:
            pending_results.pop(str(interaction.user.id), None)
            await interaction.followup.send(
                "❌ I can't send you DMs. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    async def _calculate_scores(self, interaction: discord.Interaction):
        """Calculate scores for current fixture."""
        user_id = str(interaction.user.id)
        current_time = now().timestamp()

        if user_id in _calculate_cooldowns:
            last_used = _calculate_cooldowns[user_id]
            if current_time - last_used < CALCULATE_COOLDOWN:
                remaining = CALCULATE_COOLDOWN - (current_time - last_used)
                await interaction.response.send_message(
                    f"⏳ Please wait {remaining:.1f}s before calculating again.",
                    ephemeral=True,
                )
                return

        _calculate_cooldowns[user_id] = current_time

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

        scores.sort(key=lambda x: x["points"], reverse=True)
        await self.db.save_scores(fixture["id"], scores)
        try:
            await self.bot.loop.run_in_executor(
                None, lambda: create_backup(self.db.db_path, BACKUP_DIR)
            )
            await self.bot.loop.run_in_executor(
                None, lambda: cleanup_old_backups(BACKUP_DIR, keep=10)
            )
        except Exception as e:
            logger.warning(f"Backup failed but calculation succeeded: {e}")
        lines = [f"🏆 **Week {fixture['week_number']} Results**\n"]
        for i, score in enumerate(scores, 1):
            lines.append(
                f"{i}. **{score['user_name']}**: {score['points']} pts "
                f"({score['exact_scores']} exact, {score['correct_results']} correct)"
            )

        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="health", description="Check bot health status")
    async def health_check(self, interaction: discord.Interaction):
        if not self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
            return

        status = []

        try:
            async with aiosqlite.connect(self.db.db_path) as db:
                await db.execute("SELECT 1")
            status.append("✅ Database: Connected")
        except Exception as e:
            status.append(f"❌ Database: {e}")

        latency = self.bot.latency * 1000
        status.append(f"ℹ️ Discord API latency: {latency:.0f}ms")

        await interaction.response.send_message("\n".join(status), ephemeral=True)

    async def _delete_fixture(self, interaction: discord.Interaction):
        """Delete current fixture."""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
            return

        fixture = await self.db.get_current_fixture()
        if not fixture:
            await interaction.response.send_message(
                "❌ No active fixture found to delete!", ephemeral=True
            )
            return

        view = DeleteConfirmView(
            self.db, str(interaction.user.id), fixture["id"], fixture["week_number"]
        )

        lines = [f"**⚠️ Delete Week {fixture['week_number']}?**\n"]
        for i, game in enumerate(fixture["games"], 1):
            lines.append(f"{i}. {game}")

        await interaction.response.send_message(
            "\n".join(lines)
            + "\n\nThis will delete the fixture and all associated predictions. Are you sure?",
            view=view,
            ephemeral=True,
        )


class DeadlineChoiceView(discord.ui.View):
    """View for choosing deadline type."""

    def __init__(self, db: Database, user_id: str):
        super().__init__(timeout=120)
        self.db = db
        self.user_id = user_id

    async def on_timeout(self):
        pending_fixtures.pop(self.user_id, None)

    @discord.ui.button(label="✅ Use Default (Friday 18:00)", style=discord.ButtonStyle.primary)
    async def use_default(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Use default deadline."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "❌ This button is not for you!", ephemeral=True
            )
            return

        state = pending_fixtures.get(self.user_id)
        if not state:
            await interaction.response.edit_message(
                content="❌ Session expired. Please start over with `/admin fixture`.", view=None
            )
            return

        state["deadline"] = state["default_deadline"]

        await interaction.response.edit_message(
            content="✅ Using default deadline. Showing preview...", view=None
        )

        cog = interaction.client.get_cog("AdminCommands")
        if cog:
            await cog._show_fixture_preview(interaction.user, self.user_id)


class FixtureConfirmView(discord.ui.View):
    """View for confirming fixture creation."""

    def __init__(
        self,
        db: Database,
        user_id: str,
        week_number: int,
        games: list[str],
        deadline: datetime,
        channel: discord.TextChannel,
        preview: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.user_id = user_id
        self.week_number = week_number
        self.games = games
        self.deadline = deadline
        self.channel = channel
        self.preview = preview

    async def on_timeout(self):
        pending_fixtures.pop(self.user_id, None)

    @discord.ui.button(label="✅ Create Fixture", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save fixture to database and announce."""
        pending_fixtures.pop(self.user_id, None)

        await self.db.create_fixture(self.week_number, self.games, self.deadline)

        await interaction.response.edit_message(
            content=f"✅ **Week {self.week_number} Fixture Created!**\n\n{self.preview}",
            view=None,
        )

        try:
            await self.channel.send(
                f"📢 **Week {self.week_number} Fixture is now open!**\n\n{self.preview}"
            )
        except Exception:
            await interaction.followup.send(
                "⚠️ Fixture created but I couldn't announce it in the channel. "
                "Please announce it manually.",
                ephemeral=True,
            )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel fixture creation."""
        pending_fixtures.pop(self.user_id, None)

        await interaction.response.edit_message(content="❌ Fixture creation cancelled.", view=None)


class ResultsConfirmView(discord.ui.View):
    """View for confirming results entry."""

    def __init__(
        self,
        db: Database,
        user_id: str,
        fixture_id: int,
        results: list[str],
        preview: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.results = results
        self.preview = preview

    async def on_timeout(self):
        pending_results.pop(self.user_id, None)

    @discord.ui.button(label="✅ Save Results", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Save results to database."""
        pending_results.pop(self.user_id, None)

        await self.db.save_results(self.fixture_id, self.results)

        await interaction.response.edit_message(
            content=f"✅ **Results Saved!**\n\n{self.preview}\n\nUse `/admin calculate` to calculate scores.",
            view=None,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel results entry."""
        pending_results.pop(self.user_id, None)

        await interaction.response.edit_message(
            content="❌ Results entry cancelled. Use `/admin results` to try again.", view=None
        )


class DeleteConfirmView(discord.ui.View):
    """View for confirming fixture deletion."""

    def __init__(self, db: Database, user_id: str, fixture_id: int, week_number: int):
        super().__init__(timeout=60)
        self.db = db
        self.user_id = user_id
        self.fixture_id = fixture_id
        self.week_number = week_number

    async def on_timeout(self):
        pass

    @discord.ui.button(label="✅ Yes, Delete", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Delete fixture from database."""
        cog = interaction.client.get_cog("AdminCommands")
        if not cog or not cog.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to do this!", ephemeral=True
            )
            return

        await self.db.delete_fixture(self.fixture_id)

        await interaction.response.edit_message(
            content=f"✅ **Week {self.week_number} Fixture Deleted!**", view=None
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel deletion."""
        cog = interaction.client.get_cog("AdminCommands")
        if not cog or not cog.is_admin(interaction.user):
            await interaction.response.send_message(
                "❌ You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="❌ Deletion cancelled. The fixture is still active.", view=None
        )


async def setup(bot: commands.Bot):
    """Add cog to bot."""
    await bot.add_cog(AdminCommands(bot))
