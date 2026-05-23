"""Fixture repository for guild-scoped fixture ownership."""

import logging
import time

import aiosqlite

from typer_bot.utils import parse_iso
from typer_bot.utils.config import DB_PATH

from .seasons import _get_active_season_in_connection, _get_or_create_active_season_in_connection

logger = logging.getLogger(__name__)


def _validate_guild_id(guild_id: str) -> None:
    if not isinstance(guild_id, str) or not guild_id.strip():
        raise ValueError("guild_id is required")


class FixtureRepository:
    """Read and write fixtures without crossing guild league boundaries.

    Public guild-scoped methods require a nonblank Discord guild ID and raise
    ``ValueError`` when it is missing.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DB_PATH

    async def _active_season_id(self, db: aiosqlite.Connection, guild_id: str) -> int | None:
        season = await _get_active_season_in_connection(db, guild_id)
        return season["id"] if season else None

    def _row_to_fixture(self, row: aiosqlite.Row) -> dict:
        row_dict = dict(row)
        games_val = row_dict.get("games")
        games_text = games_val if isinstance(games_val, str) else ""
        deadline_val = row_dict.get("deadline")
        deadline_text = deadline_val if isinstance(deadline_val, str) else None
        return {
            "id": row_dict.get("id"),
            "guild_id": row_dict.get("guild_id"),
            "week_number": row_dict.get("week_number"),
            "season_id": row_dict.get("season_id"),
            "games": [g for g in games_text.split("\n") if g],
            "deadline": parse_iso(deadline_text) if deadline_text else None,
            "status": row_dict.get("status"),
            "message_id": row_dict.get("message_id"),
            "channel_id": row_dict.get("channel_id"),
        }

    async def create_fixture(
        self,
        guild_id: str,
        week_number: int,
        games: list[str],
        deadline,
    ) -> int:
        """Create a new fixture and return its ID."""
        _validate_guild_id(guild_id)
        if deadline.tzinfo is None:
            from typer_bot.utils import APP_TZ

            deadline = deadline.replace(tzinfo=APP_TZ)
        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                season = await _get_or_create_active_season_in_connection(db, guild_id)
                cursor = await db.execute(
                    "INSERT INTO fixtures (guild_id, season_id, week_number, games, deadline) VALUES (?, ?, ?, ?, ?)",
                    (
                        guild_id,
                        season["id"],
                        week_number,
                        "\n".join(games),
                        deadline.isoformat(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            if cursor.lastrowid is None:
                raise RuntimeError("Failed to create fixture: lastrowid is None")

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.fixtures.create_fixture completed",
                extra={
                    "operation": "db.fixtures.create_fixture",
                    "week_number": week_number,
                    "fixture_id": cursor.lastrowid,
                    "duration_ms": round(duration_ms, 2),
                    "games_count": len(games),
                },
            )
            return cursor.lastrowid

    async def create_next_fixture(
        self, guild_id: str, games: list[str], deadline
    ) -> tuple[int, int]:
        """Create a new fixture with the next available week number atomically.

        Returns:
            Tuple of (fixture_id, allocated_week_number).
        """
        _validate_guild_id(guild_id)
        if deadline.tzinfo is None:
            from typer_bot.utils import APP_TZ

            deadline = deadline.replace(tzinfo=APP_TZ)

        start_time = time.perf_counter()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                season = await _get_or_create_active_season_in_connection(db, guild_id)
                async with db.execute(
                    "SELECT COALESCE(MAX(week_number), 0) FROM fixtures WHERE guild_id = ? AND season_id = ?",
                    (guild_id, season["id"]),
                ) as cursor:
                    row = await cursor.fetchone()
                    next_week = int(row[0]) + 1 if row else 1

                insert_cursor = await db.execute(
                    "INSERT INTO fixtures (guild_id, season_id, week_number, games, deadline) VALUES (?, ?, ?, ?, ?)",
                    (guild_id, season["id"], next_week, "\n".join(games), deadline.isoformat()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

            if insert_cursor.lastrowid is None:
                raise RuntimeError("Failed to create fixture: lastrowid is None")

            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.debug(
                "db.fixtures.create_next_fixture completed",
                extra={
                    "operation": "db.fixtures.create_next_fixture",
                    "week_number": next_week,
                    "fixture_id": insert_cursor.lastrowid,
                    "duration_ms": round(duration_ms, 2),
                    "games_count": len(games),
                },
            )
            return insert_cursor.lastrowid, next_week

    async def get_current_fixture(self, guild_id: str) -> dict | None:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return None
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (guild_id, season_id),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_open_fixtures(self, guild_id: str) -> list[dict]:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return []
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? AND status = 'open' ORDER BY week_number ASC, id ASC",
                (guild_id, season_id),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_all_open_fixtures(self) -> list[dict]:
        """Get all open fixtures for process-level background tasks."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT f.*
                FROM fixtures f
                JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
                WHERE f.status = 'open' AND s.status = 'active'
                ORDER BY f.guild_id ASC, f.week_number ASC, f.id ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_open_fixture_by_week(self, guild_id: str, week_number: int) -> dict | None:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return None
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? AND status = 'open' AND week_number = ? ORDER BY id DESC LIMIT 1",
                (guild_id, season_id, week_number),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_fixture_by_id(self, fixture_id: int, guild_id: str) -> dict | None:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return None
            async with db.execute(
                "SELECT * FROM fixtures WHERE id = ? AND guild_id = ? AND season_id = ?",
                (fixture_id, guild_id, season_id),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def _get_fixture_by_id_unchecked(self, fixture_id: int) -> dict | None:
        """Get a fixture by ID for process-level/internal paths that have no guild context."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_fixture_by_week(self, guild_id: str, week_number: int) -> dict | None:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return None
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? AND week_number = ? ORDER BY id DESC LIMIT 1",
                (guild_id, season_id, week_number),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_recent_fixtures(self, guild_id: str, limit: int = 25) -> list[dict]:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return []
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? ORDER BY id DESC LIMIT ?",
                (guild_id, season_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_fixture_by_message_id(
        self, message_id: str, guild_id: str | None = None
    ) -> dict | None:
        """Get a fixture by its Discord message ID.

        Args:
            message_id: Discord message/thread snowflake ID.
                Public threads share the same ID as their parent message.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if guild_id is None:
                query = """
                    SELECT f.*
                    FROM fixtures f
                    JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
                    WHERE f.message_id = ? AND f.status = 'open' AND s.status = 'active'
                """
                params = (message_id,)
            else:
                _validate_guild_id(guild_id)
                season_id = await self._active_season_id(db, guild_id)
                if season_id is None:
                    return None
                query = "SELECT * FROM fixtures WHERE guild_id = ? AND season_id = ? AND message_id = ? AND status = 'open'"
                params = (guild_id, season_id, message_id)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_max_week_number(self, guild_id: str) -> int:
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            season_id = await self._active_season_id(db, guild_id)
            if season_id is None:
                return 0
            async with db.execute(
                "SELECT MAX(week_number) FROM fixtures WHERE guild_id = ? AND season_id = ?",
                (guild_id, season_id),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row and row[0] is not None else 0

    async def delete_fixture(self, fixture_id: int, guild_id: str | None = None) -> bool:
        """Delete a fixture and all associated data, optionally requiring guild ownership."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if guild_id is not None:
                    _validate_guild_id(guild_id)
                    async with db.execute(
                        """
                        SELECT 1
                        FROM fixtures f
                        JOIN seasons s ON s.id = f.season_id AND s.guild_id = f.guild_id
                        WHERE f.id = ? AND f.guild_id = ? AND s.status = 'active'
                        """,
                        (fixture_id, guild_id),
                    ) as cursor:
                        if await cursor.fetchone() is None:
                            await db.rollback()
                            return False
                await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
                await db.execute("DELETE FROM results WHERE fixture_id = ?", (fixture_id,))
                await db.execute("DELETE FROM predictions WHERE fixture_id = ?", (fixture_id,))
                if guild_id is None:
                    cursor = await db.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,))
                else:
                    cursor = await db.execute(
                        """
                        DELETE FROM fixtures
                        WHERE id = ? AND guild_id = ?
                          AND EXISTS (
                              SELECT 1
                              FROM seasons s
                              WHERE s.id = fixtures.season_id
                                AND s.guild_id = fixtures.guild_id
                                AND s.status = 'active'
                          )
                        """,
                        (fixture_id, guild_id),
                    )
                await db.commit()
                return cursor.rowcount > 0
            except Exception:
                await db.rollback()
                raise

    async def update_fixture_announcement(
        self,
        fixture_id: int,
        message_id: str,
        channel_id: str,
    ) -> None:
        """Store the announcement message and channel IDs after posting to Discord."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE fixtures SET message_id = ?, channel_id = ? WHERE id = ?",
                (message_id, channel_id, fixture_id),
            )
            await db.commit()
