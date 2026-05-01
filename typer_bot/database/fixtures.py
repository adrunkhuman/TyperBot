"""Fixture repository — CRUD for the fixtures table."""

import logging
import time

import aiosqlite

from typer_bot.utils import parse_iso
from typer_bot.utils.config import DB_PATH

logger = logging.getLogger(__name__)


def _validate_guild_id(guild_id: str) -> None:
    if not isinstance(guild_id, str) or not guild_id.strip():
        raise ValueError("guild_id is required")


class FixtureRepository:
    """CRUD for the fixtures table."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or DB_PATH

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
            cursor = await db.execute(
                "INSERT INTO fixtures (guild_id, week_number, games, deadline) VALUES (?, ?, ?, ?)",
                (guild_id, week_number, "\n".join(games), deadline.isoformat()),
            )
            await db.commit()
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
                async with db.execute(
                    "SELECT COALESCE(MAX(week_number), 0) FROM fixtures WHERE guild_id = ?",
                    (guild_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    next_week = int(row[0]) + 1 if row else 1

                insert_cursor = await db.execute(
                    "INSERT INTO fixtures (guild_id, week_number, games, deadline) VALUES (?, ?, ?, ?)",
                    (guild_id, next_week, "\n".join(games), deadline.isoformat()),
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

    async def get_current_fixture(self, guild_id: str) -> dict | None:
        """Get the most recently created open fixture for a guild."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (guild_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_open_fixtures(self, guild_id: str) -> list[dict]:
        """Get open fixtures for a guild ordered by week and creation order."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND status = 'open' ORDER BY week_number ASC, id ASC",
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_all_open_fixtures(self) -> list[dict]:
        """Get all open fixtures for process-level background tasks."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE status = 'open' ORDER BY guild_id ASC, week_number ASC, id ASC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

    async def get_open_fixture_by_week(self, guild_id: str, week_number: int) -> dict | None:
        """Get an open fixture by guild and week number."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND status = 'open' AND week_number = ? ORDER BY id DESC LIMIT 1",
                (guild_id, week_number),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_fixture_by_id(self, fixture_id: int, guild_id: str) -> dict | None:
        """Get a fixture by ID, requiring guild ownership."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE id = ? AND guild_id = ?",
                (fixture_id, guild_id),
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
        """Get the most recent fixture for a guild week, regardless of status."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? AND week_number = ? ORDER BY id DESC LIMIT 1",
                (guild_id, week_number),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_recent_fixtures(self, guild_id: str, limit: int = 25) -> list[dict]:
        """Get recent fixtures for a guild ordered by newest first."""
        _validate_guild_id(guild_id)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
                (guild_id, limit),
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
                query = "SELECT * FROM fixtures WHERE message_id = ? AND status = 'open'"
                params = (message_id,)
            else:
                _validate_guild_id(guild_id)
                query = "SELECT * FROM fixtures WHERE guild_id = ? AND message_id = ? AND status = 'open'"
                params = (guild_id, message_id)
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_max_week_number(self, guild_id: str) -> int:
        """Get the maximum week number for a guild.

        Returns:
            Maximum week number, or 0 if no fixtures exist.
        """
        _validate_guild_id(guild_id)
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute(
                "SELECT MAX(week_number) FROM fixtures WHERE guild_id = ?",
                (guild_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            return row[0] if row and row[0] is not None else 0

    async def delete_fixture(self, fixture_id: int, guild_id: str | None = None) -> bool:
        """Delete a fixture and all associated data, optionally requiring guild ownership."""
        async with aiosqlite.connect(self.db_path) as db:
            if guild_id is not None:
                _validate_guild_id(guild_id)
                async with db.execute(
                    "SELECT 1 FROM fixtures WHERE id = ? AND guild_id = ?",
                    (fixture_id, guild_id),
                ) as cursor:
                    if await cursor.fetchone() is None:
                        return False
            await db.execute("DELETE FROM scores WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM results WHERE fixture_id = ?", (fixture_id,))
            await db.execute("DELETE FROM predictions WHERE fixture_id = ?", (fixture_id,))
            cursor = await db.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,))
            await db.commit()
            return cursor.rowcount > 0

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
