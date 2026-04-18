"""Tests for shared admin service workflows."""

from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

import typer_bot.database.predictions as predictions_module
import typer_bot.database.results as results_module
from typer_bot.services import AdminService
from typer_bot.services.errors import (
    FixtureNotFoundError,
    NoPredictionsSavedError,
    PredictionDisappearedError,
    PredictionNotFoundError,
)


class TestLatePenaltyWaiver:
    """Late waivers should only change penalty application."""

    @pytest.mark.asyncio
    async def test_toggle_late_penalty_waiver_recalculates_closed_fixture(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) - timedelta(hours=2)
        )

        await database.save_prediction(
            fixture_id,
            "late-user",
            "Late User",
            ["2-1", "1-1", "0-2"],
            True,
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await service.calculate_fixture_scores(fixture_id)

        standings = await database.get_standings()
        assert standings[0]["total_points"] == 0

        fixture, prediction, recalculation = await service.toggle_late_penalty_waiver(
            fixture_id,
            "late-user",
        )

        assert fixture["week_number"] == 1
        assert prediction["late_penalty_waived"] == 1
        assert recalculation is not None

        standings = await database.get_standings()
        assert standings[0]["total_points"] == 9

    @pytest.mark.asyncio
    async def test_toggle_late_waiver_rolls_back_if_recalculation_fails(
        self,
        database,
        sample_games,
        monkeypatch,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) - timedelta(hours=2)
        )

        await database.save_prediction(
            fixture_id,
            "late-user",
            "Late User",
            ["2-1", "1-1", "0-2"],
            True,
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await service.calculate_fixture_scores(fixture_id)

        async def raise_recalc_error(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            predictions_module, "_recalculate_scores_in_connection", raise_recalc_error
        )

        with pytest.raises(RuntimeError, match="boom"):
            await service.toggle_late_penalty_waiver(fixture_id, "late-user")

        prediction = await database.get_prediction(fixture_id, "late-user")
        assert prediction is not None
        assert prediction["late_penalty_waived"] == 0


class TestAdminFlowErrors:
    @pytest.mark.asyncio
    async def test_get_fixture_prediction_summary_raises_typed_fixture_not_found(self, database):
        service = AdminService(database)

        with pytest.raises(FixtureNotFoundError):
            await service.get_fixture_prediction_summary(999)

    @pytest.mark.asyncio
    async def test_get_fixture_prediction_summary_raises_typed_no_predictions(
        self, database, sample_games
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        with pytest.raises(NoPredictionsSavedError):
            await service.get_fixture_prediction_summary(fixture_id)

    @pytest.mark.asyncio
    async def test_replace_prediction_raises_typed_prediction_not_found(
        self, database, sample_games
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        with pytest.raises(PredictionNotFoundError):
            await service.replace_prediction(
                fixture_id,
                "missing-user",
                "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
                "admin-1",
            )

    @pytest.mark.asyncio
    async def test_toggle_waiver_raises_typed_prediction_disappeared(
        self, database, sample_games, monkeypatch
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) - timedelta(hours=2)
        )
        await database.save_prediction(
            fixture_id,
            "late-user",
            "Late User",
            ["2-1", "1-1", "0-2"],
            True,
        )

        original_get_prediction = database.get_prediction

        async def disappear_after_toggle(request_fixture_id: int, user_id: str):
            prediction = await original_get_prediction(request_fixture_id, user_id)
            if prediction is not None and prediction["late_penalty_waived"]:
                return None
            return prediction

        monkeypatch.setattr(database, "get_prediction", disappear_after_toggle)

        with pytest.raises(PredictionDisappearedError, match="waiver update"):
            await service.toggle_late_penalty_waiver(fixture_id, "late-user")


class TestPredictionReplacement:
    """Admin edits should preserve original timing facts."""

    @pytest.mark.asyncio
    async def test_replace_prediction_preserves_original_timing_metadata(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        await database.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            True,
        )

        original = await database.get_prediction(fixture_id, "user-1")
        assert original is not None

        fixture, updated_prediction, recalculation = await service.replace_prediction(
            fixture_id,
            "user-1",
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
            "admin-1",
        )

        assert fixture["week_number"] == 1
        assert recalculation is None
        assert updated_prediction["predictions"] == ["2-1", "1-1", "0-2"]
        assert updated_prediction["submitted_at"] == original["submitted_at"]
        assert updated_prediction["is_late"] == original["is_late"]
        assert updated_prediction["admin_edited_by"] == "admin-1"
        assert updated_prediction["admin_edited_at"] is not None

    @pytest.mark.asyncio
    async def test_replace_prediction_rolls_back_if_recalculation_fails(
        self,
        database,
        sample_games,
        monkeypatch,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        await database.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        await database.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await service.calculate_fixture_scores(fixture_id)

        async def raise_recalc_error(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            predictions_module, "_recalculate_scores_in_connection", raise_recalc_error
        )

        with pytest.raises(RuntimeError, match="boom"):
            await service.replace_prediction(
                fixture_id,
                "user-1",
                "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
                "admin-1",
            )

        prediction = await database.get_prediction(fixture_id, "user-1")
        assert prediction is not None
        assert prediction["predictions"] == ["1-0", "1-1", "0-0"]
        assert prediction["admin_edited_by"] is None

    @pytest.mark.asyncio
    async def test_replace_prediction_recalculates_scored_fixture(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )

        await database.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        await database.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await service.calculate_fixture_scores(fixture_id)

        before = await database.get_standings()
        assert before[0]["total_points"] == 9

        _fixture, updated_prediction, recalculation = await service.replace_prediction(
            fixture_id,
            "user-1",
            "Team A - Team B 2-1\nTeam C - Team D 2-2\nTeam E - Team F 1-0",
            "admin-1",
        )

        assert updated_prediction["admin_edited_by"] == "admin-1"
        assert recalculation is not None

        after = await database.get_standings()
        assert after[0]["total_points"] == 2


class TestResultCorrection:
    """Result correction should refresh saved scores and standings."""

    @pytest.mark.asyncio
    async def test_correct_results_recalculates_fixture_scores_and_standings(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture1_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        fixture2_id = await database.create_fixture(
            2, sample_games, datetime.now(UTC) + timedelta(days=2)
        )

        await database.save_prediction(
            fixture1_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-2"],
            False,
        )
        await database.save_prediction(
            fixture1_id,
            "user-2",
            "User Two",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await database.save_results(fixture1_id, ["1-0", "1-1", "0-2"])
        await service.calculate_fixture_scores(fixture1_id)

        await database.save_prediction(
            fixture2_id,
            "user-1",
            "User One",
            ["2-0", "0-0", "1-1"],
            False,
        )
        await database.save_results(fixture2_id, ["2-0", "0-0", "1-1"])
        await service.calculate_fixture_scores(fixture2_id)

        before = await database.get_standings()
        assert before[0]["user_id"] == "user-1"
        assert before[0]["total_points"] == 18
        assert before[1]["total_points"] == 7

        fixture, results, recalculation = await service.correct_results(
            fixture1_id,
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2",
        )

        assert fixture["week_number"] == 1
        assert results == ["2-1", "1-1", "0-2"]
        assert recalculation is not None

        after = await database.get_standings()
        assert after[0]["user_id"] == "user-1"
        assert after[0]["total_points"] == 16
        assert after[1]["user_id"] == "user-2"
        assert after[1]["total_points"] == 9

        async with (
            aiosqlite.connect(database.db_path) as conn,
            conn.execute(
                "SELECT COUNT(*) FROM results WHERE fixture_id = ?", (fixture1_id,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_correct_results_rolls_back_if_recalculation_fails(
        self,
        database,
        sample_games,
        monkeypatch,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["1-0", "1-1", "0-0"],
            False,
        )
        await database.save_results(fixture_id, ["1-0", "1-1", "0-0"])
        await service.calculate_fixture_scores(fixture_id)

        async def raise_recalc_error(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(results_module, "_recalculate_scores_in_connection", raise_recalc_error)

        with pytest.raises(RuntimeError, match="boom"):
            await service.correct_results(
                fixture_id,
                "Team A - Team B 2-1\nTeam C - Team D 2-2\nTeam E - Team F 1-0",
            )

        assert await database.get_results(fixture_id) == ["1-0", "1-1", "0-0"]


class TestPartialPredictionApproval:
    @pytest.mark.asyncio
    async def test_pending_partial_predictions_are_excluded_from_scoring(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            3, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(
            fixture_id,
            "111",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await database.save_prediction(
            fixture_id,
            "222",
            "Pending User",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        result = await service.calculate_fixture_scores(fixture_id)

        assert [score["user_id"] for score in result.scores] == ["111"]

    @pytest.mark.asyncio
    async def test_approved_partial_prediction_scores_only_selected_rows(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            4, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(
            fixture_id,
            "222",
            "Partial User",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        _fixture, approved_prediction, recalculation = await service.approve_partial_prediction(
            fixture_id,
            "222",
            "admin-1",
        )

        assert approved_prediction["pending_partial_approval"] is False
        assert recalculation is None

        result = await service.calculate_fixture_scores(fixture_id)

        assert result.scores[0]["user_id"] == "222"
        assert result.scores[0]["points"] == 6

    @pytest.mark.asyncio
    async def test_approve_partial_prediction_recalculates_scored_fixture(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            5, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(
            fixture_id,
            "111",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await service.calculate_fixture_scores(fixture_id)
        await database.save_prediction(
            fixture_id,
            "222",
            "Pending User",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        _fixture, approved_prediction, recalculation = await service.approve_partial_prediction(
            fixture_id,
            "222",
            "admin-1",
        )

        assert approved_prediction["pending_partial_approval"] is False
        assert recalculation is not None
        standings = await database.get_standings()
        assert {row["user_id"] for row in standings} == {"111", "222"}

    @pytest.mark.asyncio
    async def test_reject_partial_prediction_recalculates_scored_fixture(
        self,
        database,
        sample_games,
    ):
        service = AdminService(database)
        fixture_id = await database.create_fixture(
            6, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(
            fixture_id,
            "111",
            "Full User",
            ["2-1", "1-1", "0-2"],
            False,
        )
        await service.calculate_fixture_scores(fixture_id)
        await database.save_prediction(
            fixture_id,
            "222",
            "Pending User",
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        _fixture, prediction, recalculation = await service.reject_partial_prediction(
            fixture_id,
            "222",
        )

        assert prediction["pending_partial_approval"] is True
        assert recalculation is not None
        assert await database.get_prediction(fixture_id, "222") is None
