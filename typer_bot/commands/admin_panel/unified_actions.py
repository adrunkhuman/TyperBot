"""Workflow buttons for the unified admin panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from typer_bot.utils import format_standings, get_admin_permission_error, has_setup_permission, now

from .modals import CreateFixtureModal, EnterResultsModal

if TYPE_CHECKING:
    from .unified import UnifiedAdminPanelView


class SetupBotButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Setup TyperBot", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        from typer_bot.commands.admin_commands import GuildSetupPromptView

        if not has_setup_permission(interaction):
            await interaction.response.send_message(
                "Only a server admin can configure TyperBot for this server.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Update TyperBot setup for this server.",
            view=GuildSetupPromptView(self.parent_view.db, str(interaction.user.id)),
            ephemeral=True,
        )


class CreateFixtureButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Create Fixture", style=discord.ButtonStyle.success, row=row)

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if interaction.guild is None or channel is None:
            await interaction.response.send_message(
                "Error: Invalid interaction context.", ephemeral=True
            )
            return
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if not isinstance(parent, discord.TextChannel):
                await interaction.response.send_message(
                    "Fixture creation must be started from a server text channel.", ephemeral=True
                )
                return
            channel = parent
        elif not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Fixture creation must be started from a server text channel.", ephemeral=True
            )
            return

        modal = CreateFixtureModal(
            self.parent_view.db,
            channel,
            str(interaction.user.id),
            self.parent_view.bot,
        )
        await interaction.response.send_modal(modal)


class NewSeasonModal(discord.ui.Modal):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        super().__init__(title="Start New Season")
        self.parent_view = parent_view
        self.name_input = discord.ui.TextInput(
            label="Season Name",
            placeholder="e.g. 2026/27",
            required=True,
            max_length=80,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.parent_view.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return
        permission_error = await get_admin_permission_error(interaction, self.parent_view.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Season management must be used in a server.", ephemeral=True
            )
            return

        try:
            season = await self.parent_view.db.start_new_season(
                str(interaction.guild_id),
                self.name_input.value,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.parent_view.selection.fixture_id = None
        self.parent_view.selection.fixture_label = ""
        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = []
        self.parent_view.selection.status_message = f"Started new active season: {season['name']}"
        self.parent_view.current_prediction = None
        self.parent_view.has_user_overflow = False
        self.parent_view.user_select.update_options([])
        await self.parent_view.load_fixture_options()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class NewSeasonButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="New Season", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewSeasonModal(self.parent_view))


class JumpToWeekModal(discord.ui.Modal):
    def __init__(self, parent_view: UnifiedAdminPanelView):
        super().__init__(title="Jump To Week")
        self.parent_view = parent_view
        self.week_input = discord.ui.TextInput(
            label="Week Number",
            placeholder="e.g. 12",
            required=True,
            max_length=8,
        )
        self.add_item(self.week_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            week_number = int(self.week_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "Week number must be a whole number.", ephemeral=True
            )
            return

        open_fixtures = await self.parent_view.db.get_open_fixtures(self.parent_view.guild_id)
        matching = [fixture for fixture in open_fixtures if fixture["week_number"] == week_number]
        if not matching:
            await interaction.response.send_message(
                f"No open fixture found for week {week_number}.", ephemeral=True
            )
            return
        if len(matching) > 1:
            await interaction.response.send_message(
                f"More than one open fixture was found for week {week_number}. Resolve duplicate week numbers first.",
                ephemeral=True,
            )
            return

        fixture = matching[0]
        self.parent_view.selection.fixture_id = fixture["id"]
        self.parent_view.selection.fixture_label = (
            f"Week {fixture['week_number']} [{fixture['status'].upper()}]"
        )
        self.parent_view.selection.user_id = None
        self.parent_view.selection.user_label = ""
        self.parent_view.selection.detail_lines = []
        self.parent_view.selection.status_message = ""
        await self.parent_view.populate_fixture_details(fixture)
        await self.parent_view.load_user_options()
        self.parent_view.fixture_select.sync_selected_option()
        self.parent_view._refresh_items()
        await interaction.response.edit_message(
            content=self.parent_view.render_content(),
            view=self.parent_view,
        )


class JumpToWeekButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Jump To Week", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(JumpToWeekModal(self.parent_view))


class EnterResultsButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(
            label="Enter Results",
            style=discord.ButtonStyle.secondary,
            disabled=parent_view.selection.fixture_id is None,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return
        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "That fixture is no longer open.", ephemeral=True
            )
            return
        existing_results = await self.parent_view.db.get_results(fixture_id)
        if existing_results:
            await interaction.response.send_message(
                "Results already entered for this fixture. Use Correct Results instead.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EnterResultsModal(fixture, self.parent_view.db))


class CalculateScoresButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(
            label="Calculate Scores",
            style=discord.ButtonStyle.secondary,
            disabled=parent_view.selection.fixture_id is None,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        fixture_id = self.parent_view.selection.fixture_id
        if fixture_id is None:
            await interaction.response.send_message("Select a fixture first.", ephemeral=True)
            return
        fixture = await self.parent_view.db.get_fixture_by_id(fixture_id, self.parent_view.guild_id)
        if fixture is None or fixture["status"] != "open":
            await interaction.response.send_message(
                "That fixture is no longer open.", ephemeral=True
            )
            return

        admin_commands = self.parent_view.admin_commands
        if admin_commands is None:
            await interaction.response.send_message(
                "Calculate Scores is unavailable in this context.", ephemeral=True
            )
            return
        user_id = str(interaction.user.id)
        current_time = now().timestamp()
        remaining = admin_commands.get_calculate_cooldown_remaining(
            self.parent_view.guild_id,
            user_id,
            current_time=current_time,
            cooldown_seconds=30.0,
        )
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {remaining:.1f}s before calculating again.", ephemeral=True
            )
            return

        try:
            score_result = await self.parent_view.service.calculate_fixture_scores(
                fixture_id, self.parent_view.guild_id
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        admin_commands.record_calculate_cooldown(
            self.parent_view.guild_id, user_id, current_time=current_time
        )
        await admin_commands._create_backup()
        await admin_commands._post_calculation_to_channel(interaction, score_result)


class PostResultsConfirmView(discord.ui.View):
    def __init__(
        self,
        fixture_data: dict,
        standings: list[dict],
        channel: discord.TextChannel,
    ):
        super().__init__(timeout=60)
        self.fixture_data = fixture_data
        self.standings = standings
        self.channel = channel

    @discord.ui.button(label="No Mentions", style=discord.ButtonStyle.primary)
    async def no_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        try:
            await interaction.response.edit_message(
                content="Results posted without mentions!", view=None
            )
        except Exception:
            return
        try:
            await self.channel.send(message)
        except Exception as exc:
            await interaction.followup.send(f"Failed to post results: {exc}")

    @discord.ui.button(label="Mention Users", style=discord.ButtonStyle.green)
    async def with_mentions(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = format_standings(self.standings, self.fixture_data)
        mentions = [f"<@{score['user_id']}>" for score in self.fixture_data["scores"]]
        message += f"\n\n**Participants:**\n{' '.join(mentions)}"
        try:
            await interaction.response.edit_message(
                content="Results posted with mentions!", view=None
            )
        except Exception:
            return
        try:
            await self.channel.send(message)
        except Exception as exc:
            await interaction.followup.send(f"Failed to post results: {exc}")


class PostResultsButton(discord.ui.Button):
    def __init__(self, parent_view: UnifiedAdminPanelView, row: int | None = None):
        self.parent_view = parent_view
        super().__init__(label="Re-post Results", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        fixture_data = await self.parent_view.db.get_last_fixture_scores(self.parent_view.guild_id)
        standings = await self.parent_view.db.get_standings(self.parent_view.guild_id)
        if not fixture_data:
            await interaction.response.send_message(
                "No completed fixtures found with scores!", ephemeral=True
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This action can only be used in text channels.", ephemeral=True
            )
            return
        preview = format_standings(standings, fixture_data)
        view = PostResultsConfirmView(fixture_data, standings, interaction.channel)
        await interaction.response.send_message(
            f"{preview}\n\nMention users in this post?",
            view=view,
            ephemeral=True,
        )
