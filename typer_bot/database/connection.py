"""Database composition root — schema initialisation and validation."""

import logging
from pathlib import Path

import aiosqlite

from typer_bot.utils.config import DB_PATH

from .fixtures import FixtureRepository
from .guild_config import GuildConfigRepository
from .predictions import PredictionRepository, SaveResult
from .results import ResultsRepository
from .scores import ScoreRepository
from .seasons import SeasonRepository

logger = logging.getLogger(__name__)

__all__ = ["Database", "SaveResult"]

REQUIRED_COLUMNS = {
    "seasons": {
        "id",
        "guild_id",
        "name",
        "status",
        "exact_score_points",
        "correct_outcome_points",
        "wrong_outcome_points",
        "late_prediction_points",
        "created_at",
        "ended_at",
    },
    "fixtures": {
        "id",
        "guild_id",
        "season_id",
        "week_number",
        "games",
        "deadline",
        "status",
        "message_id",
        "channel_id",
        "created_at",
    },
    "predictions": {
        "id",
        "fixture_id",
        "user_id",
        "user_name",
        "predictions",
        "submitted_at",
        "is_late",
        "late_penalty_waived",
        "admin_edited_at",
        "admin_edited_by",
        "predicted_game_indexes",
        "pending_partial_approval",
        "public_message_id",
        "public_message_kind",
    },
    "results": {"id", "fixture_id", "results", "calculated_at", "updated_at"},
    "scores": {
        "id",
        "fixture_id",
        "user_id",
        "user_name",
        "points",
        "exact_scores",
        "correct_results",
    },
    "guild_config": {
        "guild_id",
        "admin_role_id",
        "league_channel_id",
        "active_season_id",
        "created_at",
        "updated_at",
    },
}


async def _table_columns(db: aiosqlite.Connection, table_name: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        columns = await cursor.fetchall()
    return {col[1] for col in columns}


async def _has_existing_schema(db: aiosqlite.Connection) -> bool:
    async with db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ) as cursor:
        return await cursor.fetchone() is not None


async def _has_unique_index(
    db: aiosqlite.Connection,
    table_name: str,
    column_names: tuple[str, ...],
) -> bool:
    async with db.execute(f"PRAGMA index_list({table_name})") as cursor:
        indexes = await cursor.fetchall()

    for index in indexes:
        if not index[2] or index[4]:
            continue
        index_name = index[1]
        async with db.execute(f"PRAGMA index_info({index_name})") as cursor:
            index_columns = await cursor.fetchall()
        if tuple(row[2] for row in index_columns) == column_names:
            return True
    return False


async def _validate_fixture_guild_ownership(db: aiosqlite.Connection) -> None:
    columns = await _table_columns(db, "fixtures")
    if "guild_id" not in columns:
        raise RuntimeError(
            "fixtures.guild_id is missing. Manually port the database to the current schema before starting the bot."
        )

    async with db.execute(
        "SELECT COUNT(*) FROM fixtures WHERE guild_id IS NULL OR TRIM(guild_id) = ''"
    ) as cursor:
        row = await cursor.fetchone()
    if row and row[0] > 0:
        raise RuntimeError(
            "fixtures.guild_id has empty rows. Set every fixture to its owning Discord guild ID before starting the bot."
        )


async def _validate_fixture_season_ownership(db: aiosqlite.Connection) -> None:
    async with db.execute(
        """
        SELECT f.id
        FROM fixtures f
        LEFT JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
        WHERE f.season_id IS NULL OR s.id IS NULL
        ORDER BY f.id
        LIMIT 5
        """
    ) as cursor:
        rows = await cursor.fetchall()
    if not rows:
        return

    fixture_ids = ", ".join(str(row[0]) for row in rows)
    raise RuntimeError(
        "fixtures has rows without a valid same-guild season_id: "
        f"{fixture_ids}. Assign every fixture to a season owned by the same guild before starting the bot."
    )


