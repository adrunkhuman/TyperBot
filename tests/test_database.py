"""Tests for database operations and defensive coding patterns."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from typer_bot.database import Database, SaveResult
from typer_bot.database import scores as scores_module


@pytest.fixture
def temp_db_path():
    """Provide a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


class TestGetMaxWeekNumber:
    """Test suite for get_max_week_number method."""

    @pytest.mark.asyncio
    async def test_get_max_week_number_empty_db(self, temp_db_path):
        """Should return 0 when no fixtures exist."""
        db = Database(temp_db_path)
        await db.initialize()

        result = await db.get_max_week_number("111111")
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_max_week_number_with_fixtures(self, temp_db_path):
        """Should return maximum week number from existing fixtures."""
        db = Database(temp_db_path)
        await db.initialize()

        await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        await db.create_fixture("111111", 3, ["Team C - Team D"], datetime.now(UTC))
        await db.create_fixture("111111", 5, ["Team E - Team F"], datetime.now(UTC))

        result = await db.get_max_week_number("111111")
        assert result == 5

    @pytest.mark.asyncio
    async def test_get_max_week_number_closed_fixtures(self, temp_db_path):
        """Should include closed fixtures in maximum calculation."""
        db = Database(temp_db_path)
        await db.initialize()

        fixture_id = await db.create_fixture("111111", 10, ["Team A - Team B"], datetime.now(UTC))
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

        await db.create_fixture("111111", 5, ["Team C - Team D"], datetime.now(UTC))

        result = await db.get_max_week_number("111111")
        assert result == 10


class TestGuildConfig:
    @pytest.mark.asyncio
    async def test_guild_config_persists_and_updates(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        assert await db.get_guild_config("111111") is None

        await db.upsert_guild_config("111111", "role-1", "channel-1")
        config = await db.get_guild_config("111111")
        assert config["admin_role_id"] == "role-1"
        assert config["league_channel_id"] == "channel-1"

        await db.upsert_guild_config("111111", "role-2", "channel-2")
        updated = await db.get_guild_config("111111")
        assert updated["admin_role_id"] == "role-2"
        assert updated["league_channel_id"] == "channel-2"

    @pytest.mark.asyncio
    async def test_guild_config_is_per_guild(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        await db.upsert_guild_config("111111", "role-1", "channel-1")
        await db.upsert_guild_config("222222", "role-2", "channel-2")

        guild_one = await db.get_guild_config("111111")
        guild_two = await db.get_guild_config("222222")
        assert guild_one["admin_role_id"] == "role-1"
        assert guild_two["admin_role_id"] == "role-2"


class TestScores:
    @pytest.mark.asyncio
    async def test_save_scores_does_not_mutate_when_write_lock_is_held(
        self, temp_db_path, monkeypatch
    ):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        await db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        real_connect = scores_module.aiosqlite.connect

        def connect_with_short_timeout(*args, **kwargs):
            kwargs.setdefault("timeout", 0.05)
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(scores_module.aiosqlite, "connect", connect_with_short_timeout)
        async with aiosqlite.connect(temp_db_path) as locked_conn:
            await locked_conn.execute("BEGIN IMMEDIATE")

            with pytest.raises(aiosqlite.OperationalError, match="locked"):
                await db.save_scores(
                    fixture_id,
                    [
                        {
                            "user_id": "user-2",
                            "user_name": "User Two",
                            "points": 9,
                            "exact_scores": 3,
                            "correct_results": 3,
                        }
                    ],
                )

            await locked_conn.rollback()

        scores = await db.get_scores_for_fixture(fixture_id)
        assert [score["user_id"] for score in scores] == ["user-1"]

    @pytest.mark.asyncio
    async def test_save_scores_rolls_back_after_partial_write_failure(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        await db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        with pytest.raises(aiosqlite.IntegrityError):
            await db.save_scores(
                fixture_id,
                [
                    {
                        "user_id": "user-2",
                        "user_name": "User Two",
                        "points": 9,
                        "exact_scores": 3,
                        "correct_results": 3,
                    },
                    {
                        "user_id": "user-2",
                        "user_name": "Duplicate User Two",
                        "points": 0,
                        "exact_scores": 0,
                        "correct_results": 0,
                    },
                ],
            )

        scores = await db.get_scores_for_fixture(fixture_id)
        assert len(scores) == 1
        assert scores[0]["user_id"] == "user-1"
        assert scores[0]["points"] == 3

    @pytest.mark.asyncio
    async def test_standings_order_by_points_tiebreakers_and_name(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))

        await db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "total",
                    "user_name": "Total",
                    "points": 10,
                    "exact_scores": 0,
                    "correct_results": 0,
                },
                {
                    "user_id": "exact",
                    "user_name": "Exact",
                    "points": 9,
                    "exact_scores": 2,
                    "correct_results": 0,
                },
                {
                    "user_id": "correct",
                    "user_name": "Correct",
                    "points": 9,
                    "exact_scores": 1,
                    "correct_results": 3,
                },
                {
                    "user_id": "alpha",
                    "user_name": "Alpha",
                    "points": 9,
                    "exact_scores": 1,
                    "correct_results": 2,
                },
                {
                    "user_id": "beta",
                    "user_name": "Beta",
                    "points": 9,
                    "exact_scores": 1,
                    "correct_results": 2,
                },
            ],
        )

        standings = await db.get_standings("111111")

        assert [row["user_id"] for row in standings] == [
            "total",
            "exact",
            "correct",
            "alpha",
            "beta",
        ]


