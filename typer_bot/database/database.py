"""SQLite database operations for the prediction bot."""

from datetime import datetime

import aiosqlite


class Database:
    """SQLite database wrapper for football predictions."""

    def __init__(self, db_path: str = "typer.db"):
        self.db_path = db_path

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

            await db.commit()

    async def create_fixture(self, week_number: int, games: list[str], deadline: datetime) -> int:
        """Create a new fixture and return its ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO fixtures (week_number, games, deadline) VALUES (?, ?, ?)",
                (week_number, "\n".join(games), deadline.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_current_fixture(self) -> dict | None:
        """Get the current open fixture."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE status = 'open' ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "week_number": row["week_number"],
                        "games": row["games"].split("\n"),
                        "deadline": datetime.fromisoformat(row["deadline"]),
                        "status": row["status"],
                    }
                return None

    async def get_fixture_by_id(self, fixture_id: int) -> dict | None:
        """Get a specific fixture by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "week_number": row["week_number"],
                        "games": row["games"].split("\n"),
                        "deadline": datetime.fromisoformat(row["deadline"]),
                        "status": row["status"],
                    }
                return None

    async def save_prediction(
        self,
        fixture_id: int,
        user_id: str,
        user_name: str,
        predictions: list[str],
        is_late: bool = False,
    ):
        """Save or update a user's predictions."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO predictions (fixture_id, user_id, user_name, predictions, is_late)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(fixture_id, user_id) 
                   DO UPDATE SET predictions = excluded.predictions, 
                                 is_late = excluded.is_late,
                                 submitted_at = CURRENT_TIMESTAMP""",
                (fixture_id, user_id, user_name, "\n".join(predictions), is_late),
            )
            await db.commit()

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
                        "submitted_at": datetime.fromisoformat(row["submitted_at"]),
                        "is_late": row["is_late"],
                    }
                return None

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
                        "submitted_at": datetime.fromisoformat(row["submitted_at"]),
                        "is_late": row["is_late"],
                    }
                    for row in rows
                ]

    async def save_results(self, fixture_id: int, results: list[str]):
        """Save actual results for a fixture."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results (fixture_id, results) VALUES (?, ?)",
                (fixture_id, "\n".join(results)),
            )
            await db.commit()

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
        """Save calculated scores for a fixture."""
        async with aiosqlite.connect(self.db_path) as db:
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
            await db.execute("UPDATE fixtures SET status = 'closed' WHERE id = ?", (fixture_id,))
            await db.commit()

    async def get_standings(self) -> list[dict]:
        """Get overall standings across all fixtures."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT user_id, user_name, 
                          SUM(points) as total_points,
                          SUM(exact_scores) as total_exact,
                          SUM(correct_results) as total_correct,
                          COUNT(DISTINCT fixture_id) as weeks_played
                   FROM scores 
                   GROUP BY user_id, user_name
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
                            "scores": [
                                {
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
            # Delete related records first (foreign key constraints)
            await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM results WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM predictions WHERE fixture_id = ?", (fixture_id,))
            # Delete the fixture
            await db.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,))
            await db.commit()
