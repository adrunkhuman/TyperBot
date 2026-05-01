"""Prediction repository — CRUD for the predictions table."""

import logging
import time
from enum import StrEnum

import aiosqlite

from typer_bot.utils import parse_iso

from .scores import _fixture_has_scores_in_connection, _recalculate_scores_in_connection

logger = logging.getLogger(__name__)


def _serialize_game_indexes(game_indexes: list[int] | None, prediction_count: int) -> str | None:
    if game_indexes is None:
        game_indexes = list(range(prediction_count))
    return ",".join(str(index) for index in game_indexes)


def _deserialize_game_indexes(raw: str | None, prediction_count: int) -> list[int]:
    if not raw:
        return list(range(prediction_count))
    return [int(part) for part in raw.split(",") if part != ""]


class SaveResult(StrEnum):
    """Result of an atomic prediction save attempt."""

    SAVED = "saved"
    DUPLICATE = "duplicate"  # first-write-wins: prior prediction exists
    FIXTURE_CLOSED = "fixture_closed"  # fixture closed between handler read and write


class PredictionRepository:
    """CRUD for the predictions table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @staticmethod
    def _prediction_row_to_dict(row: aiosqlite.Row) -> dict:
        prediction_values = row["predictions"].split("\n")
        return {
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "predictions": prediction_values,
            "submitted_at": parse_iso(row["submitted_at"]),
            "is_late": row["is_late"],
            "late_penalty_waived": row["late_penalty_waived"],
            "predicted_game_indexes": _deserialize_game_indexes(
                row["predicted_game_indexes"],
                len(prediction_values),
            ),
            "pending_partial_approval": bool(row["pending_partial_approval"]),
            "admin_edited_at": parse_iso(row["admin_edited_at"])
            if row["admin_edited_at"]
            else None,
            "admin_edited_by": row["admin_edited_by"],
            "public_message_id": row["public_message_id"],
            "public_message_kind": row["public_message_kind"],
        }

    async def save_prediction(
        self,
        fixture_id: int,
        user_id: str,
        user_name: str,
        predictions: list[str],
        is_late: bool = False,
        *,
        predicted_game_indexes: list[int] | None = None,
        pending_partial_approval: bool = False,
        public_message_id: str | None = None,
        public_message_kind: str | None = None,
    ) -> None:
        """Save or update a user's predictions."""
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO predictions (
                       fixture_id,
                       user_id,
                       user_name,
                       predictions,
                       is_late,
                       predicted_game_indexes,
                       pending_partial_approval,
                       public_message_id,
                       public_message_kind
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(fixture_id, user_id)
                     DO UPDATE SET predictions = excluded.predictions,
                                    user_name = excluded.user_name,
                                    is_late = excluded.is_late,
                                    predicted_game_indexes = excluded.predicted_game_indexes,
                                    pending_partial_approval = excluded.pending_partial_approval,
                                    public_message_id = excluded.public_message_id,
                                    public_message_kind = excluded.public_message_kind,
                                    late_penalty_waived = FALSE,
                                    admin_edited_at = NULL,
                                    admin_edited_by = NULL,
                                   submitted_at = CURRENT_TIMESTAMP""",
                (
                    fixture_id,
                    user_id,
                    user_name,
                    "\n".join(predictions),
                    is_late,
                    _serialize_game_indexes(predicted_game_indexes, len(predictions)),
                    pending_partial_approval,
                    public_message_id,
                    public_message_kind,
                ),
            )
            await db.commit()

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.save_prediction completed",
                extra={
                    "operation": "db.save_prediction",
                    "fixture_id": fixture_id,
                    "user_id": user_id,
                    "duration_ms": round(duration_ms, 2),
                    "rows_affected": cursor.rowcount,
                    "is_late": is_late,
                },
            )

    async def try_save_prediction(
        self,
        fixture_id: int,
        user_id: str,
        user_name: str,
        predictions: list[str],
        is_late: bool = False,
        *,
        predicted_game_indexes: list[int] | None = None,
        pending_partial_approval: bool = False,
        public_message_id: str | None = None,
        public_message_kind: str | None = None,
    ) -> SaveResult:
        """Insert a prediction atomically with first-write-wins and fixture-open guards.

        Executes both checks and the INSERT inside a single BEGIN IMMEDIATE transaction,
        so no concurrent writer can slip between the guards and the write.

        Args:
            fixture_id: ID of the fixture to predict.
            user_id: Discord user snowflake.
            user_name: Display name at submission time.
            predictions: List of score strings.
            is_late: Whether submission is past the deadline.

        Returns:
            SaveResult.SAVED if written successfully.
            SaveResult.FIXTURE_CLOSED if fixture was no longer open at write time.
            SaveResult.DUPLICATE if a prior prediction already exists.
        """
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT 1 FROM fixtures WHERE id = ? AND status = 'open'", (fixture_id,)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        await db.rollback()
                        return SaveResult.FIXTURE_CLOSED

                async with db.execute(
                    "SELECT 1 FROM predictions WHERE fixture_id = ? AND user_id = ?",
                    (fixture_id, user_id),
                ) as cursor:
                    if await cursor.fetchone() is not None:
                        await db.rollback()
                        return SaveResult.DUPLICATE

                await db.execute(
                    """INSERT INTO predictions (
                           fixture_id,
                           user_id,
                           user_name,
                           predictions,
                           is_late,
                           predicted_game_indexes,
                           pending_partial_approval,
                           public_message_id,
                           public_message_kind
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fixture_id,
                        user_id,
                        user_name,
                        "\n".join(predictions),
                        is_late,
                        _serialize_game_indexes(predicted_game_indexes, len(predictions)),
                        pending_partial_approval,
                        public_message_id,
                        public_message_kind,
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "db.try_save_prediction completed",
            extra={
                "operation": "db.try_save_prediction",
                "fixture_id": fixture_id,
                "user_id": user_id,
                "duration_ms": round(duration_ms, 2),
                "is_late": is_late,
            },
        )
        return SaveResult.SAVED

    async def save_prediction_guarded(
        self,
        fixture_id: int,
        user_id: str,
        user_name: str,
        predictions: list[str],
        is_late: bool = False,
        *,
        predicted_game_indexes: list[int] | None = None,
        pending_partial_approval: bool = False,
        public_message_id: str | None = None,
        public_message_kind: str | None = None,
    ) -> SaveResult:
        """Upsert a prediction, but only when the fixture is still open.

        Unlike try_save_prediction(), allows overwriting an existing prediction
        (intentional re-submission via `/predict` or admin correction paths).
        Returns FIXTURE_CLOSED if the fixture was closed between the caller's
        read and this write; SAVED otherwise.

        Args:
            fixture_id: ID of the fixture to predict.
            user_id: Discord user snowflake.
            user_name: Display name at submission time.
            predictions: List of score strings.
            is_late: Whether submission is past the deadline.

        Returns:
            SaveResult.SAVED if written successfully.
            SaveResult.FIXTURE_CLOSED if fixture was no longer open at write time.
        """
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT 1 FROM fixtures WHERE id = ? AND status = 'open'", (fixture_id,)
                ) as cursor:
                    if await cursor.fetchone() is None:
                        await db.rollback()
                        return SaveResult.FIXTURE_CLOSED

                await db.execute(
                    """INSERT INTO predictions (
                           fixture_id,
                           user_id,
                           user_name,
                           predictions,
                           is_late,
                           predicted_game_indexes,
                           pending_partial_approval,
                           public_message_id,
                           public_message_kind
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(fixture_id, user_id)
                        DO UPDATE SET predictions = excluded.predictions,
                                      user_name = excluded.user_name,
                                      is_late = excluded.is_late,
                                      predicted_game_indexes = excluded.predicted_game_indexes,
                                      pending_partial_approval = excluded.pending_partial_approval,
                                      public_message_id = excluded.public_message_id,
                                      public_message_kind = excluded.public_message_kind,
                                      late_penalty_waived = FALSE,
                                      admin_edited_at = NULL,
                                      admin_edited_by = NULL,
                                      submitted_at = CURRENT_TIMESTAMP""",
                    (
                        fixture_id,
                        user_id,
                        user_name,
                        "\n".join(predictions),
                        is_late,
                        _serialize_game_indexes(predicted_game_indexes, len(predictions)),
                        pending_partial_approval,
                        public_message_id,
                        public_message_kind,
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "db.save_prediction_guarded completed",
            extra={
                "operation": "db.save_prediction_guarded",
                "fixture_id": fixture_id,
                "user_id": user_id,
                "duration_ms": round(duration_ms, 2),
                "is_late": is_late,
            },
        )
        return SaveResult.SAVED

    async def get_prediction(self, fixture_id: int, user_id: str) -> dict | None:
        """Get a user's predictions for a fixture."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (fixture_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._prediction_row_to_dict(row)
                return None

    async def admin_update_prediction(
        self,
        fixture_id: int,
        user_id: str,
        predictions: list[str],
        admin_user_id: str,
    ) -> bool:
        """Replace a stored prediction without changing original submission timing."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE predictions
                SET predictions = ?,
                    admin_edited_at = CURRENT_TIMESTAMP,
                    admin_edited_by = ?
                WHERE fixture_id = ? AND user_id = ?
                """,
                ("\n".join(predictions), admin_user_id, fixture_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def admin_update_prediction_with_recalc(
        self,
        fixture_id: int,
        user_id: str,
        predictions: list[str],
        admin_user_id: str,
    ) -> bool:
        """Replace a stored prediction and refresh scores atomically when needed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                    UPDATE predictions
                    SET predictions = ?,
                        predicted_game_indexes = ?,
                        pending_partial_approval = FALSE,
                        admin_edited_at = CURRENT_TIMESTAMP,
                        admin_edited_by = ?
                    WHERE fixture_id = ? AND user_id = ?
                    """,
                    (
                        "\n".join(predictions),
                        _serialize_game_indexes(None, len(predictions)),
                        admin_user_id,
                        fixture_id,
                        user_id,
                    ),
                )
                if cursor.rowcount <= 0:
                    await db.rollback()
                    return False

                if await _fixture_has_scores_in_connection(db, fixture_id):
                    await _recalculate_scores_in_connection(db, fixture_id)

                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

    async def set_late_penalty_waiver(
        self,
        fixture_id: int,
        user_id: str,
        waived: bool,
    ) -> bool:
        """Set late-penalty waiver for an existing prediction."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE predictions
                SET late_penalty_waived = ?
                WHERE fixture_id = ? AND user_id = ?
                """,
                (waived, fixture_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def toggle_late_penalty_waiver_with_recalc(
        self,
        fixture_id: int,
        user_id: str,
    ) -> bool | None:
        """Toggle late waiver and refresh scores atomically when needed."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT late_penalty_waived FROM predictions WHERE fixture_id = ? AND user_id = ?",
                    (fixture_id, user_id),
                ) as cursor:
                    row = await cursor.fetchone()

                if row is None:
                    await db.rollback()
                    return None

                waived = not bool(row["late_penalty_waived"])
                cursor = await db.execute(
                    """
                    UPDATE predictions
                    SET late_penalty_waived = ?
                    WHERE fixture_id = ? AND user_id = ?
                    """,
                    (waived, fixture_id, user_id),
                )
                if cursor.rowcount <= 0:
                    await db.rollback()
                    return None

                if await _fixture_has_scores_in_connection(db, fixture_id):
                    await _recalculate_scores_in_connection(db, fixture_id)

                await db.commit()
                return waived
            except Exception:
                await db.rollback()
                raise

    async def delete_prediction(self, fixture_id: int, user_id: str) -> bool:
        """Delete a user's prediction for a fixture. Returns True if deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (fixture_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def set_partial_approval_pending(
        self,
        fixture_id: int,
        user_id: str,
        pending: bool,
    ) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE predictions SET pending_partial_approval = ?, is_late = ? WHERE fixture_id = ? AND user_id = ?",
                (pending, pending, fixture_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def approve_partial_prediction_with_recalc(
        self,
        fixture_id: int,
        user_id: str,
        admin_user_id: str,
    ) -> bool:
        """Approve a pending partial prediction and recalculate if needed.

        Approval clears `pending_partial_approval`, resets `is_late`, and marks
        the row as admin-edited. If the fixture already has scores, standings are
        recalculated in the same transaction.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                    UPDATE predictions
                    SET pending_partial_approval = FALSE,
                        is_late = FALSE,
                        late_penalty_waived = FALSE,
                        admin_edited_at = CURRENT_TIMESTAMP,
                        admin_edited_by = ?
                    WHERE fixture_id = ? AND user_id = ? AND pending_partial_approval = TRUE
                    """,
                    (admin_user_id, fixture_id, user_id),
                )
                if cursor.rowcount <= 0:
                    await db.rollback()
                    return False

                if await _fixture_has_scores_in_connection(db, fixture_id):
                    await _recalculate_scores_in_connection(db, fixture_id)

                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

    async def reject_partial_prediction_with_recalc(
        self,
        fixture_id: int,
        user_id: str,
    ) -> bool:
        """Reject and delete a pending partial prediction.

        If the fixture already has scores, standings are recalculated in the same
        transaction after the pending row is removed.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "DELETE FROM predictions WHERE fixture_id = ? AND user_id = ? AND pending_partial_approval = TRUE",
                    (fixture_id, user_id),
                )
                if cursor.rowcount <= 0:
                    await db.rollback()
                    return False

                if await _fixture_has_scores_in_connection(db, fixture_id):
                    await _recalculate_scores_in_connection(db, fixture_id)

                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

    async def get_all_predictions(
        self, fixture_id: int, *, include_pending: bool = False
    ) -> list[dict]:
        """Get fixture predictions, excluding pending partials by default.

        Scoring paths rely on the default behavior so pending partial approvals do
        not count until an admin explicitly approves them.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                (
                    "SELECT * FROM predictions WHERE fixture_id = ?"
                    if include_pending
                    else "SELECT * FROM predictions WHERE fixture_id = ? AND pending_partial_approval = FALSE"
                ),
                (fixture_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._prediction_row_to_dict(row) for row in rows]

    async def get_pending_partial_predictions(self, guild_id: str) -> list[dict]:
        """List pending partial predictions with fixture metadata for one guild."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    p.fixture_id,
                    p.user_id,
                    p.user_name,
                    p.predictions,
                    p.predicted_game_indexes,
                    p.is_late,
                    p.late_penalty_waived,
                    p.pending_partial_approval,
                    p.public_message_id,
                    p.public_message_kind,
                    f.guild_id,
                    f.week_number,
                    f.games,
                    f.status
                FROM predictions p
                JOIN fixtures f ON f.id = p.fixture_id
                WHERE p.pending_partial_approval = TRUE AND f.guild_id = ?
                ORDER BY f.week_number ASC, p.user_name COLLATE NOCASE ASC
                """,
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "fixture_id": row["fixture_id"],
                        "user_id": row["user_id"],
                        "user_name": row["user_name"],
                        "predictions": row["predictions"].split("\n"),
                        "predicted_game_indexes": _deserialize_game_indexes(
                            row["predicted_game_indexes"],
                            len(row["predictions"].split("\n")),
                        ),
                        "is_late": row["is_late"],
                        "late_penalty_waived": row["late_penalty_waived"],
                        "pending_partial_approval": bool(row["pending_partial_approval"]),
                        "public_message_id": row["public_message_id"],
                        "public_message_kind": row["public_message_kind"],
                        "guild_id": row["guild_id"],
                        "week_number": row["week_number"],
                        "games": row["games"].split("\n"),
                        "status": row["status"],
                    }
                    for row in rows
                ]
