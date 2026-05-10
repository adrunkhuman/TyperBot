"""Season repository for guild-scoped league seasons."""

import aiosqlite

DEFAULT_SEASON_NAME = "Default Season"
ACTIVE_SEASON_STATUS = "active"
ARCHIVED_SEASON_STATUS = "archived"


def _validate_guild_id(guild_id: str) -> None:
    if not isinstance(guild_id, str) or not guild_id.strip():
        raise ValueError("guild_id is required")


def _row_to_season(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "name": row["name"],
        "status": row["status"],
        "created_at": row["created_at"],
        "ended_at": row["ended_at"],
    }


async def _repair_guild_config_active_season_in_connection(
    db: aiosqlite.Connection,
    guild_id: str,
    season_id: int,
) -> None:
    await db.execute(
        """
        UPDATE guild_config
        SET active_season_id = ?
        WHERE guild_id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM seasons s
              WHERE s.id = guild_config.active_season_id
                AND s.guild_id = guild_config.guild_id
                AND s.status = ?
          )
        """,
        (season_id, guild_id, ACTIVE_SEASON_STATUS),
    )


async def _get_active_season_in_connection(
    db: aiosqlite.Connection,
    guild_id: str,
) -> dict | None:
    async with db.execute(
        """
        SELECT s.*
        FROM guild_config gc
        JOIN seasons s ON s.id = gc.active_season_id
        WHERE gc.guild_id = ? AND s.guild_id = ? AND s.status = ?
        """,
        (guild_id, guild_id, ACTIVE_SEASON_STATUS),
    ) as cursor:
        row = await cursor.fetchone()
    if row is not None:
        return _row_to_season(row)

    async with db.execute(
        """
        SELECT *
        FROM seasons
        WHERE guild_id = ? AND status = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (guild_id, ACTIVE_SEASON_STATUS),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_season(row) if row else None


async def _create_default_season_in_connection(
    db: aiosqlite.Connection,
    guild_id: str,
) -> dict:
    cursor = await db.execute(
        """
        INSERT INTO seasons (guild_id, name, status)
        VALUES (?, ?, ?)
        """,
        (guild_id, DEFAULT_SEASON_NAME, ACTIVE_SEASON_STATUS),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("Failed to create season: lastrowid is None")
    season_id = cursor.lastrowid

    await _repair_guild_config_active_season_in_connection(db, guild_id, season_id)

    async with db.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Created season disappeared")
    return _row_to_season(row)


async def _get_or_create_active_season_in_connection(
    db: aiosqlite.Connection,
    guild_id: str,
) -> dict:
    prior_row_factory = db.row_factory
    db.row_factory = aiosqlite.Row
    try:
        season = await _get_active_season_in_connection(db, guild_id)
        if season is not None:
            await _repair_guild_config_active_season_in_connection(
                db,
                guild_id,
                season["id"],
            )
            return season
        return await _create_default_season_in_connection(db, guild_id)
    finally:
        db.row_factory = prior_row_factory


class SeasonRepository:
    """CRUD for seasons and active-season resolution."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def get_active_season(self, guild_id: str) -> dict | None:
        """Return the active season for one guild, if it exists."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            return await _get_active_season_in_connection(db, guild_id)

    async def get_or_create_active_season(self, guild_id: str) -> dict:
        """Return the active season, creating the default season when needed."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                season = await _get_or_create_active_season_in_connection(db, guild_id)
                await db.commit()
                return season
            except Exception:
                await db.rollback()
                raise

    async def get_seasons(self, guild_id: str) -> list[dict]:
        """List seasons for one guild."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM seasons WHERE guild_id = ? ORDER BY id ASC",
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_season(row) for row in rows]

    async def start_new_season(self, guild_id: str, name: str) -> dict:
        """Archive the current active season and create a new active season."""
        _validate_guild_id(guild_id)
        season_name = name.strip()
        if not season_name:
            raise ValueError("Season name is required.")

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                active_season = await _get_active_season_in_connection(db, guild_id)
                if active_season is not None:
                    async with db.execute(
                        "SELECT COUNT(*) FROM fixtures WHERE guild_id = ? AND season_id = ? AND status = 'open'",
                        (guild_id, active_season["id"]),
                    ) as cursor:
                        row = await cursor.fetchone()
                    open_count = int(row[0]) if row else 0
                    if open_count:
                        message = "Close all open fixtures before starting a new season."
                        raise ValueError(message)

                    await db.execute(
                        """
                        UPDATE seasons
                        SET status = ?, ended_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND guild_id = ? AND status = ?
                        """,
                        (
                            ARCHIVED_SEASON_STATUS,
                            active_season["id"],
                            guild_id,
                            ACTIVE_SEASON_STATUS,
                        ),
                    )

                cursor = await db.execute(
                    "INSERT INTO seasons (guild_id, name, status) VALUES (?, ?, ?)",
                    (guild_id, season_name, ACTIVE_SEASON_STATUS),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("Failed to create season: lastrowid is None")
                season_id = cursor.lastrowid

                await db.execute(
                    "UPDATE guild_config SET active_season_id = ?, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ?",
                    (season_id, guild_id),
                )

                async with db.execute("SELECT * FROM seasons WHERE id = ?", (season_id,)) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("Created season disappeared")

                await db.commit()
                return _row_to_season(row)
            except Exception:
                await db.rollback()
                raise
