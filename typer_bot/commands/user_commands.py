"""User-facing Discord commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from typer_bot.database import Database, SaveResult
from typer_bot.services import WorkflowStateStore
from typer_bot.utils import (
    format_for_discord,
    format_predictions_preview,
    format_standings,
    is_admin,
    now,
    parse_line_predictions,
)

SELECT_PAGE_SIZE = 25
BUTTON_PAGE_SIZE = 23


def _prediction_template(games: list[str]) -> str:
    return "\n".join(f"{game} 2:0" for game in games)


def _remaining_open_fixtures(
    open_fixtures: list[dict], completed_fixture_ids: set[int]
) -> list[dict]:
    return [fixture for fixture in open_fixtures if fixture["id"] not in completed_fixture_ids]


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
        owner_user_id: str,
        remaining_fixtures: list[dict],
        completed_fixture_ids: set[int],
    ):
        super().__init__(owner_user_id, timeout=3600)
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
        owner_user_id: str,
        fixtures: list[dict],
        completed_fixture_ids: set[int] | None = None,
    ):
        super().__init__(owner_user_id, timeout=3600)
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
        db: Database,
        fixture: dict,
        user_name: str,
        completed_fixture_ids: set[int] | None = None,
        existing_prediction: dict | None = None,
    ):
        super().__init__(title=f"Predict Week {fixture['week_number']}")
        self.db = db
        self.fixture = fixture
        self.user_name = user_name
        self.completed_fixture_ids = completed_fixture_ids or set()
        default_value = (
            "\n".join(
                f"{game} {prediction}"
                for game, prediction in zip(
                    fixture["games"], existing_prediction["predictions"], strict=False
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

        predictions, errors = parse_line_predictions(self.predictions_input.value, fixture["games"])
        if errors:
            await interaction.response.send_message("\n".join(errors), ephemeral=True)
            return

        is_late = now() > fixture["deadline"]
        try:
            result = await self.db.save_prediction_guarded(
                fixture["id"],
                str(interaction.user.id),
                self.user_name,
                predictions,
                is_late,
            )
        except Exception:
            await interaction.response.send_message(
                "Something went wrong while saving your prediction. Please try again.",
                ephemeral=True,
            )
            return
        if result == SaveResult.FIXTURE_CLOSED:
            await interaction.response.send_message(
                "This fixture closed before your prediction could be saved. Use `/predict` to refresh the list.",
                ephemeral=True,
            )
            return

        content = format_predictions_preview(fixture["games"], predictions)
        deadline_str = format_for_discord(fixture["deadline"], "F")
        relative_str = format_for_discord(fixture["deadline"], "R")
        content += f"\n\n**Deadline:** {deadline_str} ({relative_str})"
        if is_late:
            content += "\n\n⚠️ **Late prediction!** You will receive 0 points for this round."

        completed_fixture_ids = set(self.completed_fixture_ids)
        completed_fixture_ids.add(fixture["id"])
        open_fixtures = await self.db.get_open_fixtures()
        remaining_fixtures = _remaining_open_fixtures(open_fixtures, completed_fixture_ids)
        if remaining_fixtures:
            view = ContinuePredictView(
                self.db,
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
        self.workflow_state: WorkflowStateStore = bot.workflow_state  # type: ignore[attr-defined]

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
                self.db,
                fixture,
                interaction.user.display_name,
                existing_prediction=existing_prediction,
            )
            await interaction.response.send_modal(modal)
            return

        view = FixtureSelectView(self.db, str(interaction.user.id), open_fixtures)
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
• `/predict` - Submit predictions via modal
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

**Method 2: /predict Modal**
1. Type `/predict` in the channel
2. If multiple fixtures are open, choose the week from the picker
3. Enter your predictions in the modal
4. Save, then use the buttons to continue to other open fixtures

**Scoring:**
• Exact score: 3 points
• Correct result (win/loss/draw): 1 point
• Wrong: 0 points
• Late predictions: 0 points (submit before deadline!)

**Input formats:** Use `2:0`, `2-0`, or `2 : 0`

**To change a prediction:** Use `/predict` again. Thread posts do not edit existing picks."""

        admin_help = """\n\n## 🔧 Admin Commands

**For Admins:**
• `/admin panel` - Open the admin hub for fixture deletion, overrides, waivers, and result correction
**Fixture Management:**
• `/admin fixture create` - Create new fixture (modal + confirm, auto-creates thread)
• `/admin fixture delete [week]` - Delete an open fixture

**Results Management:**
• `/admin results enter [week]` - Enter actual scores (modal + confirm)
• `/admin results calculate [week]` - Calculate and post scores
• `/admin results post` - Re-post results with optional mentions

**Admin Workflow:**
1. **Create Fixture:**
   - `/admin fixture create`
   - Modal opens in Discord
   - Enter game list and optional deadline
   - Review preview and confirm
   - Bot auto-creates thread for predictions

2. **Enter Results:**
   - `/admin results enter` (add `week:` if multiple fixtures are open)
   - Modal opens in Discord
   - Enter actual scores:
      ```
      Team A - Team B 1:0
      Team C - Team D 2:2
      ...
      ```
   - Review preview and confirm

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

⚠️ No DMs are required for `/predict`, `/admin fixture create`, or `/admin results enter`."""

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
