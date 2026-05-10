"""Score repository and shared in-connection helpers for cross-entity recalculation."""

import logging

import aiosqlite

from typer_bot.utils import build_fixture_scores

logger = logging.getLogger(__name__)


def _deserialize_game_indexes(raw: str | None, prediction_count: int) -> list[int]:
    if not raw:
        return list(range(prediction_count))
    return [int(part) for part in raw.split(",") if part != ""]


def _prediction_row_to_score_input(row: aiosqlite.Row) -> dict:
    prediction_values = row["predictions"].split("\n")
    return {
        "user_id": row["user_id"],
        "user_name": row["user_name"],
        "predictions": prediction_values,
        "predicted_game_indexes": _deserialize_game_indexes(
            row["predicted_game_indexes"],
            len(prediction_values),
        ),
        "is_late": bool(row["is_late"]),
        "late_penalty_waived": bool(row["late_penalty_waived"]),
    }


async def _fixture_has_scores_in_connection(db: aiosqlite.Connection, fixture_id: int) -> bool:
    async with db.execute(
        "SELECT 1 FROM scores WHERE fixture_id = ? LIMIT 1", (fixture_id,)
    ) as cursor:
        return await cursor.fetchone() is not None


async def _recalculate_scores_in_connection(db: aiosqlite.Connection, fixture_id: int) -> None:
    prior_row_factory = db.row_factory
    db.row_factory = aiosqlite.Row
    try:
        async with db.execute(
            """
            SELECT f.*
            FROM fixtures f
            JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
            WHERE f.id = ? AND s.status = 'active'
            """,
            (fixture_id,),
        ) as cursor:
            fixture_row = await cursor.fetchone()
        if fixture_row is None:
            raise ValueError("Fixture not found in active season")

        async with db.execute(
            "SELECT results FROM results WHERE fixture_id = ? ORDER BY id DESC LIMIT 1",
            (fixture_id,),
        ) as cursor:
            results_row = await cursor.fetchone()
        if results_row is None:
            raise ValueError("No results entered for this fixture")

        async with db.execute(
            "SELECT * FROM predictions WHERE fixture_id = ? AND pending_partial_approval = FALSE",
            (fixture_id,),
        ) as cursor:
            prediction_rows = await cursor.fetchall()
        if not prediction_rows:
            raise ValueError("No predictions found for this fixture")

        results = results_row["results"].split("\n")
        predictions = [_prediction_row_to_score_input(row) for row in prediction_rows]
        scores = build_fixture_scores(predictions, results)

        await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
        for score in scores:
            await db.execute(
                """
                INSERT INTO scores (fixture_id, user_id, user_name, points, exact_scores, correct_results)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture_id,
                    score["user_id"],
                    score["user_name"],
                    score["points"],
                    score["exact_scores"],
                    score["correct_results"],
                ),
            )
        await db.execute("UPDATE fixtures SET status = 'closed' WHERE id = ?", (fixture_id,))
    finally:
        db.row_factory = prior_row_factory


class ScoreRepository:
    """CRUD for the scores table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def fixture_has_scores(self, fixture_id: int) -> bool:
        """Return whether a fixture already has stored scores."""
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute(
                "SELECT 1 FROM scores WHERE fixture_id = ? LIMIT 1", (fixture_id,)
            ) as cursor,
        ):
            return await cursor.fetchone() is not None

    async def get_scores_for_fixture(self, fixture_id: int) -> list[dict]:
        """Get saved scores for a single fixture ordered by points."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT s.*, f.week_number
                FROM scores s
                JOIN fixtures f ON f.id = s.fixture_id
                WHERE s.fixture_id = ?
                ORDER BY s.points DESC, s.exact_scores DESC, s.correct_results DESC, s.user_name ASC
                """,
                (fixture_id,),
            ) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            return []

        return [
            {
                "user_id": row["user_id"],
                "user_name": row["user_name"],
                "points": row["points"],
                "exact_scores": row["exact_scores"],
                "correct_results": row["correct_results"],
            }
            for row in rows
        ]

    async def save_scores(self, fixture_id: int, scores: list[dict]) -> None:
        """Save calculated scores for a fixture atomically."""
        import time

        start_time = time.perf_counter()
        operation = "db.save_scores.transaction"

        logger.debug(
            "Transaction started",
            extra={
                "operation": operation,
                "fixture_id": fixture_id,
                "event_type": "transaction.begin",
            },
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT 1
                    FROM fixtures f
                    JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
                    WHERE f.id = ? AND s.status = 'active'
                    """,
                    (fixture_id,),
                ) as cursor:
                    if await cursor.fetchone() is None:
                        raise ValueError("Fixture not found in active season")

                await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
                for score in scores:
                    await db.execute(
                        """INSERT INTO scores (fixture_id, user_id, user_name, points,
                                              exact_scores, correct_results)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            fixture_id,
                            score["user_id"],
                            score["user_name"],
                            score["points"],
                            score["exact_scores"],
                            score["correct_results"],
                        ),
                    )
                await db.execute(
                    "UPDATE fixtures SET status = 'closed' WHERE id = ?", (fixture_id,)
                )
                await db.commit()

                duration_ms = (time.perf_counter() - start_time) * 1000
                logger.debug(
                    "Transaction committed",
                    extra={
                        "operation": operation,
                        "fixture_id": fixture_id,
                        "duration_ms": round(duration_ms, 2),
                        "scores_count": len(scores),
                        "event_type": "transaction.commit",
                    },
                )
            except Exception as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                try:
                    await db.rollback()
                    logger.debug(
                        "Transaction rolled back",
                        extra={
                            "operation": operation,
                            "fixture_id": fixture_id,
                            "duration_ms": round(duration_ms, 2),
                            "event_type": "transaction.rollback",
                        },
                    )
                except aiosqlite.Error as rb_err:
                    logger.warning(
                        "Rollback failed after save_scores error",
                        extra={
                            "operation": operation,
                            "fixture_id": fixture_id,
                            "rollback_error": str(rb_err),
                            "original_error": str(e),
                            "event_type": "transaction.rollback_failed",
                        },
                    )
                raise

    async def recalculate_fixture_scores(self, fixture_id: int) -> None:
        """Recalculate one fixture's scores atomically from stored results and predictions."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await _recalculate_scores_in_connection(db, fixture_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def get_standings(self, guild_id: str) -> list[dict]:
        """Get standings for one guild."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """WITH guild_scores AS (
                           SELECT s.*
                           FROM scores s
                           JOIN fixtures f ON f.id = s.fixture_id
                           JOIN seasons season ON season.id = f.season_id AND season.guild_id = f.guild_id
                           WHERE f.guild_id = ? AND season.status = 'active'
                       ),
                       latest_names AS (
                           SELECT user_id, user_name
                           FROM (
                               SELECT
                                   user_id,
                                   user_name,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY user_id
                                       ORDER BY fixture_id DESC
                                   ) AS row_number
                               FROM guild_scores
                           )
                           WHERE row_number = 1
                       )
                    SELECT
                          gs.user_id,
                          ln.user_name,
                          SUM(gs.points) as total_points,
                          SUM(gs.exact_scores) as total_exact,
                          SUM(gs.correct_results) as total_correct,
                          COUNT(DISTINCT gs.fixture_id) as weeks_played
                    FROM guild_scores gs
                    JOIN latest_names ln ON ln.user_id = gs.user_id
                    GROUP BY gs.user_id, ln.user_name
                    ORDER BY total_points DESC, total_exact DESC, total_correct DESC, ln.user_name ASC""",
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "user_id": row["user_id"],
                        "user_name": row["user_name"],
                        "total_points": row["total_points"],
                        "total_exact": row["total_exact"],
                        "total_correct": row["total_correct"],
                        "weeks_played": row["weeks_played"],
                    }
                    for row in rows
                ]

    async def get_last_fixture_scores(self, guild_id: str) -> dict | None:
        """Get scores from the most recently closed fixture in one guild."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT s.*, f.week_number
                   FROM scores s
                   JOIN fixtures f ON s.fixture_id = f.id
                   JOIN seasons season ON season.id = f.season_id AND season.guild_id = f.guild_id
                   WHERE f.status = 'closed' AND f.guild_id = ? AND season.status = 'active'
                   ORDER BY f.id DESC
                   LIMIT 1""",
                (guild_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    fixture_id = row["fixture_id"]
                    async with db.execute(
                        """
                         SELECT s.*
                         FROM scores s
                         JOIN fixtures f ON f.id = s.fixture_id
                          JOIN seasons season ON season.id = f.season_id AND season.guild_id = f.guild_id
                          WHERE s.fixture_id = ? AND f.guild_id = ? AND season.status = 'active'
                         ORDER BY points DESC, exact_scores DESC, correct_results DESC, user_name ASC
                         """,
                        (fixture_id, guild_id),
                    ) as cursor2:
                        scores = await cursor2.fetchall()
                        return {
                            "week_number": row["week_number"],
                            "fixture_id": fixture_id,
                            "scores": [
                                {
                                    "user_id": s["user_id"],
                                    "user_name": s["user_name"],
                                    "points": s["points"],
                                    "exact_scores": s["exact_scores"],
                                    "correct_results": s["correct_results"],
                                }
                                for s in scores
                            ],
                        }
                return None
