"""Database composition root — schema initialisation and migrations."""

import logging
from pathlib import Path

import aiosqlite

from typer_bot.utils.config import DB_PATH

from .fixtures import FixtureRepository
from .guild_config import GuildConfigRepository
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

    timestamp_expr = (
        "COALESCE(calculated_at, CURRENT_TIMESTAMP)"
        if "calculated_at" in columns
        else "CURRENT_TIMESTAMP"
    )

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
            f"""
            INSERT INTO results_migrated (fixture_id, results, calculated_at, updated_at)
            SELECT fixture_id,
                   results,
                   {timestamp_expr},
                   {timestamp_expr}
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

    if "predicted_game_indexes" not in columns:
        logger.info("Adding predicted_game_indexes column to predictions table")
        await db.execute("ALTER TABLE predictions ADD COLUMN predicted_game_indexes TEXT")

    if "pending_partial_approval" not in columns:
        logger.info("Adding pending_partial_approval column to predictions table")
        await db.execute(
            "ALTER TABLE predictions ADD COLUMN pending_partial_approval BOOLEAN DEFAULT FALSE"
        )

    if "public_message_id" not in columns:
        logger.info("Adding public_message_id column to predictions table")
        await db.execute("ALTER TABLE predictions ADD COLUMN public_message_id TEXT")

    if "public_message_kind" not in columns:
        logger.info("Adding public_message_kind column to predictions table")
        await db.execute("ALTER TABLE predictions ADD COLUMN public_message_kind TEXT")


async def _validate_fixture_guild_ownership(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "fixtures")
    if "guild_id" not in columns:
        raise RuntimeError(
            "fixtures.guild_id is missing. Run the one-time v2.0.0 guild ownership migration before starting the bot."
        )

    async with db.execute(
        "SELECT COUNT(*) FROM fixtures WHERE guild_id IS NULL OR TRIM(guild_id) = ''"
    ) as cursor:
        row = await cursor.fetchone()
    if row and row[0] > 0:
        raise RuntimeError(
            "fixtures.guild_id has empty rows. Backfill every fixture with the owning Discord guild ID before starting the bot."
        )


class Database:
    """Composition root for SQLite setup and the bot's stable data facade.

    Callers use this facade instead of reaching into repositories directly. It
    owns path setup, schema initialization, additive migrations, and the focused
    repository objects that perform the actual reads and writes.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DB_PATH

        db_dir = Path(self.db_path).parent
        if db_dir and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

        self._fixtures = FixtureRepository(self.db_path)
        self._guild_config = GuildConfigRepository(self.db_path)
        self._predictions = PredictionRepository(self.db_path)
        self._results = ResultsRepository(self.db_path)
        self._scores = ScoreRepository(self.db_path)

    async def initialize(self) -> None:
        """Create tables, enable WAL mode, and apply additive migrations.

        Fresh databases get the current schema. Existing databases are migrated
        in place by adding missing columns and by collapsing legacy ``results``
        rows into the current one-row-per-fixture layout.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA journal_mode=WAL") as cur:
                row = await cur.fetchone()
                if row and row[0] != "wal":
                    logger.warning("WAL mode not applied; journal_mode=%s", row[0])
            await db.execute("""
                CREATE TABLE IF NOT EXISTS fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
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
                    predicted_game_indexes TEXT,
                    pending_partial_approval BOOLEAN DEFAULT FALSE,
                    public_message_id TEXT,
                    public_message_kind TEXT,
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

            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id TEXT PRIMARY KEY,
                    admin_role_id TEXT NOT NULL,
                    league_channel_id TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            column_names = await _table_columns(db, "fixtures")

            if "message_id" not in column_names:
                logger.info("Adding message_id column to fixtures table")
                await db.execute("ALTER TABLE fixtures ADD COLUMN message_id TEXT")

            if "channel_id" not in column_names:
                logger.info("Adding channel_id column to fixtures table")
                await db.execute("ALTER TABLE fixtures ADD COLUMN channel_id TEXT")

            await _validate_fixture_guild_ownership(db)

            await _migrate_prediction_columns(db)
            await _migrate_results_table(db)

            # Keep one results row per fixture on both fresh installs and upgraded DBs.
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_results_fixture_id_unique ON results(fixture_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fixtures_guild_status_week ON fixtures(guild_id, status, week_number)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fixtures_guild_week ON fixtures(guild_id, week_number)"
            )

            await db.commit()

    async def upsert_guild_config(self, guild_id, admin_role_id, league_channel_id):
        return await self._guild_config.upsert_guild_config(
            guild_id, admin_role_id, league_channel_id
        )

    async def get_guild_config(self, guild_id):
        return await self._guild_config.get_guild_config(guild_id)

    async def create_fixture(self, guild_id, week_number, games, deadline):
        return await self._fixtures.create_fixture(guild_id, week_number, games, deadline)

    async def create_next_fixture(self, guild_id, games, deadline):
        return await self._fixtures.create_next_fixture(guild_id, games, deadline)

    async def get_current_fixture(self, guild_id):
        return await self._fixtures.get_current_fixture(guild_id)

    async def get_open_fixtures(self, guild_id):
        return await self._fixtures.get_open_fixtures(guild_id)

    async def get_all_open_fixtures(self):
        return await self._fixtures.get_all_open_fixtures()

    async def get_open_fixture_by_week(self, guild_id, week_number):
        return await self._fixtures.get_open_fixture_by_week(guild_id, week_number)

    async def get_fixture_by_id(self, fixture_id, guild_id=None):
        return await self._fixtures.get_fixture_by_id(fixture_id, guild_id)

    async def get_fixture_by_week(self, guild_id, week_number):
        return await self._fixtures.get_fixture_by_week(guild_id, week_number)

    async def get_recent_fixtures(self, guild_id, limit=25):
        return await self._fixtures.get_recent_fixtures(guild_id, limit)

    async def get_fixture_by_message_id(self, message_id, guild_id=None):
        return await self._fixtures.get_fixture_by_message_id(message_id, guild_id)

    async def get_max_week_number(self, guild_id):
        return await self._fixtures.get_max_week_number(guild_id)

    async def delete_fixture(self, fixture_id, guild_id=None):
        return await self._fixtures.delete_fixture(fixture_id, guild_id)

    async def update_fixture_announcement(self, fixture_id, message_id, channel_id):
        return await self._fixtures.update_fixture_announcement(fixture_id, message_id, channel_id)

    async def save_prediction(
        self,
        fixture_id,
        user_id,
        user_name,
        predictions,
        is_late=False,
        *,
        predicted_game_indexes=None,
        pending_partial_approval=False,
        public_message_id=None,
        public_message_kind=None,
    ):
        return await self._predictions.save_prediction(
            fixture_id,
            user_id,
            user_name,
            predictions,
            is_late,
            predicted_game_indexes=predicted_game_indexes,
            pending_partial_approval=pending_partial_approval,
            public_message_id=public_message_id,
            public_message_kind=public_message_kind,
        )

    async def try_save_prediction(
        self,
        fixture_id,
        user_id,
        user_name,
        predictions,
        is_late=False,
        *,
        predicted_game_indexes=None,
        pending_partial_approval=False,
        public_message_id=None,
        public_message_kind=None,
    ):
        """Insert once for thread submissions with atomic duplicate and open checks."""
        return await self._predictions.try_save_prediction(
            fixture_id,
            user_id,
            user_name,
            predictions,
            is_late,
            predicted_game_indexes=predicted_game_indexes,
            pending_partial_approval=pending_partial_approval,
            public_message_id=public_message_id,
            public_message_kind=public_message_kind,
        )

    async def save_prediction_guarded(
        self,
        fixture_id,
        user_id,
        user_name,
        predictions,
        is_late=False,
        *,
        predicted_game_indexes=None,
        pending_partial_approval=False,
        public_message_id=None,
        public_message_kind=None,
    ):
        """Upsert a prediction only while the fixture is still open."""
        return await self._predictions.save_prediction_guarded(
            fixture_id,
            user_id,
            user_name,
            predictions,
            is_late,
            predicted_game_indexes=predicted_game_indexes,
            pending_partial_approval=pending_partial_approval,
            public_message_id=public_message_id,
            public_message_kind=public_message_kind,
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
        """Toggle late-waiver state and recalculate scores when they already exist."""
        return await self._predictions.toggle_late_penalty_waiver_with_recalc(fixture_id, user_id)

    async def delete_prediction(self, fixture_id, user_id):
        return await self._predictions.delete_prediction(fixture_id, user_id)

    async def get_all_predictions(self, fixture_id, include_pending=False):
        return await self._predictions.get_all_predictions(
            fixture_id, include_pending=include_pending
        )

    async def get_pending_partial_predictions(self, guild_id):
        return await self._predictions.get_pending_partial_predictions(guild_id)

    async def approve_partial_prediction(self, fixture_id, user_id, admin_user_id):
        return await self._predictions.approve_partial_prediction_with_recalc(
            fixture_id, user_id, admin_user_id
        )

    async def reject_partial_prediction(self, fixture_id, user_id):
        return await self._predictions.reject_partial_prediction_with_recalc(fixture_id, user_id)

    async def save_results(self, fixture_id, results):
        return await self._results.save_results(fixture_id, results)

    async def save_results_with_recalc(self, fixture_id, results):
        """Replace stored results and recalculate scores for already-scored fixtures."""
        return await self._results.save_results_with_recalc(fixture_id, results)

    async def get_results(self, fixture_id):
        return await self._results.get_results(fixture_id)

    async def fixture_has_scores(self, fixture_id):
        return await self._scores.fixture_has_scores(fixture_id)

    async def get_scores_for_fixture(self, fixture_id):
        return await self._scores.get_scores_for_fixture(fixture_id)

    async def save_scores(self, fixture_id, scores):
        return await self._scores.save_scores(fixture_id, scores)

    async def get_standings(self, guild_id):
        return await self._scores.get_standings(guild_id)

    async def get_last_fixture_scores(self, guild_id):
        return await self._scores.get_last_fixture_scores(guild_id)
