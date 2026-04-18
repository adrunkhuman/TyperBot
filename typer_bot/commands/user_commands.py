"""User-facing Discord commands."""

from __future__ import annotations

import logging
from contextlib import suppress

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database, SaveResult
from typer_bot.utils import (
    format_for_discord,
    format_predictions_preview,
    format_standings,
    get_admin_role_mention,
    is_admin,
    now,
    parse_prediction_lines,
)

SELECT_PAGE_SIZE = 25
BUTTON_PAGE_SIZE = 23
logger = logging.getLogger(__name__)


def _prediction_template(games: list[str]) -> str:
    return "\n".join(f"{game} 2:0" for game in games)


def _remaining_open_fixtures(
    open_fixtures: list[dict], completed_fixture_ids: set[int]
) -> list[dict]:
    return [fixture for fixture in open_fixtures if fixture["id"] not in completed_fixture_ids]


def _format_thread_prediction_message(
    fixture: dict,
    guild: discord.Guild | None,
    user_id: int,
    predictions: list[str],
    predicted_game_indexes: list[int],
    *,
    is_update: bool,
    is_late: bool,
    pending_partial_approval: bool,
) -> str:
    heading = "Updated prediction" if is_update else "Prediction"
    content = [f"**{heading} from <@{user_id}> · Week {fixture['week_number']}**", ""]
    for game_index, prediction in zip(predicted_game_indexes, predictions, strict=False):
        game = fixture["games"][game_index]
        content.append(f"{game_index + 1}. {game} **{prediction}**")

    status: str | None = None
    if pending_partial_approval:
        admin_role_mention = get_admin_role_mention(guild)
        status = "⏳ Late prediction awaiting admin review."
        if admin_role_mention:
            status += f" {admin_role_mention}"
    elif is_late:
        status = "⚠️ Late prediction."
    elif len(predicted_game_indexes) < len(fixture["games"]):
        status = "Partial prediction. Add the missing games before the deadline if you can."

    if status:
        content.extend(["", status])
    return "\n".join(content)


async def _get_prediction_thread(bot: commands.Bot, fixture: dict) -> discord.Thread | None:
    message_id = fixture.get("message_id")
    if not message_id:
        return None

    thread = bot.get_channel(int(message_id))
    if isinstance(thread, discord.Thread):
        return thread

    fetch_channel = getattr(bot, "fetch_channel", None)
    if fetch_channel is None:
        return None

    try:
        fetched = await fetch_channel(int(message_id))
    except discord.HTTPException:
        return None
    return fetched if isinstance(fetched, discord.Thread) else None


def _page_slice(items: list[dict], page: int, page_size: int) -> list[dict]:
    start = page * page_size
    end = start + page_size
    return items[start:end]


class PaginationButton(discord.ui.Button):
    def __init__(self, *, direction: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, PaginatedFixtureView):
            await interaction.response.send_message(
                "Selection flow is no longer available.", ephemeral=True
            )
            return
        if str(interaction.user.id) != view.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        view.page += self.direction
        view._refresh_items()
        await interaction.response.edit_message(view=view)


class PaginatedFixtureView(discord.ui.View):
    def __init__(self, owner_user_id: str, *, timeout: float = 3600):
        super().__init__(timeout=timeout)
        self.owner_user_id = owner_user_id
        self.page = 0
        self.fixtures: list[dict] = []
        self.page_size = SELECT_PAGE_SIZE

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.fixtures) + self.page_size - 1) // self.page_size)

    def _refresh_pagination_items(self) -> None:
        if self.total_pages == 1:
            return

        previous_button = PaginationButton(direction=-1, label="Previous")
        previous_button.disabled = self.page == 0
        next_button = PaginationButton(direction=1, label="Next")
        next_button.disabled = self.page >= self.total_pages - 1
        self.add_item(previous_button)
        self.add_item(next_button)


