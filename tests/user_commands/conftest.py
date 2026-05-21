import pytest

from typer_bot.commands.user_commands import (
    UserCommands,
)


@pytest.fixture
async def user_commands(mock_bot, database):
    mock_bot.db = database
    return UserCommands(mock_bot)
