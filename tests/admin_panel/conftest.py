import pytest

from typer_bot.commands.admin_commands import AdminCommands


@pytest.fixture
def admin_cog(mock_bot, database):
    mock_bot.db = database
    return AdminCommands(mock_bot)
