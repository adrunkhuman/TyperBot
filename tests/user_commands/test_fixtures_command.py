from datetime import UTC, datetime, timedelta

import pytest


class TestFixturesCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.fixtures.callback(user_commands, mock_interaction)

        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_single_open_fixture_lists_games_and_deadline(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Week 1 Fixtures" in content
        assert sample_games[0] in content
        assert "Deadline:" in content
        assert mock_interaction.response_sent[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_list_each_week(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Open Fixtures" in content
        assert "Week 1" in content
        assert "Week 2" in content

    @pytest.mark.asyncio
    async def test_fixtures_only_shows_current_guild(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("guild-2", 2, sample_games, deadline)

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Week 1" in content
        assert "Week 2" not in content
