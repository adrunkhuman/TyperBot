from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import typer_bot.services.calculation_posting as calculation_posting
from typer_bot.services.admin_service import FixtureScoreResult


def _score_result(sample_games: list[str]) -> FixtureScoreResult:
    return FixtureScoreResult(
        fixture={"games": sample_games, "week_number": 7},
        results=["2-1", "1-1", "0-2"],
        predictions=[],
        scores=[],
        standings=[
            {
                "user_id": "111",
                "user_name": "User One",
                "total_points": 7,
                "total_exact": 2,
                "total_correct": 1,
            }
        ],
        last_fixture=None,
    )


def _bot_with_executor() -> MagicMock:
    bot = MagicMock(spec=discord.Client)
    bot.loop = MagicMock()

    async def run_in_executor(_executor, callback):
        return callback()

    bot.loop.run_in_executor = AsyncMock(side_effect=run_in_executor)
    return bot


@pytest.mark.asyncio
async def test_post_calculation_result_posts_to_configured_league_channel(
    database,
    mock_interaction_admin,
    sample_games,
    monkeypatch,
):
    bot = _bot_with_executor()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    monkeypatch.setattr(calculation_posting, "create_backup", MagicMock(return_value="backup.sql"))
    monkeypatch.setattr(calculation_posting, "cleanup_old_backups", MagicMock(return_value=0))

    await calculation_posting.post_calculation_result(
        bot, database, mock_interaction_admin, _score_result(sample_games)
    )

    channel.send.assert_awaited_once()
    assert "Week 7 Results" in channel.send.call_args.args[0]
    assert "User One" in channel.send.call_args.args[0]
    assert "posted to the league channel" in mock_interaction_admin.response_sent[-1]["content"]


@pytest.mark.asyncio
async def test_post_calculation_result_posts_when_backup_fails(
    database,
    mock_interaction_admin,
    sample_games,
    monkeypatch,
):
    bot = _bot_with_executor()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    monkeypatch.setattr(
        calculation_posting,
        "create_backup",
        MagicMock(side_effect=RuntimeError("backup failed")),
    )

    await calculation_posting.post_calculation_result(
        bot, database, mock_interaction_admin, _score_result(sample_games)
    )

    channel.send.assert_awaited_once()
    assert "posted to the league channel" in mock_interaction_admin.response_sent[-1]["content"]


@pytest.mark.asyncio
async def test_post_calculation_result_reports_send_failure(
    database,
    mock_interaction_admin,
    sample_games,
    monkeypatch,
):
    bot = _bot_with_executor()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(side_effect=RuntimeError("discord unavailable"))
    bot.get_channel.return_value = channel
    monkeypatch.setattr(calculation_posting, "create_backup", MagicMock(return_value="backup.sql"))
    monkeypatch.setattr(calculation_posting, "cleanup_old_backups", MagicMock(return_value=0))

    await calculation_posting.post_calculation_result(
        bot, database, mock_interaction_admin, _score_result(sample_games)
    )

    assert "failed to post" in mock_interaction_admin.response_sent[-1]["content"]


@pytest.mark.asyncio
async def test_post_calculation_result_reports_unavailable_league_channel(
    database,
    mock_interaction_admin,
    sample_games,
    monkeypatch,
):
    bot = _bot_with_executor()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=discord.InvalidData("unknown channel type"))
    monkeypatch.setattr(calculation_posting, "create_backup", MagicMock(return_value="backup.sql"))
    monkeypatch.setattr(calculation_posting, "cleanup_old_backups", MagicMock(return_value=0))

    await calculation_posting.post_calculation_result(
        bot, database, mock_interaction_admin, _score_result(sample_games)
    )

    assert (
        "configured league channel is unavailable"
        in mock_interaction_admin.response_sent[-1]["content"].lower()
    )