class ContinuePredictButton(discord.ui.Button):
    def __init__(self, fixture: dict):
        super().__init__(
            label=f"Predict Week {fixture['week_number']}",
            style=discord.ButtonStyle.primary,
        )
        self.fixture = fixture

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, ContinuePredictView):
            await interaction.response.send_message(
                "Prediction flow is no longer available.", ephemeral=True
            )
            return
        if str(interaction.user.id) != view.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        latest_fixture = await view.db.get_fixture_by_id(self.fixture["id"])
        if latest_fixture is None or latest_fixture["status"] != "open":
            await interaction.response.edit_message(
                content="That fixture is no longer open. Use `/predict` to refresh the list.",
                view=None,
            )
            return

        existing_prediction = await view.db.get_prediction(self.fixture["id"], view.owner_user_id)
        modal = PredictModal(
            view.bot,
            view.db,
            latest_fixture,
            interaction.user.display_name,
            view.completed_fixture_ids,
            existing_prediction,
        )
        await interaction.response.send_modal(modal)


class ContinuePredictView(PaginatedFixtureView):
    """Offer remaining open fixtures after a successful prediction save."""

    def __init__(
        self,
        db: Database,
        bot: commands.Bot,
        owner_user_id: str,
        remaining_fixtures: list[dict],
        completed_fixture_ids: set[int],
    ):
        super().__init__(owner_user_id, timeout=3600)
        self.bot = bot
        self.db = db
        self.completed_fixture_ids = completed_fixture_ids
        self.fixtures = remaining_fixtures
        self.page_size = BUTTON_PAGE_SIZE
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        for fixture in _page_slice(self.fixtures, self.page, self.page_size):
            self.add_item(ContinuePredictButton(fixture))
        self._refresh_pagination_items()


