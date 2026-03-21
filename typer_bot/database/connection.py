"""Database composition root — schema initialisation and migrations."""

import logging
from pathlib import Path

import aiosqlite

from typer_bot.utils.config import DB_PATH

from .fixtures import FixtureRepository
from .predictions import PredictionRepository, SaveResult
from .results import ResultsRepository
from .scores import ScoreRepository

logger = logging.getLogger(__name__)

__all__ = ["Database", "SaveResult"]


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        columns = await cursor.fetchall()
    return {col[1] for col in columns}


async def _migrate_results_table(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "results")
    if not columns:
        return

    async with db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_results_fixture_id_unique'"
    ) as cursor:
        row = await cursor.fetchone()
        unique_index_exists = bool(row and row[0] > 0)

    if unique_index_exists:
        return

    logger.info("Migrating results table for deterministic result updates")
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute("DROP TABLE IF EXISTS results_migrated")
        await db.execute(
            """
            CREATE TABLE results_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id INTEGER NOT NULL,
                results TEXT NOT NULL,
                calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (fixture_id) REFERENCES fixtures(id)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO results_migrated (fixture_id, results, calculated_at, updated_at)
            SELECT fixture_id,
                   results,
                   COALESCE(calculated_at, CURRENT_TIMESTAMP),
                   COALESCE(calculated_at, CURRENT_TIMESTAMP)
            FROM results old
            WHERE old.id IN (
                SELECT MAX(id)
                FROM results
                GROUP BY fixture_id
            )
            """
        )
        await db.execute("DROP TABLE results")
        await db.execute("ALTER TABLE results_migrated RENAME TO results")
        await db.execute("CREATE UNIQUE INDEX idx_results_fixture_id_unique ON results(fixture_id)")
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _migrate_prediction_columns(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "predictions")

    if "late_penalty_waived" not in columns:
        logger.info("Adding late_penalty_waived column to predictions table")
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN late_penalty_waived BOOLEAN DEFAULT FALSE"
        )

    if "admin_edited_at" not in columns:
        logger.info("Adding admin_edited_at column to predictions table")
        await db.execute("ALTER TABLE predictions ADD COLUMN admin_edited_at DATETIME")

    if "admin_edited_by" not in columns:
        logger.info("Adding admin_edited_by column to predictions table")
        await db.execute("ALTER TABLE predictions ADD COLUMN admin_edited_by TEXT")


