"""Fixture creation modals for the admin panel."""

from __future__ import annotations

from datetime import datetime, timedelta

import discord

from typer_bot.database import Database
from typer_bot.utils import APP_TZ, format_for_discord, get_admin_permission_error, now

MAX_GAMES = 100


def _default_fixture_deadline(current_time: datetime) -> datetime:
    days_until_friday = (4 - current_time.weekday()) % 7
    if days_until_friday == 0 and current_time.hour >= 18:
        days_until_friday = 7
    deadline = current_time + timedelta(days=days_until_friday)
    return deadline.replace(hour=18, minute=0, second=0, microsecond=0)


def _build_fixture_preview_text(week_number: int, games: list[str], deadline: datetime) -> str:
    lines = [f"**Week {week_number} Fixture Preview**", ""]
    lines.extend(f"{index}. {game}" for index, game in enumerate(games, 1))
    lines.extend(
        [
            "",
            f"**Deadline:** {format_for_discord(deadline, 'F')} ({format_for_discord(deadline, 'R')})",
        ]
    )
    return "\n".join(lines)


def _parse_fixture_games(games_text: str) -> list[str]:
    games = [line.strip() for line in games_text.strip().split("\n") if line.strip()]
    if len(games) > MAX_GAMES:
        raise ValueError(f"Too many games! (max {MAX_GAMES})")
    if not games:
        raise ValueError("No games provided! Please enter at least one fixture.")
    return games


def _parse_fixture_deadline(deadline_text: str, current_time: datetime) -> datetime:
    if not deadline_text.strip():
        return _default_fixture_deadline(current_time)

    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(deadline_text.strip(), fmt).replace(tzinfo=APP_TZ)
        except ValueError:
            continue

    raise ValueError(
        "Invalid date format. Use one of these formats:\n"
        "2024-02-15 18:00\n"
        "15.02.2024 18:00\n"
        "15/02/2024 18:00"
    )


