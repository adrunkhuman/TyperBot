import pytest


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_uses_active_season_scoring_rules(
        self,
        user_commands,
        mock_interaction,
        database,
    ):
        await database.update_active_scoring_rules(
            "111111",
            {
                "exact_score_points": 5,
                "correct_outcome_points": 2,
                "wrong_outcome_points": 1,
                "late_prediction_points": 1,
            },
        )

        await user_commands.help.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[-1]["content"]
        assert "Exact score: 5 points" in content
        assert "Correct result (win/loss/draw): 2 points" in content
        assert "Wrong: 1 point" in content
        assert "Late full predictions: 1 point" in content
