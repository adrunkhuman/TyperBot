"""Integration tests for complete workflows."""

from datetime import UTC, datetime, timedelta

import pytest


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
    async def test_prediction_resubmission_replaces_existing(self, database):
        """Re-submitting a prediction replaces the existing one (upsert behavior)."""
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

    @pytest.mark.asyncio
    async def test_calculating_one_fixture_keeps_other_fixture_open(self, database):
        """Closing one fixture should not block another concurrently open fixture."""
        games = ["Team A - Team B", "Team C - Team D"]
        deadline = datetime.now(UTC) + timedelta(days=1)

        fixture_one_id = await database.create_fixture(1, games, deadline)
        fixture_two_id = await database.create_fixture(2, games, deadline)

        await database.save_prediction(fixture_one_id, "user1", "User1", ["2-1", "1-1"], False)
        await database.save_results(fixture_one_id, ["2-1", "1-1"])
        await database.save_scores(
            fixture_one_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "User1",
                    "points": 6,
                    "exact_scores": 2,
                    "correct_results": 0,
                }
            ],
        )

        fixture_one = await database.get_fixture_by_id(fixture_one_id)
        fixture_two = await database.get_fixture_by_id(fixture_two_id)
        open_fixtures = await database.get_open_fixtures()

        assert fixture_one is not None
        assert fixture_one["status"] == "closed"
        assert fixture_two is not None
        assert fixture_two["status"] == "open"
        assert [fixture["id"] for fixture in open_fixtures] == [fixture_two_id]


class TestDatabaseIntegrity:
    """Test database integrity constraints."""

    @pytest.mark.asyncio
    async def test_prediction_uniqueness_per_user_fixture(self, database):
        """One prediction per user per fixture - re-submission replaces existing."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, games, deadline)

        await database.save_prediction(fixture_id, "user1", "User1", ["2-1"], False)
        await database.save_prediction(fixture_id, "user1", "User1", ["1-0"], False)

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["1-0"]


class TestUsernameChangeHandling:
    """Test handling of users changing Discord display names between fixtures."""

    @pytest.mark.asyncio
    async def test_standings_aggregates_same_user_with_different_names(self, database):
        """Users changing display names should appear once in standings with latest name."""
        games = ["Team A - Team B"]
        deadline = datetime.now(UTC) - timedelta(days=2)

        # Week 1: User is "st4chu"
        fixture1_id = await database.create_fixture(1, games, deadline)
        await database.save_prediction(fixture1_id, "user1", "st4chu", ["2-1"], False)
        await database.save_results(fixture1_id, ["2-1"])
        await database.save_scores(
            fixture1_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "st4chu",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        # Week 2: Same user is now "Stachu"
        deadline = datetime.now(UTC) - timedelta(days=1)
        fixture2_id = await database.create_fixture(2, games, deadline)
        await database.save_prediction(fixture2_id, "user1", "Stachu", ["1-0"], False)
        await database.save_results(fixture2_id, ["1-0"])
        await database.save_scores(
            fixture2_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "Stachu",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        standings = await database.get_standings()
        assert len(standings) == 1  # Should NOT be 2 separate entries
        assert standings[0]["total_points"] == 6
        assert standings[0]["weeks_played"] == 2
        # Should show most recent name (from fixture 2)
        assert standings[0]["user_name"] == "Stachu"

    @pytest.mark.asyncio
    async def test_standings_shows_latest_name_when_changed_multiple_times(self, database):
        """Should show the most recent username from latest fixture."""
        games = ["Team A - Team B"]

        # Week 1: "OldName"
        fixture1_id = await database.create_fixture(1, games, datetime.now(UTC) - timedelta(days=3))
        await database.save_results(fixture1_id, ["2-1"])
        await database.save_scores(
            fixture1_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "OldName",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )

        # Week 2: "MiddleName"
        fixture2_id = await database.create_fixture(2, games, datetime.now(UTC) - timedelta(days=2))
        await database.save_results(fixture2_id, ["2-1"])
        await database.save_scores(
            fixture2_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "MiddleName",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )

        # Week 3: "NewName" (latest)
        fixture3_id = await database.create_fixture(3, games, datetime.now(UTC) - timedelta(days=1))
        await database.save_results(fixture3_id, ["2-1"])
        await database.save_scores(
            fixture3_id,
            [
                {
                    "user_id": "user1",
                    "user_name": "NewName",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )

        standings = await database.get_standings()
        assert len(standings) == 1
        # Should show name from most recent fixture (fixture 3)
        assert standings[0]["user_name"] == "NewName"
        assert standings[0]["total_points"] == 3
