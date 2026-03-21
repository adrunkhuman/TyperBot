"""Tests for database operations and defensive coding patterns."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from typer_bot.database.database import Database, SaveResult


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


class TestOpenFixturesQueries:
    """Test suite for multi-open fixture query helpers."""

    @pytest.mark.asyncio
    async def test_get_open_fixtures_returns_all_open_ordered(self, temp_db_path):
        """Open fixtures are returned in week order for deterministic selection prompts."""
        db = Database(temp_db_path)
        await db.initialize()

        fixture_week_2 = await db.create_fixture(2, ["Team C - Team D"], datetime.now(UTC))
        fixture_week_1 = await db.create_fixture(1, ["Team A - Team B"], datetime.now(UTC))
        fixture_week_3 = await db.create_fixture(3, ["Team E - Team F"], datetime.now(UTC))

        # Close week 3 fixture so only weeks 1 and 2 remain open
        await db.save_scores(fixture_week_3, [])

        open_fixtures = await db.get_open_fixtures()
        open_ids = [fixture["id"] for fixture in open_fixtures]
        open_weeks = [fixture["week_number"] for fixture in open_fixtures]

        assert fixture_week_3 not in open_ids
        assert set(open_ids) == {fixture_week_1, fixture_week_2}
        assert open_weeks == [1, 2]

    @pytest.mark.asyncio
    async def test_get_open_fixture_by_week_ignores_closed_fixtures(self, temp_db_path):
        """Week resolver should only return fixtures that are still open."""
        db = Database(temp_db_path)
        await db.initialize()

        open_fixture_id = await db.create_fixture(7, ["Team A - Team B"], datetime.now(UTC))
        closed_fixture_id = await db.create_fixture(8, ["Team C - Team D"], datetime.now(UTC))
        await db.save_scores(closed_fixture_id, [])

        open_fixture = await db.get_open_fixture_by_week(7)
        closed_fixture = await db.get_open_fixture_by_week(8)

        assert open_fixture is not None
        assert open_fixture["id"] == open_fixture_id
        assert closed_fixture is None

    @pytest.mark.asyncio
    async def test_create_next_fixture_allocates_incrementing_weeks(self, temp_db_path):
        """Atomic allocator should issue increasing week numbers."""
        db = Database(temp_db_path)
        await db.initialize()

        fixture_one_id, week_one = await db.create_next_fixture(
            ["Team A - Team B"],
            datetime.now(UTC),
        )
        fixture_two_id, week_two = await db.create_next_fixture(
            ["Team C - Team D"],
            datetime.now(UTC),
        )

        fixture_one = await db.get_fixture_by_id(fixture_one_id)
        fixture_two = await db.get_fixture_by_id(fixture_two_id)

        assert week_one == 1
        assert week_two == 2
        assert fixture_one is not None
        assert fixture_one["week_number"] == 1
        assert fixture_two is not None
        assert fixture_two["week_number"] == 2


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
        await db.update_fixture_announcement(fixture_id, message_id="123456", channel_id="999")

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

    @pytest.mark.asyncio
    async def test_initialize_migrates_legacy_results_to_unique_latest_row(self, temp_db_path):
        """Legacy duplicate result rows should collapse to the newest saved value."""
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open'
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    predictions TEXT NOT NULL,
                    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_late BOOLEAN DEFAULT FALSE,
                    UNIQUE(fixture_id, user_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "INSERT INTO fixtures (id, week_number, games, deadline, status) VALUES (1, 1, 'A - B', ?, 'open')",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.execute(
                "INSERT INTO results (fixture_id, results, calculated_at) VALUES (1, '1-0', '2024-01-01T10:00:00+00:00')"
            )
            await conn.execute(
                "INSERT INTO results (fixture_id, results, calculated_at) VALUES (1, '2-0', '2024-01-01T12:00:00+00:00')"
            )
            await conn.commit()

        db = Database(temp_db_path)
        await db.initialize()

        assert await db.get_results(1) == ["2-0"]

        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute("SELECT COUNT(*) FROM results WHERE fixture_id = 1") as cursor,
        ):
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

        await db.save_results(1, ["3-0"])
        assert await db.get_results(1) == ["3-0"]

        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute("SELECT COUNT(*) FROM results WHERE fixture_id = 1") as cursor,
        ):
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_initialize_adds_prediction_override_columns_with_safe_defaults(
        self, temp_db_path
    ):
        """Legacy prediction rows should gain admin-override fields without mutating existing facts."""
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open'
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    predictions TEXT NOT NULL,
                    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_late BOOLEAN DEFAULT FALSE,
                    UNIQUE(fixture_id, user_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "INSERT INTO fixtures (id, week_number, games, deadline, status) VALUES (1, 1, 'A - B', ?, 'open')",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.execute(
                """
                INSERT INTO predictions (fixture_id, user_id, user_name, predictions, submitted_at, is_late)
                VALUES (1, 'user-1', 'User One', '1-0', '2024-01-01T10:00:00+00:00', 1)
                """
            )
            await conn.commit()

        db = Database(temp_db_path)
        await db.initialize()

        prediction = await db.get_prediction(1, "user-1")
        assert prediction is not None
        assert prediction["is_late"] == 1
        assert prediction["late_penalty_waived"] == 0
        assert prediction["admin_edited_at"] is None
        assert prediction["admin_edited_by"] is None


@pytest.fixture
async def prediction_db(temp_db_path):
    """Initialized Database for prediction-write tests."""
    database = Database(temp_db_path)
    await database.initialize()
    return database


@pytest.fixture
async def open_fixture_id(prediction_db):
    deadline = datetime.now(UTC) + timedelta(hours=1)
    return await prediction_db.create_fixture(1, ["A - B", "C - D"], deadline)


@pytest.fixture
async def closed_fixture_id(prediction_db):
    deadline = datetime.now(UTC) + timedelta(hours=1)
    fixture_id = await prediction_db.create_fixture(2, ["A - B", "C - D"], deadline)
    async with aiosqlite.connect(prediction_db.db_path) as conn:
        await conn.execute("UPDATE fixtures SET status = 'closed' WHERE id = ?", (fixture_id,))
        await conn.commit()
    return fixture_id


class TestTrySavePrediction:
    """Atomic first-write-wins insert with fixture-open guard."""

    @pytest.mark.asyncio
    async def test_saved_when_fixture_open_and_no_prior_prediction(
        self, prediction_db, open_fixture_id
    ):
        result = await prediction_db.try_save_prediction(
            open_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1")
        assert prediction is not None
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_duplicate_when_prior_prediction_exists(self, prediction_db, open_fixture_id):
        await prediction_db.try_save_prediction(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.try_save_prediction(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.DUPLICATE
        # Original prediction preserved
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1")
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_fixture_closed_returns_fixture_closed(self, prediction_db, closed_fixture_id):
        result = await prediction_db.try_save_prediction(
            closed_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.FIXTURE_CLOSED

    @pytest.mark.asyncio
    async def test_no_row_written_on_fixture_closed(self, prediction_db, closed_fixture_id):
        await prediction_db.try_save_prediction(closed_fixture_id, "u1", "User", ["2-1", "0-0"])
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1")
        assert prediction is None

    @pytest.mark.asyncio
    async def test_fixture_closed_checked_before_duplicate(self, prediction_db, closed_fixture_id):
        # Pre-seed a prediction so both conditions are true
        async with aiosqlite.connect(prediction_db.db_path) as conn:
            await conn.execute(
                "INSERT INTO predictions (fixture_id, user_id, user_name, predictions, is_late) VALUES (?, 'u1', 'User', '2-1', 0)",
                (closed_fixture_id,),
            )
            await conn.commit()
        result = await prediction_db.try_save_prediction(
            closed_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        # Fixture check runs first, so FIXTURE_CLOSED wins
        assert result == SaveResult.FIXTURE_CLOSED


class TestSavePredictionGuarded:
    """Upsert with fixture-open guard (DM re-submission path)."""

    @pytest.mark.asyncio
    async def test_saved_when_fixture_open(self, prediction_db, open_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1")
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_fixture_closed_blocks_write(self, prediction_db, closed_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            closed_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.FIXTURE_CLOSED
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1")
        assert prediction is None

    @pytest.mark.asyncio
    async def test_allows_overwrite_of_existing_prediction(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1")
        # New predictions persisted (distinguishes from try_save_prediction's duplicate guard)
        assert prediction["predictions"] == ["3-0", "1-1"]

    @pytest.mark.asyncio
    async def test_updates_user_name_on_resubmission(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "OldName", ["2-1", "0-0"]
        )
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "NewName", ["3-0", "1-1"]
        )
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1")
        assert prediction["user_name"] == "NewName"


class TestRowToFixture:
    """Test edge cases in _row_to_fixture deserialization."""

    @pytest.mark.asyncio
    async def test_empty_games_column_returns_empty_list(self, temp_db_path):
        """Empty games column must deserialize to [] not [''] (split artefact)."""
        db = Database(temp_db_path)
        await db.initialize()

        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "INSERT INTO fixtures (week_number, games, deadline, status) VALUES (?, ?, ?, ?)",
                (99, "", "2030-01-01T00:00:00+00:00", "open"),
            )
            await conn.commit()

        fixture = await db.get_current_fixture()
        assert fixture is not None
        assert fixture["games"] == []
