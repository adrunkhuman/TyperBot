"""Integration tests for complete workflows."""

from datetime import UTC, datetime, timedelta

import pytest

from typer_bot.handlers.fixture_handler import _pending_fixtures
from typer_bot.handlers.results_handler import _pending_results


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear all pending sessions before each test."""
    _pending_fixtures.clear()
    _pending_results.clear()


class TestFullWorkflow:
    """Integration tests for complete prediction workflows."""

    @pytest.mark.asyncio
    async def test_full_workflow_create_predict_results_calculate(self, database):
        """End-to-end workflow produces correct standings."""
        games = ["Team A - Team B", "Team C - Team D", "Team E - Team F"]
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, games, deadline)
        await database.update_fixture_announcement(fixture_id, message_id="789012")

        fixture = await database.get_fixture_by_id(fixture_id)
        assert fixture["week_number"] == 1
        assert len(fixture["games"]) == 3

        await database.save_prediction(fixture_id, "user1", "User1", ["2-1", "1-1", "0-2"], False)
        await database.save_prediction(fixture_id, "user2", "User2", ["1-0", "2-2", "1-1"], False)

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 2

        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        results = await database.get_results(fixture_id)
        assert results == ["2-1", "1-1", "0-2"]

        from typer_bot.utils.scoring import calculate_points

        scores = []
        for pred in predictions:
            score_data = calculate_points(pred["predictions"], results, pred["is_late"])
            scores.append(
                {
                    "user_id": pred["user_id"],
                    "user_name": pred["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        scores.sort(key=lambda x: x["points"], reverse=True)
        await database.save_scores(fixture_id, scores)

        assert scores[0]["user_name"] == "User1"
        assert scores[0]["points"] == 9
        assert scores[0]["exact_scores"] == 3

    @pytest.mark.asyncio
    async def test_multi_user_late_predictions(self, database):
        """Late predictions receive 0 points (100% penalty)."""
        games = ["Team A - Team B", "Team C - Team D", "Team E - Team F"]
        deadline = datetime.now(UTC) - timedelta(hours=1)
        fixture_id = await database.create_fixture(1, games, deadline)

        await database.save_prediction(fixture_id, "user1", "User1", ["2-1", "1-1", "0-2"], True)

        predictions = await database.get_all_predictions(fixture_id)
        assert predictions[0]["is_late"] == 1  # SQLite BOOLEAN returns int, not bool

        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])

        from typer_bot.utils.scoring import calculate_points

        scores = []
        for pred in predictions:
            score_data = calculate_points(
                pred["predictions"], ["2-1", "1-1", "0-2"], pred["is_late"]
            )
            scores.append(
                {
                    "user_id": pred["user_id"],
                    "user_name": pred["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        await database.save_scores(fixture_id, scores)

        assert scores[0]["points"] == 0

    @pytest.mark.asyncio
    async def test_prediction_edits_update_correctly(self, database):
        """Prediction edits update the existing record."""
        games = ["Team A - Team B", "Team C - Team D"]
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, games, deadline)

        await database.save_prediction(fixture_id, "user1", "User1", ["2-1", "1-0"], False)
        await database.save_prediction(fixture_id, "user1", "User1", ["3-0", "2-1"], False)

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["3-0", "2-1"]


class TestEdgeCases:
    """Test edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_delete_fixture_with_predictions(self, database):
        """Cascading deletion removes predictions and results."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, games, deadline)

        await database.save_prediction(fixture_id, "user1", "User1", "2-1", False)
        await database.save_results(fixture_id, ["2-1"])

        await database.delete_fixture(fixture_id)

        assert await database.get_fixture_by_id(fixture_id) is None
        assert len(await database.get_all_predictions(fixture_id)) == 0

    @pytest.mark.asyncio
    async def test_standings_accumulate_across_fixtures(self, database):
        """Standings accumulate across multiple fixtures."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) - timedelta(days=2)
        fixture1_id = await database.create_fixture(1, games, deadline)
        await database.save_prediction(fixture1_id, "user1", "User1", "2-1", False)
        await database.save_results(fixture1_id, ["2-1"])
        await database.save_scores(
            fixture1_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "User1",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        deadline = datetime.now(UTC) - timedelta(days=1)
        fixture2_id = await database.create_fixture(2, games, deadline)
        await database.save_prediction(fixture2_id, "user1", "User1", "1-0", False)
        await database.save_results(fixture2_id, ["1-0"])
        await database.save_scores(
            fixture2_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "User1",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        standings = await database.get_standings()
        assert len(standings) == 1
        assert standings[0]["total_points"] == 6

    @pytest.mark.asyncio
    async def test_max_week_number_increment(self, database):
        """Week numbers are tracked for chronological ordering."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) + timedelta(days=1)

        await database.create_fixture(5, games, deadline)
        await database.create_fixture(3, games, deadline)

        max_week = await database.get_max_week_number()
        assert max_week == 5


class TestDatabaseIntegrity:
    """Test database integrity constraints."""

    @pytest.mark.asyncio
    async def test_prediction_uniqueness_per_user_fixture(self, database):
        """One prediction per user per fixture - edits update existing record."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, games, deadline)

        await database.save_prediction(fixture_id, "user1", "User1", ["2-1"], False)
        await database.save_prediction(fixture_id, "user1", "User1", ["1-0"], False)

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["1-0"]
