"""Post-calculation publishing helpers."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from typer_bot.database import Database
from typer_bot.services.admin_service import FixtureScoreResult
from typer_bot.utils import build_discord_message_chunks, format_fixture_results, format_standings
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

    The interaction may already be deferred. Admin feedback is sent through the
    initial response or an ephemeral followup based on interaction state. Backup
    failures are logged but do not fail score calculation; posting failures are
    reported ephemerally to the admin.
    """
    await _create_backup(bot, db.db_path)
    await _post_calculation_to_channel(bot, db, interaction, score_result)


async def _send_interaction_feedback(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
        return
    await interaction.response.send_message(content, ephemeral=True)


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
        await _send_interaction_feedback(
            interaction,
            "Scores calculated but could not resolve this server.",
        )
        return

    config = await db.guild_config.get_guild_config(str(interaction.guild_id))
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
        await _send_interaction_feedback(
            interaction,
            "Scores calculated but the configured league channel is unavailable.",
        )
        return

    results_section = format_fixture_results(
        score_result.fixture["games"],
        score_result.results,
        score_result.fixture["week_number"],
    )
    standings_section = format_standings(score_result.standings, score_result.last_fixture)
    message_chunks = build_discord_message_chunks([results_section, standings_section])

    try:
        for message_chunk in message_chunks:
            await channel.send(message_chunk)
        await _send_interaction_feedback(
            interaction,
            f"Week {score_result.fixture['week_number']} results calculated and posted to the league channel!",
        )
    except Exception as exc:
        logger.error(f"Failed to post results to channel: {exc}")
        await _send_interaction_feedback(
            interaction,
            "Scores calculated but failed to post to channel.",
        )