async def _validate_current_schema(db: aiosqlite.Connection) -> None:
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        columns = await _table_columns(db, table_name)
        missing_columns = required_columns - columns
        if missing_columns:
            missing_list = ", ".join(f"{table_name}.{column}" for column in sorted(missing_columns))
            raise RuntimeError(
                f"Database schema is missing required column(s): {missing_list}. "
                "Manually port the database to the current schema before starting the bot."
            )

    required_unique_constraints = {
        "predictions": ("fixture_id", "user_id"),
        "scores": ("fixture_id", "user_id"),
    }
    for table_name, column_names in required_unique_constraints.items():
        if not await _has_unique_index(db, table_name, column_names):
            joined_columns = ", ".join(column_names)
            raise RuntimeError(
                f"Database schema is missing required unique constraint: {table_name}({joined_columns}). "
                "Manually port the database to the current schema before starting the bot."
            )


async def _validate_unique_results(db: aiosqlite.Connection) -> None:
    async with db.execute(
        """
        SELECT fixture_id, COUNT(*) AS row_count
        FROM results
        GROUP BY fixture_id
        HAVING row_count > 1
        ORDER BY fixture_id
        LIMIT 5
        """
    ) as cursor:
        rows = await cursor.fetchall()
    if not rows:
        return

    fixture_ids = ", ".join(str(row[0]) for row in rows)
    raise RuntimeError(
        "results has duplicate rows for fixture_id(s): "
        f"{fixture_ids}. Keep one result row per fixture before starting the bot."
    )


class Database:
    """Composition root for SQLite setup and repository wiring.

    Attributes:
        fixtures: Fixture reads/writes.
        guild_config: Guild setup reads/writes.
        predictions: Prediction reads/writes.
        results: Result reads/writes.
        scores: Score and standings reads/writes.
        seasons: Season and scoring-rule reads/writes.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DB_PATH

        db_dir = Path(self.db_path).parent
        if db_dir and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

        self.fixtures = FixtureRepository(self.db_path)
        self.guild_config = GuildConfigRepository(self.db_path)
        self.predictions = PredictionRepository(self.db_path)
        self.results = ResultsRepository(self.db_path)
        self.scores = ScoreRepository(self.db_path)
        self.seasons = SeasonRepository(self.db_path)

    async def initialize(self) -> None:
        """Create tables, enable WAL mode, and validate existing schema invariants.

        Fresh databases get the current schema. Existing databases must already
        match the current schema, except for explicit fail-fast checks that
        produce actionable errors for unsafe live data.

        Raises:
            RuntimeError: Existing databases must match the current schema and
                contain data safe for current unique constraints before startup
                can continue.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA journal_mode=WAL") as cur:
                row = await cur.fetchone()
                if row and row[0] != "wal":
                    logger.warning("WAL mode not applied; journal_mode=%s", row[0])
            has_existing_schema = await _has_existing_schema(db)
            if has_existing_schema:
                await _validate_current_schema(db)
                await _validate_fixture_guild_ownership(db)
                await _validate_fixture_season_ownership(db)
                await _validate_unique_results(db)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    exact_score_points INTEGER NOT NULL DEFAULT 3,
                    correct_outcome_points INTEGER NOT NULL DEFAULT 1,
                    wrong_outcome_points INTEGER NOT NULL DEFAULT 0,
                    late_prediction_points INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    ended_at DATETIME
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    season_id INTEGER,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    message_id TEXT,
                    channel_id TEXT,
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
                    active_season_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            if not has_existing_schema:
                await _validate_current_schema(db)
                await _validate_fixture_guild_ownership(db)
                await _validate_fixture_season_ownership(db)
                await _validate_unique_results(db)

            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_results_fixture_id_unique ON results(fixture_id)"
            )
            if not await _has_unique_index(db, "results", ("fixture_id",)):
                raise RuntimeError(
                    "Database schema is missing required unique constraint: results(fixture_id). "
                    "Manually port the database to the current schema before starting the bot."
                )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fixtures_guild_status_week ON fixtures(guild_id, status, week_number)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_fixtures_guild_week ON fixtures(guild_id, week_number)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_seasons_guild_status ON seasons(guild_id, status)"
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_seasons_one_active_per_guild ON seasons(guild_id) WHERE status = 'active'"
            )

            await db.commit()