class FixtureSelect(discord.ui.Select):
    def __init__(self, fixtures: list[dict]):
        options = [
            discord.SelectOption(label=f"Week {fixture['week_number']}", value=str(fixture["id"]))
            for fixture in fixtures
        ]
        super().__init__(
            placeholder="Select a fixture", min_values=1, max_values=1, options=options
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, FixtureSelectView):
            await interaction.response.send_message(
                "Fixture picker is no longer available.", ephemeral=True
            )
            return
        if str(interaction.user.id) != view.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        fixture_id = int(self.values[0])
        fixture = await view.db.get_fixture_by_id(fixture_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.edit_message(
                content="That fixture is no longer open. Use `/predict` to refresh the list.",
                view=None,
            )
            return

        existing_prediction = await view.db.get_prediction(fixture_id, view.owner_user_id)
        modal = PredictModal(
            view.bot,
            view.db,
            fixture,
            interaction.user.display_name,
            view.completed_fixture_ids,
            existing_prediction,
        )
        await interaction.response.send_modal(modal)


class FixtureSelectView(PaginatedFixtureView):
    """Let a user choose which open fixture to predict first."""

    def __init__(
        self,
        db: Database,
        bot: commands.Bot,
        owner_user_id: str,
        fixtures: list[dict],
        completed_fixture_ids: set[int] | None = None,
    ):
        super().__init__(owner_user_id, timeout=3600)
        self.bot = bot
        self.db = db
        self.fixtures = fixtures
        self.page_size = SELECT_PAGE_SIZE
        self.completed_fixture_ids = completed_fixture_ids or set()
        self._refresh_items()

    def _refresh_items(self) -> None:
        self.clear_items()
        self.add_item(FixtureSelect(_page_slice(self.fixtures, self.page, self.page_size)))
        self._refresh_pagination_items()


class PredictModal(discord.ui.Modal):
    """Collect predictions for one fixture through a modal instead of DMs.

    Submitting can overwrite an existing open prediction, late submissions are
    accepted but marked late, and successful saves can chain into a follow-up
    picker for any remaining open fixtures.
    """

    def __init__(
        self,
        bot: commands.Bot,
        db: Database,
        fixture: dict,
        user_name: str,
        completed_fixture_ids: set[int] | None = None,
        existing_prediction: dict | None = None,
    ):
        super().__init__(title=f"Predict Week {fixture['week_number']}")
        self.bot = bot
        self.db = db
        self.fixture = fixture
        self.user_name = user_name
        self.completed_fixture_ids = completed_fixture_ids or set()
        default_value = (
            "\n".join(
                f"{fixture['games'][game_index]} {prediction}"
                for game_index, prediction in zip(
                    existing_prediction["predicted_game_indexes"],
                    existing_prediction["predictions"],
                    strict=False,
                )
            )
            if existing_prediction
            else _prediction_template(fixture["games"])
        )
        self.predictions_input = discord.ui.TextInput(
            label="Predictions",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default=default_value,
            required=True,
            max_length=4000,
        )
        self.add_item(self.predictions_input)

    async def on_submit(self, interaction: discord.Interaction):
        fixture = await self.db.get_fixture_by_id(self.fixture["id"])
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "This fixture is no longer open. Use `/predict` to refresh the list.",
                ephemeral=True,
            )
            return

        predictions, predicted_game_indexes, errors = parse_prediction_lines(
            self.predictions_input.value,
            fixture["games"],
            allow_partial=True,
        )
        if errors:
            await interaction.response.send_message("\n".join(errors), ephemeral=True)
            return

        thread = await _get_prediction_thread(self.bot, fixture)
        if thread is None:
            await interaction.response.send_message(
                "This fixture does not have a usable prediction thread yet. Ask an admin to restore it before using `/predict`.",
                ephemeral=True,
            )
            return

        is_late = now() > fixture["deadline"]
        existing_prediction = await self.db.get_prediction(fixture["id"], str(interaction.user.id))
        is_partial = len(predicted_game_indexes) < len(fixture["games"])
        pending_partial_approval = is_late and is_partial
        public_message = None
        try:
            public_message = await thread.send(
                _format_thread_prediction_message(
                    fixture,
                    interaction.guild,
                    interaction.user.id,
                    predictions,
                    predicted_game_indexes,
                    is_update=existing_prediction is not None,
                    is_late=is_late,
                    pending_partial_approval=pending_partial_approval,
                )
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "This fixture does not have a usable prediction thread yet. Ask an admin to restore it before using `/predict`.",
                ephemeral=True,
            )
            return

        try:
            result = await self.db.save_prediction_guarded(
                fixture["id"],
                str(interaction.user.id),
                self.user_name,
                predictions,
                is_late,
                predicted_game_indexes=predicted_game_indexes,
                pending_partial_approval=pending_partial_approval,
            )
        except Exception:
            logger.exception(
                "Failed to save modal prediction",
                extra={
                    "fixture_id": fixture["id"],
                    "user_id": str(interaction.user.id),
                    "source": "predict_modal",
                },
            )
            if public_message is not None:
                with suppress(Exception):
                    await public_message.delete()
            await interaction.response.send_message(
                "Something went wrong while saving your prediction. Please try again.",
                ephemeral=True,
            )
            return
        if result == SaveResult.FIXTURE_CLOSED:
            if public_message is not None:
                with suppress(Exception):
                    await public_message.delete()
            await interaction.response.send_message(
                "This fixture closed before your prediction could be saved. Use `/predict` to refresh the list.",
                ephemeral=True,
            )
            return

        preview_games = [fixture["games"][index] for index in predicted_game_indexes]
        content = format_predictions_preview(preview_games, predictions)
        deadline_str = format_for_discord(fixture["deadline"], "F")
        relative_str = format_for_discord(fixture["deadline"], "R")
        content += f"\n\n**Posted publicly in the fixture thread.**\n**Deadline:** {deadline_str} ({relative_str})"
        if pending_partial_approval:
            content += (
                "\n\n⏳ **Late prediction awaiting admin review:** your predicted games will only count "
                "if an admin approves this late submission with missing games."
            )
        elif is_late:
            content += "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."
        elif is_partial:
            content += (
                "\n\nℹ️ **Partial prediction saved:** any missing games will count as no prediction. "
                "If the deadline has not passed yet, use `/predict` again to fill the rest."
            )

        completed_fixture_ids = set(self.completed_fixture_ids)
        completed_fixture_ids.add(fixture["id"])
        open_fixtures = await self.db.get_open_fixtures()
        remaining_fixtures = _remaining_open_fixtures(open_fixtures, completed_fixture_ids)
        if remaining_fixtures:
            view = ContinuePredictView(
                self.db,
                self.bot,
                str(interaction.user.id),
                remaining_fixtures,
                completed_fixture_ids,
            )
            await interaction.response.send_message(
                f"{content}\n\nPredict another open fixture?",
                ephemeral=True,
                view=view,
            )
            return

        await interaction.response.send_message(
            f"{content}\n\nYou're done for now.", ephemeral=True
        )


