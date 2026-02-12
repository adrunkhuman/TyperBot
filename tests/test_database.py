"""Tests for database operations and defensive coding patterns."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from typer_bot.database.database import Database


@pytest.fixture
def temp_db_path():
    """Provide a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    # Cleanup
    Path(path).unlink(missing_ok=True)


class TestGetMaxWeekNumber:
    """Test suite for get_max_week_number method."""

    @pytest.mark.asyncio
    async def test_get_max_week_number_empty_db(self, temp_db_path):
        """Should return 0 when no fixtures exist."""
        db = Database(temp_db_path)
        await db.initialize()

        result = await db.get_max_week_number()
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_max_week_number_with_fixtures(self, temp_db_path):
        """Should return maximum week number from existing fixtures."""
        db = Database(temp_db_path)
        await db.initialize()

        # Create fixtures with various week numbers
        await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))
        await db.create_fixture(3, ["Team C - Team D"], datetime.now(UTC))
        await db.create_fixture(5, ["Team E - Team F"], datetime.now(UTC))

        result = await db.get_max_week_number()
        assert result == 5

    @pytest.mark.asyncio
    async def test_get_max_week_number_closed_fixtures(self, temp_db_path):
        """Should include closed fixtures in maximum calculation."""
        db = Database(temp_db_path)
        await db.initialize()

        # Create a fixture and close it
        fixture_id = await db.create_fixture(10, ["Team A - Team B"], datetime.now(UTC))
        await db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "123",
                    "user_name": "Test",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        # Create another fixture
        await db.create_fixture(5, ["Team C - Team D"], datetime.now(UTC))

        result = await db.get_max_week_number()
        assert result == 10


class TestDefensiveColumnAccess:
    """Test suite for defensive .get() column access patterns."""

    @pytest.mark.asyncio
    async def test_get_current_fixture_handles_missing_columns(self, temp_db_path):
        """Should gracefully handle missing optional columns using .get()."""
        db = Database(temp_db_path)
        await db.initialize()

        # Create a fixture
        await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))

        # This should not crash even if columns were missing
        fixture = await db.get_current_fixture()
        assert fixture is not None
        assert fixture["message_id"] is None

    @pytest.mark.asyncio
    async def test_get_fixture_by_id_handles_missing_columns(self, temp_db_path):
        """Should gracefully handle missing optional columns using .get()."""
        db = Database(temp_db_path)
        await db.initialize()

        # Create a fixture
        fixture_id = await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))

        # This should not crash even if columns were missing
        fixture = await db.get_fixture_by_id(fixture_id)
        assert fixture is not None
        assert fixture["message_id"] is None

    @pytest.mark.asyncio
    async def test_get_fixture_by_message_id_handles_missing_columns(self, temp_db_path):
        """Should gracefully handle missing optional columns using .get()."""
        db = Database(temp_db_path)
        await db.initialize()

        # Create a fixture with message_id
        fixture_id = await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))
        await db.update_fixture_announcement(fixture_id, message_id="123456")

        # This should not crash
        fixture = await db.get_fixture_by_message_id("123456")
        assert fixture is not None
        assert fixture["message_id"] == "123456"


class TestSchemaMigration:
    """Test suite for automatic schema migration."""

    @pytest.mark.asyncio
    async def test_initialize_adds_missing_columns(self, temp_db_path):
        """Should automatically add missing columns during initialization."""
        # Create a database with old schema (missing columns)
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("""
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.commit()

        db = Database(temp_db_path)

        # Initialize should add missing columns
        await db.initialize()

        # Verify columns were added by creating a fixture
        await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))
        fixture = await db.get_current_fixture()
        assert fixture is not None
        assert "message_id" in fixture