class CreateFixtureConfirmView(discord.ui.View):
    """Confirm or cancel modal-based fixture creation.

    Confirm persists the fixture, posts the announcement, and attempts to create
    the predictions thread. The fixture may still be created even if the later
    Discord-side steps fail.
    """

    def __init__(
        self,
        db: Database,
        channel,
        owner_user_id: str,
        preview_week_number: int,
        games: list[str],
        deadline: datetime,
        bot: discord.Client | None = None,
    ):
        super().__init__(timeout=120)
        self.db = db
        self.channel = channel
        self.owner_user_id = owner_user_id
        self.preview_week_number = preview_week_number
        self.games = games
        self.deadline = deadline
        self.bot = bot

    async def _get_league_channel(self, guild_id: str):
        config = await self.db.get_guild_config(guild_id)
        if config is None:
            return None

        league_channel_id = int(config["league_channel_id"])
        if getattr(self.channel, "id", None) == league_channel_id:
            return self.channel

        if self.bot is None:
            return None

        channel = self.bot.get_channel(league_channel_id)
        if channel is None:
            fetch_channel = getattr(self.bot, "fetch_channel", None)
            if fetch_channel is not None:
                try:
                    channel = await fetch_channel(league_channel_id)
                except discord.HTTPException:
                    return None
        return channel if getattr(channel, "send", None) is not None else None

    @discord.ui.button(label="Create Fixture", style=discord.ButtonStyle.green)
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

        league_channel = await self._get_league_channel(str(interaction.guild_id))
        if league_channel is None:
            await interaction.response.send_message(
                "Configured league channel is unavailable. Run `/admin panel` again to update setup.",
                ephemeral=True,
            )
            return

        fixture_id, allocated_week = await self.db.create_next_fixture(
            str(interaction.guild_id),
            self.games,
            self.deadline,
        )
        final_preview = _build_fixture_preview_text(allocated_week, self.games, self.deadline)

        created_text = f"**Week {allocated_week} Fixture Created!**\n\n{final_preview}"
        if allocated_week != self.preview_week_number:
            created_text += (
                f"\n\n⚠️ **Week number changed:** preview showed Week {self.preview_week_number} but "
                f"this was created as **Week {allocated_week}** because the fixture set "
                f"changed between steps."
            )

        await interaction.response.edit_message(content=created_text, view=None)

        try:
            announcement = await league_channel.send(
                f"**Week {allocated_week} Fixture is now open!**\n\n"
                f"{final_preview}\n\n"
                f"💬 **How to predict:**\n"
                f"• Reply in this thread with your scores (one per line)\n"
                f"• Or use `/predict` to fill a modal and post publicly here"
            )

            await self.db.update_fixture_announcement(
                fixture_id,
                message_id=str(announcement.id),
                channel_id=str(league_channel.id),
            )

            try:
                thread = await announcement.create_thread(
                    name=f"Week {allocated_week} Predictions",
                    auto_archive_duration=1440,
                )
                await thread.send(
                    "💬 **Post your predictions here!**\n"
                    "Reply with one line per match. For partial updates, include the game name on each line.\n"
                    "Predictions are one-shot here. To change one, use `/predict`."
                )
            except Exception:
                await interaction.followup.send(
                    "⚠️ Fixture created but I couldn't create a prediction thread. Restore the thread before users can predict.",
                    ephemeral=True,
                )
        except Exception:
            await interaction.followup.send(
                "⚠️ Fixture created but I couldn't announce it in the channel. Please announce it manually.",
                ephemeral=True,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.owner_user_id:
            await interaction.response.send_message(
                "You don't have permission to do this!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content="Fixture creation cancelled.",
            view=None,
        )


class CreateFixtureModal(discord.ui.Modal):
    """Collect fixture games and an optional deadline in one interaction.

    Blank deadline falls back to Friday 18:00 in the app timezone. Manual
    deadlines accept `YYYY-MM-DD HH:MM`, `DD.MM.YYYY HH:MM`, or
    `DD/MM/YYYY HH:MM`. Open fixtures show a warning in the preview, but do not
    block creation. Nothing is persisted until the confirm view succeeds.
    """

    def __init__(
        self, db: Database, channel, owner_user_id: str, bot: discord.Client | None = None
    ):
        super().__init__(title="Create Fixture")
        self.db = db
        self.channel = channel
        self.owner_user_id = owner_user_id
        self.bot = bot
        self.games_input = discord.ui.TextInput(
            label="Games",
            style=discord.TextStyle.paragraph,
            placeholder="Team A - Team B\nTeam C - Team D",
            required=True,
            max_length=4000,
        )
        self.deadline_input = discord.ui.TextInput(
            label="Deadline",
            style=discord.TextStyle.short,
            placeholder="YYYY-MM-DD HH:MM or blank for Friday 18:00",
            required=False,
            max_length=32,
        )
        self.add_item(self.games_input)
        self.add_item(self.deadline_input)

    async def on_submit(self, interaction: discord.Interaction):
        permission_error = await get_admin_permission_error(interaction, self.db)
        if permission_error is not None:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        try:
            games = _parse_fixture_games(self.games_input.value)
            deadline = _parse_fixture_deadline(self.deadline_input.value, now())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        preview_week_number = await self.db.get_max_week_number(str(interaction.guild_id)) + 1
        preview = _build_fixture_preview_text(preview_week_number, games, deadline)
        open_fixtures = await self.db.get_open_fixtures(str(interaction.guild_id))
        if open_fixtures:
            open_weeks = ", ".join(str(fixture["week_number"]) for fixture in open_fixtures)
            preview += (
                f"\n\n⚠️ **Warning:** Week(s) {open_weeks} are already open. "
                "Creating another fixture may overlap prediction windows."
            )

        view = CreateFixtureConfirmView(
            self.db,
            self.channel,
            self.owner_user_id,
            preview_week_number,
            games,
            deadline,
            self.bot,
        )
        await interaction.response.send_message(
            f"{preview}\n\nCreate this fixture?",
            view=view,
            ephemeral=True,
        )