class UserCommands(commands.Cog):
    """Commands for regular users."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # TyperBot injects these attrs; discord.py typing cannot see them.
        self.db: Database = bot.db  # type: ignore

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

    @app_commands.command(name="predict", description="Submit your predictions for open fixtures")
    @app_commands.checks.cooldown(1, 1.0)
    async def predict(self, interaction: discord.Interaction):
        """Open a modal to submit predictions for open fixtures."""
        open_fixtures = await self.db.get_open_fixtures()
        if not open_fixtures:
            await interaction.response.send_message(
                "❌ No active fixture found! Ask an admin to create one.", ephemeral=True
            )
            return

        existing_prediction = None
        if len(open_fixtures) == 1:
            fixture = open_fixtures[0]
            existing_prediction = await self.db.get_prediction(
                fixture["id"], str(interaction.user.id)
            )
            modal = PredictModal(
                self.bot,
                self.db,
                fixture,
                interaction.user.display_name,
                existing_prediction=existing_prediction,
            )
            await interaction.response.send_modal(modal)
            return

        view = FixtureSelectView(self.db, self.bot, str(interaction.user.id), open_fixtures)
        await interaction.response.send_message(
            "Multiple fixtures are open. Choose which week you want to predict first.",
            ephemeral=True,
            view=view,
        )

    @app_commands.command(name="help", description="Show help information")
    async def help(self, interaction: discord.Interaction):
        """Display help for users and admins."""
        is_admin_user = is_admin(interaction)

        user_help = """## 📖 User Commands

**For Players:**
• `/predict` - Fill predictions in a modal, then post them publicly to the fixture thread
• `/fixtures` - View all open fixtures
• `/standings` - See overall leaderboard
• `/mypredictions` - Check your submitted predictions for open fixtures

**How to Predict:**

**Reply in the fixture thread**
1. Open the fixture thread
2. Reply with your predictions:
   ```
   Team A - Team B 2:0
   Team C - Team D 1:1
   ...
   ```
3. Bot reacts ✅ when saved
4. Late submissions with missing games react ⏳ and wait for admin review

**Or use `/predict`**
1. Type `/predict` in the channel
2. If multiple fixtures are open, choose the week from the picker
3. Enter your predictions in the modal
4. Submit to post them in the fixture thread
5. Use the buttons to continue to other open fixtures

**Partial predictions:**
- You can leave some games out, but before the deadline you should still try to fill the whole fixture
- Each partial line must name the game it applies to
- Missing games count as no prediction
- Late submissions with missing games wait for admin review before they count

**Scoring:**
• Exact score: 3 points
• Correct result (win/loss/draw): 1 point
• Wrong: 0 points
• Late full predictions: 0 points
• Late predictions with missing games: pending admin review

**Input formats:** Use `2:0`, `2-0`, or `2 : 0`

**To change a prediction:** Use `/predict` again. The bot will post an updated prediction in the fixture thread."""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
• `/admin panel` - Open the main admin surface

**Admin workflow:**
- run `/admin panel` once
- use the panel buttons and selectors for admin actions

**Inside the panel you can:**
- create fixtures
- delete fixtures
- jump to an older open week that is not shown in the quick list
- enter or correct results
- calculate scores
- re-post the latest completed results with optional mentions
- replace predictions
- toggle late waivers
- review, approve, or reject late predictions submitted with missing games
- inspect overflow prediction lists when a fixture has more than 25 users

**Custom Deadline Format:**
• `2024-02-15 18:00`
• `15.02.2024 18:00`
• `15/02/2024 18:00`

Use these directly in Discord."""

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
            for game_index, pred in zip(
                prediction["predicted_game_indexes"], prediction["predictions"], strict=False
            ):
                lines.append(f"{game_index + 1}. {fixture['games'][game_index]} **{pred}**")

            late_status = "✅ On time"
            if prediction["pending_partial_approval"]:
                late_status = "⏳ Late prediction awaiting admin review"
            elif prediction["is_late"]:
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
            for game_index, pred in zip(
                prediction["predicted_game_indexes"], prediction["predictions"], strict=False
            ):
                lines.append(f"{game_index + 1}. {fixture['games'][game_index]} **{pred}**")

            late_status = "✅ On time"
            if prediction["pending_partial_approval"]:
                late_status = "⏳ Late prediction awaiting admin review"
            elif prediction["is_late"]:
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
