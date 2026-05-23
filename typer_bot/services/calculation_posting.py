"""Post-calculation publishing helpers."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import format_fixture_results, format_standings
from typer_bot.utils.config import BACKUP_DIR
from typer_bot.utils.db_backup import cleanup_old_backups, create_backup

logger = logging.getLogger(__name__)


async def post_calculation_result(
    bot: commands.Bot | discord.Client,
    db: Database,
    interaction: discord.Interaction,
    score_result: FixtureScoreResult,
) -> None:
    """Run best-effort DB backup, then publish fixture results and standings.

    The interaction response must still be unused. Backup failures are logged but
    do not fail score calculation; posting failures are reported ephemerally to
    the admin.
    """
    await _create_backup(bot, db.db_path)
    await _post_calculation_to_channel(bot, db, interaction, score_result)


async def _create_backup(bot: commands.Bot | discord.Client, db_path: str) -> None:
    try:
        await bot.loop.run_in_executor(None, lambda: create_backup(db_path, BACKUP_DIR))
        await bot.loop.run_in_executor(None, lambda: cleanup_old_backups(BACKUP_DIR, keep=10))
    except Exception as exc:
        logger.warning(f"Backup failed but calculation succeeded: {exc}")


async def _post_calculation_to_channel(
    bot: commands.Bot | discord.Client,
    db: Database,
    interaction: discord.Interaction,
    score_result: FixtureScoreResult,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Scores calculated but could not resolve this server.", ephemeral=True
        )
        return

    config = await db.get_guild_config(str(interaction.guild_id))
    channel = None
    if config is not None:
        try:
            channel_id = int(config["league_channel_id"])
        except (TypeError, ValueError):
            channel_id = None
        if channel_id is not None:
            channel = bot.get_channel(channel_id)
            if channel is None:
                fetch_channel = getattr(bot, "fetch_channel", None)
                if fetch_channel is not None:
                    try:
                        channel = await fetch_channel(channel_id)
                    except discord.DiscordException:
                        channel = None

    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Scores calculated but the configured league channel is unavailable.",
            ephemeral=True,
        )
        return

    results_section = format_fixture_results(
        score_result.fixture["games"],
        score_result.results,
        score_result.fixture["week_number"],
    )
    message = (
        results_section
        + "\n\n"
        + format_standings(score_result.standings, score_result.last_fixture)
    )

    try:
        await channel.send(message)
        await interaction.response.send_message(
            f"Week {score_result.fixture['week_number']} results calculated and posted to the league channel!",
            ephemeral=True,
        )
    except Exception as exc:
        logger.error(f"Failed to post results to channel: {exc}")
        await interaction.response.send_message(
            "Scores calculated but failed to post to channel.", ephemeral=True
        )