class TestOpenFixturesQueries:
    """Test suite for multi-open fixture query helpers."""

    @pytest.mark.asyncio
    async def test_get_open_fixtures_returns_all_open_ordered(self, temp_db_path):
        """Open fixtures are returned in week order for deterministic selection prompts."""
        db = Database(temp_db_path)
        await db.initialize()

        fixture_week_2 = await db.create_fixture(
            "111111", 2, ["Team C - Team D"], datetime.now(UTC)
        )
        fixture_week_1 = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        fixture_week_3 = await db.create_fixture(
            "111111", 3, ["Team E - Team F"], datetime.now(UTC)
        )

        # Close week 3 fixture so only weeks 1 and 2 remain open
        await db.save_scores(fixture_week_3, [])

        await db.create_fixture("guild-2", 1, ["Other A - Other B"], datetime.now(UTC))

        open_fixtures = await db.get_open_fixtures("111111")
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

        open_fixture_id = await db.create_fixture(
            "111111", 7, ["Team A - Team B"], datetime.now(UTC)
        )
        closed_fixture_id = await db.create_fixture(
            "111111", 8, ["Team C - Team D"], datetime.now(UTC)
        )
        await db.save_scores(closed_fixture_id, [])

        open_fixture = await db.get_open_fixture_by_week("111111", 7)
        closed_fixture = await db.get_open_fixture_by_week("111111", 8)

        assert open_fixture is not None
        assert open_fixture["id"] == open_fixture_id
        assert closed_fixture is None

    @pytest.mark.asyncio
    async def test_week_and_recent_fixture_queries_are_guild_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        guild_one_week = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        guild_two_week = await db.create_fixture(
            "guild-2", 1, ["Team C - Team D"], datetime.now(UTC)
        )

        assert (await db.get_fixture_by_week("111111", 1))["id"] == guild_one_week
        assert (await db.get_fixture_by_week("guild-2", 1))["id"] == guild_two_week
        assert [fixture["id"] for fixture in await db.get_recent_fixtures("111111")] == [
            guild_one_week
        ]
        assert await db.get_fixture_by_id(guild_two_week, "111111") is None

    @pytest.mark.asyncio
    async def test_delete_fixture_can_require_guild_ownership(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        fixture_id = await db.create_fixture("guild-2", 1, ["Team A - Team B"], datetime.now(UTC))

        assert await db.delete_fixture(fixture_id, "111111") is False
        assert await db.get_fixture_by_id(fixture_id) is not None

        assert await db.delete_fixture(fixture_id, "guild-2") is True
        assert await db.get_fixture_by_id(fixture_id) is None

    @pytest.mark.asyncio
    async def test_pending_partial_predictions_are_guild_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        deadline = datetime.now(UTC) - timedelta(hours=1)
        guild_one_fixture_id = await db.create_fixture(
            "111111", 1, ["Team A - Team B", "Team C - Team D"], deadline
        )
        guild_two_fixture_id = await db.create_fixture(
            "guild-2", 1, ["Team E - Team F", "Team G - Team H"], deadline
        )
        await db.save_prediction(
            guild_one_fixture_id,
            "guild-one-user",
            "Guild One",
            ["1-1"],
            True,
            predicted_game_indexes=[0],
            pending_partial_approval=True,
        )
        await db.save_prediction(
            guild_two_fixture_id,
            "guild-two-user",
            "Guild Two",
            ["2-2"],
            True,
            predicted_game_indexes=[1],
            pending_partial_approval=True,
        )

        guild_one_pending = await db.get_pending_partial_predictions("111111")
        guild_two_pending = await db.get_pending_partial_predictions("guild-2")

        assert [prediction["user_id"] for prediction in guild_one_pending] == ["guild-one-user"]
        assert [prediction["user_id"] for prediction in guild_two_pending] == ["guild-two-user"]

    @pytest.mark.asyncio
    async def test_create_next_fixture_allocates_incrementing_weeks(self, temp_db_path):
        """Atomic allocator should issue increasing week numbers."""
        db = Database(temp_db_path)
        await db.initialize()

        fixture_one_id, week_one = await db.create_next_fixture(
            "111111",
            ["Team A - Team B"],
            datetime.now(UTC),
        )
        fixture_two_id, week_two = await db.create_next_fixture(
            "111111",
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

    @pytest.mark.asyncio
    async def test_created_fixtures_store_guild_ownership(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        fixture_id = await db.create_fixture(
            "guild-2",
            4,
            ["Team A - Team B"],
            datetime.now(UTC),
        )

        fixture = await db.get_fixture_by_id(fixture_id)
        assert fixture is not None
        assert fixture["guild_id"] == "guild-2"

    @pytest.mark.asyncio
    async def test_create_fixture_rejects_missing_guild_id(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        with pytest.raises(ValueError, match="guild_id is required"):
            await db.create_fixture("", 1, ["Team A - Team B"], datetime.now(UTC))

    @pytest.mark.asyncio
    async def test_create_next_fixture_rejects_missing_guild_id(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        with pytest.raises(ValueError, match="guild_id is required"):
            await db.create_next_fixture("", ["Team A - Team B"], datetime.now(UTC))

    @pytest.mark.asyncio
    async def test_create_next_fixture_allocates_weeks_per_guild(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        _, guild_one_week = await db.create_next_fixture(
            "111111",
            ["Team A - Team B"],
            datetime.now(UTC),
        )
        _, guild_two_week = await db.create_next_fixture(
            "guild-2",
            ["Team C - Team D"],
            datetime.now(UTC),
        )
        guild_one_second_id = await db.create_fixture(
            "111111",
            2,
            ["Team E - Team F"],
            datetime.now(UTC),
        )
        await db.create_fixture(
            "guild-2",
            9,
            ["Team G - Team H"],
            datetime.now(UTC),
        )

        assert guild_one_week == 1
        assert guild_two_week == 1
        assert await db.get_max_week_number("111111") == 2
        assert await db.get_max_week_number("guild-2") == 9

        guild_one_second = await db.get_fixture_by_id(guild_one_second_id)
        assert guild_one_second is not None
        assert guild_one_second["guild_id"] == "111111"


class TestSchemaMigration:
    """Test suite for automatic schema migration."""

    @pytest.mark.asyncio
    async def test_initialize_rejects_fixtures_without_guild_ownership(self, temp_db_path):
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
            await conn.commit()

        db = Database(temp_db_path)

        with pytest.raises(RuntimeError, match="fixtures.guild_id is missing"):
            await db.initialize()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("guild_id", [None, "", "   "])
    async def test_initialize_rejects_blank_fixture_guild_ownership(self, temp_db_path, guild_id):
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open'
                )
                """
            )
            await conn.execute(
                "INSERT INTO fixtures (guild_id, week_number, games, deadline, status) VALUES (?, 1, 'A - B', ?, 'open')",
                (guild_id, datetime.now(UTC).isoformat()),
            )
            await conn.commit()

        db = Database(temp_db_path)

        with pytest.raises(RuntimeError, match="fixtures.guild_id has empty rows"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_preserves_manually_backfilled_legacy_fixture_graph(
        self, temp_db_path
    ):
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    message_id TEXT
                );
                CREATE TABLE predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    predictions TEXT NOT NULL,
                    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_late BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL
                );
                CREATE TABLE scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    exact_scores INTEGER DEFAULT 0,
                    correct_results INTEGER DEFAULT 0
                );
                """
            )
            await conn.execute(
                "INSERT INTO fixtures (id, week_number, games, deadline, status, message_id) VALUES (1, 1, 'A - B', ?, 'closed', '789012')",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.execute(
                "INSERT INTO predictions (fixture_id, user_id, user_name, predictions, submitted_at, is_late) VALUES (1, 'user-1', 'User One', '2-1', ?, 0)",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.execute("INSERT INTO results (fixture_id, results) VALUES (1, '2-1')")
            await conn.execute(
                "INSERT INTO scores (fixture_id, user_id, user_name, points, exact_scores, correct_results) VALUES (1, 'user-1', 'User One', 3, 1, 0)"
            )
            await conn.execute("ALTER TABLE fixtures ADD COLUMN guild_id TEXT")
            await conn.execute("UPDATE fixtures SET guild_id = '111111'")
            await conn.commit()

        db = Database(temp_db_path)
        await db.initialize()

        fixture = await db.get_fixture_by_id(1, "111111")
        other_guild_fixture = await db.get_fixture_by_id(1, "222222")
        prediction = await db.get_prediction(1, "user-1")
        results = await db.get_results(1)
        standings = await db.get_standings("111111")
        other_guild_standings = await db.get_standings("222222")

        assert fixture is not None
        assert other_guild_fixture is None
        assert prediction["predictions"] == ["2-1"]
        assert results == ["2-1"]
        assert [row["user_id"] for row in standings] == ["user-1"]
        assert other_guild_standings == []

    @pytest.mark.asyncio
    async def test_initialize_adds_missing_columns(self, temp_db_path):
        """Should automatically add missing columns during initialization."""
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("""
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.commit()

        db = Database(temp_db_path)

        await db.initialize()

        await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        fixture = await db.get_current_fixture("111111")
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
                    guild_id TEXT NOT NULL,
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
                "INSERT INTO fixtures (id, guild_id, week_number, games, deadline, status) VALUES (1, '111111', 1, 'A - B', ?, 'open')",
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
                    guild_id TEXT NOT NULL,
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
                "INSERT INTO fixtures (id, guild_id, week_number, games, deadline, status) VALUES (1, '111111', 1, 'A - B', ?, 'open')",
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
    return await prediction_db.create_fixture("111111", 1, ["A - B", "C - D"], deadline)


@pytest.fixture
async def closed_fixture_id(prediction_db):
    deadline = datetime.now(UTC) + timedelta(hours=1)
    fixture_id = await prediction_db.create_fixture("111111", 2, ["A - B", "C - D"], deadline)
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
        async with aiosqlite.connect(prediction_db.db_path) as conn:
            await conn.execute(
                "INSERT INTO predictions (fixture_id, user_id, user_name, predictions, is_late) VALUES (?, 'u1', 'User', '2-1', 0)",
                (closed_fixture_id,),
            )
            await conn.commit()
        result = await prediction_db.try_save_prediction(
            closed_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.FIXTURE_CLOSED

    @pytest.mark.asyncio
    async def test_concurrent_writers_allow_only_one_prediction(
        self, prediction_db, open_fixture_id
    ):
        async def save(user_name, predictions):
            return await prediction_db.try_save_prediction(
                open_fixture_id,
                "u1",
                user_name,
                predictions,
            )

        first, second = await asyncio.gather(
            save("First", ["2-1", "0-0"]),
            save("Second", ["3-0", "1-1"]),
        )

        assert sorted([first, second]) == [SaveResult.DUPLICATE, SaveResult.SAVED]

        async with (
            aiosqlite.connect(prediction_db.db_path) as conn,
            conn.execute(
                "SELECT COUNT(*), user_name, predictions FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (open_fixture_id, "u1"),
            ) as cursor,
        ):
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] == 1
        assert row[1] in {"First", "Second"}
        assert row[2] in {"2-1\n0-0", "3-0\n1-1"}


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


class TestCreateNextFixtureConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_calls_allocate_distinct_week_numbers(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        created = await asyncio.gather(
            db.create_next_fixture("111111", ["A - B"], datetime.now(UTC)),
            db.create_next_fixture("111111", ["C - D"], datetime.now(UTC)),
        )

        fixture_ids = [fixture_id for fixture_id, _week in created]
        weeks = sorted(week for _fixture_id, week in created)

        assert weeks == [1, 2]

        fixtures = [await db.get_fixture_by_id(fixture_id) for fixture_id in fixture_ids]
        assert all(fixture is not None for fixture in fixtures)
        assert sorted(fixture["week_number"] for fixture in fixtures if fixture is not None) == [
            1,
            2,
        ]


class TestRowToFixture:
    """Test edge cases in _row_to_fixture deserialization."""

    @pytest.mark.asyncio
    async def test_empty_games_column_returns_empty_list(self, temp_db_path):
        """Empty games column must deserialize to [] not [''] (split artefact)."""
        db = Database(temp_db_path)
        await db.initialize()

        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "INSERT INTO fixtures (guild_id, week_number, games, deadline, status) VALUES (?, ?, ?, ?, ?)",
                ("111111", 99, "", "2030-01-01T00:00:00+00:00", "open"),
            )
            await conn.commit()

        fixture = await db.get_current_fixture("111111")
        assert fixture is not None
        assert fixture["games"] == []
