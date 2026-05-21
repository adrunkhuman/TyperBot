import asyncio
from datetime import UTC, datetime

import aiosqlite
import pytest

from typer_bot.database import SaveResult
from typer_bot.database.predictions import PredictionRepository


class TestTrySavePrediction:
    """Atomic first-write-wins insert with fixture-open guard."""

    @pytest.mark.asyncio
    async def test_saved_when_fixture_open_and_no_prior_prediction(
        self, prediction_db, open_fixture_id
    ):
        result = await prediction_db.try_save_prediction(
            open_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_duplicate_when_prior_prediction_exists(self, prediction_db, open_fixture_id):
        await prediction_db.try_save_prediction(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.try_save_prediction(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.DUPLICATE
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_fixture_closed_returns_fixture_closed(self, prediction_db, closed_fixture_id):
        result = await prediction_db.try_save_prediction(
            closed_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.FIXTURE_CLOSED

    @pytest.mark.asyncio
    async def test_no_row_written_on_fixture_closed(self, prediction_db, closed_fixture_id):
        await prediction_db.try_save_prediction(closed_fixture_id, "u1", "User", ["2-1", "0-0"])
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1", "111111")
        assert prediction is None

    @pytest.mark.asyncio
    async def test_fixture_closed_checked_before_duplicate(self, prediction_db, closed_fixture_id):
        async with aiosqlite.connect(prediction_db.db_path) as conn:
            await conn.execute(
                "INSERT INTO predictions (fixture_id, user_id, user_name, predictions, is_late) VALUES (?, 'u1', 'User', '2-1', 0)",
                (closed_fixture_id,),
            )
            await conn.commit()
        result = await prediction_db.try_save_prediction(
            closed_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.FIXTURE_CLOSED

    @pytest.mark.asyncio
    async def test_concurrent_writers_allow_only_one_prediction(
        self, prediction_db, open_fixture_id
    ):
        async def save(user_name, predictions):
            return await prediction_db.try_save_prediction(
                open_fixture_id,
                "u1",
                user_name,
                predictions,
            )

        first, second = await asyncio.gather(
            save("First", ["2-1", "0-0"]),
            save("Second", ["3-0", "1-1"]),
        )

        assert sorted([first, second]) == [SaveResult.DUPLICATE, SaveResult.SAVED]

        async with (
            aiosqlite.connect(prediction_db.db_path) as conn,
            conn.execute(
                "SELECT COUNT(*), user_name, predictions FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (open_fixture_id, "u1"),
            ) as cursor,
        ):
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] == 1
        assert row[1] in {"First", "Second"}
        assert row[2] in {"2-1\n0-0", "3-0\n1-1"}


class TestPredictionSaveMetadata:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name",
        ["save_prediction", "try_save_prediction", "save_prediction_guarded"],
    )
    async def test_save_paths_preserve_non_default_metadata(
        self, prediction_db, open_fixture_id, method_name
    ):
        save = getattr(prediction_db, method_name)

        result = await save(
            open_fixture_id,
            "u1",
            "User",
            ["1-1"],
            True,
            predicted_game_indexes=[1],
            pending_partial_approval=True,
            public_message_id="message-1",
            public_message_kind="bot_post",
        )

        if method_name != "save_prediction":
            assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["user_name"] == "User"
        assert prediction["predictions"] == ["1-1"]
        assert prediction["is_late"] == 1
        assert prediction["predicted_game_indexes"] == [1]
        assert prediction["pending_partial_approval"] is True
        assert prediction["public_message_id"] == "message-1"
        assert prediction["public_message_kind"] == "bot_post"


class TestSavePredictionGuarded:
    """Upsert with fixture-open guard for prediction resubmission paths."""

    @pytest.mark.asyncio
    async def test_saved_when_fixture_open(self, prediction_db, open_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_fixture_closed_blocks_write(self, prediction_db, closed_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            closed_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.FIXTURE_CLOSED
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1", "111111")
        assert prediction is None

    @pytest.mark.asyncio
    async def test_allows_overwrite_of_existing_prediction(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["predictions"] == ["3-0", "1-1"]

    @pytest.mark.asyncio
    async def test_updates_user_name_on_resubmission(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "OldName", ["2-1", "0-0"]
        )
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "NewName", ["3-0", "1-1"]
        )
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["user_name"] == "NewName"

    @pytest.mark.asyncio
    async def test_resubmission_clears_admin_and_waiver_metadata(
        self, prediction_db, open_fixture_id
    ):
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["2-1", "0-0"], True
        )
        await prediction_db.set_late_penalty_waiver(open_fixture_id, "u1", True)
        await prediction_db.admin_update_prediction(
            open_fixture_id, "u1", ["2-0", "1-1"], "admin-1"
        )
        async with aiosqlite.connect(prediction_db.db_path) as conn:
            await conn.execute(
                "UPDATE predictions SET submitted_at = '2000-01-01T00:00:00+00:00' WHERE fixture_id = ? AND user_id = ?",
                (open_fixture_id, "u1"),
            )
            await conn.commit()

        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["3-0", "1-1"], False
        )

        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["late_penalty_waived"] == 0
        assert prediction["admin_edited_at"] is None
        assert prediction["admin_edited_by"] is None
        assert prediction["submitted_at"] > datetime(2000, 1, 1, tzinfo=UTC)


class TestPartialApprovalPending:
    @pytest.mark.asyncio
    async def test_clearing_pending_does_not_clear_late_status(
        self, prediction_db, open_fixture_id
    ):
        await prediction_db.save_prediction(
            open_fixture_id,
            "u1",
            "User",
            ["2-1", "0-0"],
            is_late=True,
            pending_partial_approval=True,
        )

        predictions = PredictionRepository(prediction_db.db_path)
        updated = await predictions.set_partial_approval_pending(open_fixture_id, "u1", False)
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")

        assert updated is True
        assert prediction is not None
        assert prediction["pending_partial_approval"] is False
        assert prediction["is_late"] == 1

    @pytest.mark.asyncio
    async def test_setting_pending_does_not_force_late_status(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction(
            open_fixture_id,
            "u1",
            "User",
            ["2-1", "0-0"],
            is_late=False,
            pending_partial_approval=False,
        )

        predictions = PredictionRepository(prediction_db.db_path)
        updated = await predictions.set_partial_approval_pending(open_fixture_id, "u1", True)
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")

        assert updated is True
        assert prediction is not None
        assert prediction["pending_partial_approval"] is True
        assert prediction["is_late"] == 0
