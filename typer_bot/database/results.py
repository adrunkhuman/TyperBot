"""Results repository — CRUD for the results table."""

import logging
import time

import aiosqlite

from .scores import _fixture_has_scores_in_connection, _recalculate_scores_in_connection

logger = logging.getLogger(__name__)


class ResultsRepository:
    """CRUD for the results table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def save_results(self, fixture_id: int, results: list[str]) -> None:
        """Save actual results for a fixture.

        Raises:
            ValueError: If the fixture is already scored or closed.
        """
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT status FROM fixtures WHERE id = ?", (fixture_id,)
                ) as cur:
                    row = await cur.fetchone()
                if row is None or row[0] == "closed":
                    raise ValueError("Fixture is already scored or does not exist.")
                if await _fixture_has_scores_in_connection(db, fixture_id):
                    raise ValueError("Fixture is already scored or does not exist.")
                cursor = await db.execute(
                    """
                    INSERT INTO results (fixture_id, results)
                    VALUES (?, ?)
                    ON CONFLICT(fixture_id)
                    DO UPDATE SET results = excluded.results,
                                  updated_at = CURRENT_TIMESTAMP
                    """,
                    (fixture_id, "\n".join(results)),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.save_results completed",
                extra={
                    "operation": "db.save_results",
                    "fixture_id": fixture_id,
                    "duration_ms": round(duration_ms, 2),
                    "rows_affected": cursor.rowcount,
                },
            )

    async def save_results_with_recalc(self, fixture_id: int, results: list[str]) -> bool:
        """Save results and refresh scores atomically when needed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    """
                    INSERT INTO results (fixture_id, results)
                    VALUES (?, ?)
                    ON CONFLICT(fixture_id)
                    DO UPDATE SET results = excluded.results,
                                  updated_at = CURRENT_TIMESTAMP
                    """,
                    (fixture_id, "\n".join(results)),
                )

                recalculated = False
                if await _fixture_has_scores_in_connection(db, fixture_id):
                    await _recalculate_scores_in_connection(db, fixture_id)
                    recalculated = True

                await db.commit()
                return recalculated
            except Exception:
                await db.rollback()
                raise

    async def get_results(self, fixture_id: int) -> list[str] | None:
        """Get results for a fixture."""
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute(
                "SELECT results FROM results WHERE fixture_id = ? ORDER BY id DESC LIMIT 1",
                (fixture_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return row[0].split("\n")
            return None