class Database:
    """Composition root: owns db_path, schema init, and delegates CRUD to focused repositories."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DB_PATH

        db_dir = Path(self.db_path).parent
        if db_dir and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

        self._fixtures = FixtureRepository(self.db_path)
        self._predictions = PredictionRepository(self.db_path)
        self._results = ResultsRepository(self.db_path)
        self._scores = ScoreRepository(self.db_path)

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA journal_mode=WAL") as cur:
                row = await cur.fetchone()
                if row and row[0] != "wal":
                    logger.warning("WAL mode not applied; journal_mode=%s", row[0])
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    message_id TEXT,
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
                    late_penalty_waived BOOLEAN DEFAULT FALSE,
                    admin_edited_at DATETIME,
                    admin_edited_by TEXT,
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
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
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

            column_names = await _table_columns(db, "fixtures")

            if "message_id" not in column_names:
                logger.info("Adding message_id column to fixtures table")
                await db.execute("ALTER TABLE fixtures ADD COLUMN message_id TEXT")

            if "channel_id" not in column_names:
                logger.info("Adding channel_id column to fixtures table")
                await db.execute("ALTER TABLE fixtures ADD COLUMN channel_id TEXT")

            await _migrate_prediction_columns(db)
            await _migrate_results_table(db)

            # Idempotent guard: migration creates this index on legacy databases; this covers
            # fresh installs where _migrate_results_table returns early.
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_results_fixture_id_unique ON results(fixture_id)"
            )

            await db.commit()

    # -- Fixture delegation --

    async def create_fixture(self, week_number, games, deadline):
        return await self._fixtures.create_fixture(week_number, games, deadline)

    async def create_next_fixture(self, games, deadline):
        return await self._fixtures.create_next_fixture(games, deadline)

    async def get_current_fixture(self):
        return await self._fixtures.get_current_fixture()

    async def get_open_fixtures(self):
        return await self._fixtures.get_open_fixtures()

    async def get_open_fixture_by_week(self, week_number):
        return await self._fixtures.get_open_fixture_by_week(week_number)

    async def get_fixture_by_id(self, fixture_id):
        return await self._fixtures.get_fixture_by_id(fixture_id)

    async def get_fixture_by_week(self, week_number):
        return await self._fixtures.get_fixture_by_week(week_number)

    async def get_recent_fixtures(self, limit=25):
        return await self._fixtures.get_recent_fixtures(limit)

    async def get_fixture_by_message_id(self, message_id):
        return await self._fixtures.get_fixture_by_message_id(message_id)

    async def get_max_week_number(self):
        return await self._fixtures.get_max_week_number()

    async def delete_fixture(self, fixture_id):
        return await self._fixtures.delete_fixture(fixture_id)

    async def update_fixture_announcement(self, fixture_id, message_id, channel_id):
        return await self._fixtures.update_fixture_announcement(fixture_id, message_id, channel_id)

    # -- Prediction delegation --

    async def save_prediction(self, fixture_id, user_id, user_name, predictions, is_late=False):
        return await self._predictions.save_prediction(
            fixture_id, user_id, user_name, predictions, is_late
        )

    async def try_save_prediction(self, fixture_id, user_id, user_name, predictions, is_late=False):
        return await self._predictions.try_save_prediction(
            fixture_id, user_id, user_name, predictions, is_late
        )

    async def save_prediction_guarded(
        self, fixture_id, user_id, user_name, predictions, is_late=False
    ):
        return await self._predictions.save_prediction_guarded(
            fixture_id, user_id, user_name, predictions, is_late
        )

    async def get_prediction(self, fixture_id, user_id):
        return await self._predictions.get_prediction(fixture_id, user_id)

    async def admin_update_prediction(self, fixture_id, user_id, predictions, admin_user_id):
        return await self._predictions.admin_update_prediction(
            fixture_id, user_id, predictions, admin_user_id
        )

    async def admin_update_prediction_with_recalc(
        self, fixture_id, user_id, predictions, admin_user_id
    ):
        return await self._predictions.admin_update_prediction_with_recalc(
            fixture_id, user_id, predictions, admin_user_id
        )

    async def set_late_penalty_waiver(self, fixture_id, user_id, waived):
        return await self._predictions.set_late_penalty_waiver(fixture_id, user_id, waived)

    async def toggle_late_penalty_waiver_with_recalc(self, fixture_id, user_id):
        return await self._predictions.toggle_late_penalty_waiver_with_recalc(fixture_id, user_id)

    async def delete_prediction(self, fixture_id, user_id):
        return await self._predictions.delete_prediction(fixture_id, user_id)

    async def get_all_predictions(self, fixture_id):
        return await self._predictions.get_all_predictions(fixture_id)

    # -- Results delegation --

    async def save_results(self, fixture_id, results):
        return await self._results.save_results(fixture_id, results)

    async def save_results_with_recalc(self, fixture_id, results):
        return await self._results.save_results_with_recalc(fixture_id, results)

    async def get_results(self, fixture_id):
        return await self._results.get_results(fixture_id)

    # -- Score delegation --

    async def fixture_has_scores(self, fixture_id):
        return await self._scores.fixture_has_scores(fixture_id)

    async def get_scores_for_fixture(self, fixture_id):
        return await self._scores.get_scores_for_fixture(fixture_id)

    async def save_scores(self, fixture_id, scores):
        return await self._scores.save_scores(fixture_id, scores)

    async def get_standings(self):
        return await self._scores.get_standings()

    async def get_last_fixture_scores(self):
        return await self._scores.get_last_fixture_scores()
