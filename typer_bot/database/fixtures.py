"""Fixture repository — CRUD for the fixtures table."""

import logging
import time

import aiosqlite

from typer_bot.utils import parse_iso
from typer_bot.utils.config import DB_PATH

logger = logging.getLogger(__name__)


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
            "week_number": row_dict.get("week_number"),
            "games": [g for g in games_text.split("\n") if g],
            "deadline": parse_iso(deadline_text) if deadline_text else None,
            "status": row_dict.get("status"),
            "message_id": row_dict.get("message_id"),
            "channel_id": row_dict.get("channel_id"),
        }

    async def create_fixture(self, week_number: int, games: list[str], deadline) -> int:
        """Create a new fixture and return its ID."""
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

    async def create_next_fixture(self, games: list[str], deadline) -> tuple[int, int]:
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

    async def get_fixture_by_week(self, week_number: int) -> dict | None:
        """Get the most recent fixture for a week, regardless of status."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures WHERE week_number = ? ORDER BY id DESC LIMIT 1",
                (week_number,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_fixture(row) if row else None

    async def get_recent_fixtures(self, limit: int = 25) -> list[dict]:
        """Get recent fixtures ordered by newest first."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM fixtures ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_fixture(row) for row in rows]

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
            return row[0] if row and row[0] is not None else 0

    async def delete_fixture(self, fixture_id: int) -> None:
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
