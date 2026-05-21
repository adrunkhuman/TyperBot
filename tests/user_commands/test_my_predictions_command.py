from datetime import UTC, datetime, timedelta

import pytest


class TestMyPredictionsCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_only_uses_current_guild_fixtures(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture("guild-2", 1, sample_games, deadline)
        await database.save_prediction(
            fixture_id,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_single_fixture_without_prediction_shows_prompt(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "haven't submitted predictions" in content
        assert "Use `/predict`" in content

    @pytest.mark.asyncio
    async def test_single_fixture_prediction_shows_saved_scores(
        self, user_commands, mock_interaction, database, sample_games
    ):
        fixture_id = await database.create_fixture(
            "111111", 1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_prediction(
            fixture_id,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions:" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "Status:" in content
        assert "Submitted:" in content

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_show_mixed_prediction_state(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_week_1 = await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)
        await database.save_prediction(
            fixture_week_1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions (Open Fixtures):" in content
        assert "Week 1" in content
        assert "Week 2" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "No prediction submitted yet." in content
