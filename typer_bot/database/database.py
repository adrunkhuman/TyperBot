"""SQLite database operations for the prediction bot."""

import logging
import time
from datetime import datetime

import aiosqlite

from typer_bot.utils import parse_iso
from typer_bot.utils.config import DB_PATH

logger = logging.getLogger(__name__)


class Database:
    """SQLite database wrapper for football predictions."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH

        from pathlib import Path

        db_dir = Path(self.db_path).parent
        if db_dir and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    message_id TEXT,                 -- Discord message/ thread snowflake (threads share parent message ID)
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    predictions TEXT NOT NULL,
                    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_late BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (fixture_id) REFERENCES fixtures(id),
                    UNIQUE(fixture_id, user_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (fixture_id) REFERENCES fixtures(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    exact_scores INTEGER DEFAULT 0,
                    correct_results INTEGER DEFAULT 0,
                    FOREIGN KEY (fixture_id) REFERENCES fixtures(id),
                    UNIQUE(fixture_id, user_id)
                )
            """)

            # Migration: Add missing columns to fixtures table
            async with db.execute("PRAGMA table_info(fixtures)") as cursor:
                columns = await cursor.fetchall()
                column_names = {col[1] for col in columns}

            if "message_id" not in column_names:
                logger.info("Adding message_id column to fixtures table")
                await db.execute("ALTER TABLE fixtures ADD COLUMN message_id TEXT")

            await db.commit()

    async def create_fixture(self, week_number: int, games: list[str], deadline: datetime) -> int:
        """Create a new fixture and return its ID."""
        # Ensure deadline has timezone info before storing
        if deadline.tzinfo is None:
            from typer_bot.utils import APP_TZ

            deadline = deadline.replace(tzinfo=APP_TZ)
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO fixtures (week_number, games, deadline) VALUES (?, ?, ?)",
                (week_number, "\n".join(games), deadline.isoformat()),
            )
            await db.commit()
            # Runtime check for type narrowing (replaces assert)
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to create fixture: lastrowid is None")

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.create_fixture completed",
                extra={
                    "operation": "db.create_fixture",
                    "week_number": week_number,
                    "fixture_id": cursor.lastrowid,
                    "duration_ms": round(duration_ms, 2),
                    "games_count": len(games),
                },
            )
            return cursor.lastrowid

    async def create_next_fixture(self, games: list[str], deadline: datetime) -> tuple[int, int]:
        """Create a new fixture with the next available week number atomically.

        Returns:
            Tuple of (fixture_id, allocated_week_number).
        """
        if deadline.tzinfo is None:
            from typer_bot.utils import APP_TZ

            deadline = deadline.replace(tzinfo=APP_TZ)

        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT COALESCE(MAX(week_number), 0) FROM fixtures"
                ) as cursor:
                    row = await cursor.fetchone()
                    next_week = int(row[0]) + 1 if row else 1

                insert_cursor = await db.execute(
                    "INSERT INTO fixtures (week_number, games, deadline) VALUES (?, ?, ?)",
                    (next_week, "\n".join(games), deadline.isoformat()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

            if insert_cursor.lastrowid is None:
                raise RuntimeError("Failed to create fixture: lastrowid is None")

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.create_next_fixture completed",
                extra={
                    "operation": "db.create_next_fixture",
                    "week_number": next_week,
                    "fixture_id": insert_cursor.lastrowid,
                    "duration_ms": round(duration_ms, 2),
                    "games_count": len(games),
                },
            )
            return insert_cursor.lastrowid, next_week

    def _row_to_fixture(self, row: aiosqlite.Row) -> dict:
        """Convert database row to fixture dictionary."""
        row_dict = dict(row)
        deadline_val = row_dict.get("deadline")
        return {
            "id": row_dict.get("id"),
            "week_number": row_dict.get("week_number"),
            "games": row_dict.get("games", "").split("\n"),
            "deadline": parse_iso(deadline_val) if deadline_val else None,
            "status": row_dict.get("status"),
            "message_id": row_dict.get("message_id"),
        }

    async def get_current_fixture(self) -> dict | None:
        """Get the most recently created open fixture.

        Kept for backward compatibility with older call sites that assume a
        single active fixture.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE status = 'open' ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_open_fixtures(self) -> list[dict]:
        """Get all open fixtures ordered by week and creation order."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE status = 'open' ORDER BY week_number ASC, id ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_open_fixture_by_week(self, week_number: int) -> dict | None:
        """Get an open fixture by week number."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE status = 'open' AND week_number = ? ORDER BY id DESC LIMIT 1",
                (week_number,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_fixture_by_id(self, fixture_id: int) -> dict | None:
        """Get a specific fixture by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_fixture_by_message_id(self, message_id: str) -> dict | None:
        """Get a fixture by its Discord message ID.

        Args:
            message_id: Discord message/thread snowflake ID.
                Public threads share the same ID as their parent message.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE message_id = ? AND status = 'open'", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_max_week_number(self) -> int:
        """Get the maximum week number from all fixtures.

        Returns:
            Maximum week number, or 0 if no fixtures exist.
        """
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute("SELECT MAX(week_number) FROM fixtures") as cursor,
        ):
            row = await cursor.fetchone()
            # Runtime null check for type narrowing
            return row[0] if row and row[0] is not None else 0

    async def save_prediction(
        self,
        fixture_id: int,
        user_id: str,
        user_name: str,
        predictions: list[str],
        is_late: bool = False,
    ):
        """Save or update a user's predictions."""
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO predictions (fixture_id, user_id, user_name, predictions, is_late)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(fixture_id, user_id)
                   DO UPDATE SET predictions = excluded.predictions,
                                 is_late = excluded.is_late,
                                 submitted_at = CURRENT_TIMESTAMP""",
                (fixture_id, user_id, user_name, "\n".join(predictions), is_late),
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
                    return {
                        "user_id": row["user_id"],
                        "user_name": row["user_name"],
                        "predictions": row["predictions"].split("\n"),
                        "submitted_at": parse_iso(row["submitted_at"]),
                        "is_late": row["is_late"],
                    }
                return None

    async def delete_prediction(self, fixture_id: int, user_id: str) -> bool:
        """Delete a user's prediction for a fixture. Returns True if deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (fixture_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_all_predictions(self, fixture_id: int) -> list[dict]:
        """Get all predictions for a fixture."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM predictions WHERE fixture_id = ?", (fixture_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "user_id": row["user_id"],
                        "user_name": row["user_name"],
                        "predictions": row["predictions"].split("\n"),
                        "submitted_at": parse_iso(row["submitted_at"]),
                        "is_late": row["is_late"],
                    }
                    for row in rows
                ]

    async def save_results(self, fixture_id: int, results: list[str]):
        """Save actual results for a fixture."""
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT OR REPLACE INTO results (fixture_id, results) VALUES (?, ?)",
                (fixture_id, "\n".join(results)),
            )
            await db.commit()

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

    async def get_results(self, fixture_id: int) -> list[str] | None:
        """Get results for a fixture."""
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute("SELECT results FROM results WHERE fixture_id = ?", (fixture_id,)) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return row[0].split("\n")
            return None

    async def save_scores(self, fixture_id: int, scores: list[dict]):
        """Save calculated scores for a fixture atomically."""
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
            await db.execute("BEGIN")
            try:
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
            except aiosqlite.Error as e:
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

    async def get_standings(self) -> list[dict]:
        """Get overall standings across all fixtures."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT
                          s.user_id,
                          (SELECT user_name FROM scores s2
                           WHERE s2.user_id = s.user_id
                           ORDER BY fixture_id DESC LIMIT 1) as user_name,
                          SUM(s.points) as total_points,
                          SUM(s.exact_scores) as total_exact,
                          SUM(s.correct_results) as total_correct,
                          COUNT(DISTINCT s.fixture_id) as weeks_played
                   FROM scores s
                   GROUP BY s.user_id
                   ORDER BY total_points DESC"""
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

    async def get_last_fixture_scores(self) -> dict | None:
        """Get scores from the most recently closed fixture."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT s.*, f.week_number
                   FROM scores s
                   JOIN fixtures f ON s.fixture_id = f.id
                   WHERE f.status = 'closed'
                   ORDER BY f.id DESC
                   LIMIT 1"""
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    fixture_id = row["fixture_id"]
                    async with db.execute(
                        "SELECT * FROM scores WHERE fixture_id = ? ORDER BY points DESC",
                        (fixture_id,),
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

    async def delete_fixture(self, fixture_id: int):
        """Delete a fixture and all associated data."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM results WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM predictions WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,))
            await db.commit()

    async def update_fixture_announcement(
        self,
        fixture_id: int,
        message_id: str | None = None,
    ):
        """Update the Discord message ID for a fixture.

        Stores the parent message ID. When a thread is created from this
        message, it shares the same snowflake ID, so no separate thread_id
        column is needed.
        """
        async with aiosqlite.connect(self.db_path) as db:
            if message_id is not None:
                await db.execute(
                    "UPDATE fixtures SET message_id = ? WHERE id = ?", (message_id, fixture_id)
                )
                await db.commit()
