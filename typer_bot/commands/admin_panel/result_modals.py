"""Result entry modals for the admin panel."""

from __future__ import annotations

import discord

from typer_bot.database import Database
from typer_bot.utils import get_admin_permission_error, parse_line_predictions

from .base import _format_prediction_line


def _build_results_preview(week_number: int, games: list[str], results: list[str]) -> str:
    lines = [f"**Week {week_number} Results Preview**", ""]
    lines.extend(
        _format_prediction_line(index, game, result)
        for index, (game, result) in enumerate(zip(games, results, strict=False), 1)
    )
    return "\n".join(lines)


class EnterResultsConfirmView(discord.ui.View):
    """Confirm or cancel modal-based results entry.

    Confirm is owner-only, rechecks admin permission, and persists the parsed
    results only after the preview step succeeds.
    """

    def __init__(
        self,
        db: Database,
        fixture: dict,
        results: list[str],
        preview: str,
        owner_user_id: str,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.fixture = fixture
        self.results = results
        self.preview = preview
        self.owner_user_id = owner_user_id

    @discord.ui.button(label="Save Results", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        permission_error = await get_admin_permission_error(interaction, self.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        try:
            await self.db.save_results(self.fixture["id"], self.results)
        except ValueError as exc:
            await interaction.response.edit_message(
                content=f"**Cannot save results:** {exc}", view=None
            )
            return

        await interaction.response.edit_message(
            content=f"**Results Saved!**\n\n{self.preview}\n\nUse the Calculate Scores button in `/admin panel` to post standings now. Use Re-post Results later only if you need to post them again with optional mentions.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Results entry cancelled. Use the Enter Results button in `/admin panel` to try again.",
            view=None,
        )


class EnterResultsModal(discord.ui.Modal):
    """Collect results for an open fixture in one modal.

    Parse failures are returned ephemerally, and nothing is persisted until the
    follow-up confirm view succeeds.
    """

    def __init__(self, fixture: dict, db: Database):
        super().__init__(title=f"Enter Week {fixture['week_number']} Results")
        self.fixture = fixture
        self.db = db
        self.results_input = discord.ui.TextInput(
            label="Results",
            style=discord.TextStyle.paragraph,
            placeholder="One line per match, e.g. Team A - Team B 2:1",
            default="\n".join(f"{game} 2:0" for game in fixture["games"]),
            required=True,
            max_length=4000,
        )
        self.add_item(self.results_input)

    async def on_submit(self, interaction: discord.Interaction):
        permission_error = await get_admin_permission_error(interaction, self.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        results, errors = parse_line_predictions(self.results_input.value, self.fixture["games"])
        if errors:
            await interaction.response.send_message("\n".join(errors), ephemeral=True)
            return

        preview = _build_results_preview(
            self.fixture["week_number"], self.fixture["games"], results
        )
        view = EnterResultsConfirmView(
            self.db,
            self.fixture,
            results,
            preview,
            str(interaction.user.id),
        )
        await interaction.response.send_message(
            f"{preview}\n\nSave these results?",
            view=view,
            ephemeral=True,
        )
