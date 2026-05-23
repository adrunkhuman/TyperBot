from datetime import UTC, datetime

import aiosqlite
import pytest

from typer_bot.database import Database


class TestSchemaValidation:
    @pytest.mark.asyncio
    async def test_initialize_is_safe_for_current_schema_existing_data(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.fixtures.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.predictions.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["2-1"],
            public_message_id="message-1",
            public_message_kind="thread_prediction",
        )
        await db.results.save_results(fixture_id, ["2-1"])

        restarted_db = Database(temp_db_path)
        await restarted_db.initialize()
        await restarted_db.results.save_results(fixture_id, ["3-1"])

        fixture = await restarted_db.fixtures.get_fixture_by_id(fixture_id, "111111")
        prediction = await restarted_db.predictions.get_prediction(fixture_id, "user-1", "111111")
        results = await restarted_db.results.get_results(fixture_id)

        assert fixture is not None
        assert fixture["guild_id"] == "111111"
        assert prediction["public_message_id"] == "message-1"
        assert prediction["public_message_kind"] == "thread_prediction"
        assert results == ["3-1"]

    @pytest.mark.asyncio
    async def test_initialize_rejects_duplicate_result_rows_without_mutating(self, temp_db_path):
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE seasons (
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
                );
                CREATE TABLE fixtures (
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
                );
                CREATE TABLE predictions (
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
                    UNIQUE(fixture_id, user_id)
                );
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    exact_scores INTEGER DEFAULT 0,
                    correct_results INTEGER DEFAULT 0,
                    UNIQUE(fixture_id, user_id)
                );
                CREATE TABLE guild_config (
                    guild_id TEXT PRIMARY KEY,
                    admin_role_id TEXT NOT NULL,
                    league_channel_id TEXT NOT NULL,
                    active_season_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.execute(
                "INSERT INTO seasons (id, guild_id, name, status) VALUES (1, '111111', 'Current Season', 'active')"
            )
            await conn.execute(
                "INSERT INTO fixtures (id, guild_id, season_id, week_number, games, deadline, status) VALUES (1, '111111', 1, 1, 'A - B', ?, 'open')",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.execute(
                "INSERT INTO results (fixture_id, results, calculated_at, updated_at) VALUES (1, '1-0', '2024-01-01T10:00:00+00:00', '2024-01-01T10:00:00+00:00')"
            )
            await conn.execute(
                "INSERT INTO results (fixture_id, results, calculated_at, updated_at) VALUES (1, '2-0', '2024-01-01T12:00:00+00:00', '2024-01-01T12:00:00+00:00')"
            )
            await conn.commit()

        db = Database(temp_db_path)

        with pytest.raises(
            RuntimeError,
            match=r"results has duplicate rows for fixture_id\(s\): 1.*Keep one result row per fixture",
        ):
            await db.initialize()

        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute("SELECT results FROM results ORDER BY id") as cursor,
        ):
            assert await cursor.fetchall() == [("1-0",), ("2-0",)]

    @pytest.mark.asyncio
    async def test_initialize_creates_missing_result_unique_index_for_current_schema(
        self, temp_db_path
    ):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.fixtures.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.results.save_results(fixture_id, ["1-0"])
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("DROP INDEX idx_results_fixture_id_unique")
            await conn.commit()

        await db.initialize()
        await db.results.save_results(fixture_id, ["2-0"])

        assert await db.results.get_results(fixture_id) == ["2-0"]
        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute(
                "SELECT COUNT(*) FROM results WHERE fixture_id = ?", (fixture_id,)
            ) as cursor,
        ):
            assert await cursor.fetchone() == (1,)

    @pytest.mark.asyncio
    async def test_initialize_rejects_partial_result_unique_index(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("DROP INDEX idx_results_fixture_id_unique")
            await conn.execute(
                "CREATE UNIQUE INDEX idx_results_fixture_id_unique ON results(fixture_id) WHERE fixture_id > 0"
            )
            await conn.commit()

        with pytest.raises(RuntimeError, match=r"results\(fixture_id\)"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_rejects_missing_prediction_unique_constraint(self, temp_db_path):
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE seasons (
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
                );
                CREATE TABLE fixtures (
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
                );
                CREATE TABLE predictions (
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
                    public_message_kind TEXT
                );
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    exact_scores INTEGER DEFAULT 0,
                    correct_results INTEGER DEFAULT 0,
                    UNIQUE(fixture_id, user_id)
                );
                CREATE TABLE guild_config (
                    guild_id TEXT PRIMARY KEY,
                    admin_role_id TEXT NOT NULL,
                    league_channel_id TEXT NOT NULL,
                    active_season_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.commit()

        db = Database(temp_db_path)

        with pytest.raises(RuntimeError, match=r"predictions\(fixture_id, user_id\)"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_rejects_stale_schema_without_required_columns(self, temp_db_path):
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE seasons (
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
                );
                CREATE TABLE fixtures (
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
                );
                CREATE TABLE predictions (
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
                    public_message_kind TEXT,
                    UNIQUE(fixture_id, user_id)
                );
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    results TEXT NOT NULL,
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fixture_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    exact_scores INTEGER DEFAULT 0,
                    correct_results INTEGER DEFAULT 0,
                    UNIQUE(fixture_id, user_id)
                );
                CREATE TABLE guild_config (
                    guild_id TEXT PRIMARY KEY,
                    admin_role_id TEXT NOT NULL,
                    league_channel_id TEXT NOT NULL,
                    active_season_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.commit()

        db = Database(temp_db_path)

        with pytest.raises(RuntimeError, match="predictions.public_message_id"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_rejects_existing_schema_with_missing_table(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("DROP TABLE scores")
            await conn.commit()

        with pytest.raises(RuntimeError, match="scores.fixture_id"):
            await db.initialize()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("guild_id", ["", "   "])
    async def test_initialize_rejects_blank_fixture_guild_ownership(self, temp_db_path, guild_id):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.fixtures.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "UPDATE fixtures SET guild_id = ? WHERE id = ?", (guild_id, fixture_id)
            )
            await conn.commit()

        with pytest.raises(RuntimeError, match="fixtures.guild_id has empty rows"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_rejects_null_fixture_guild_ownership(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("DROP TABLE fixtures")
            await conn.execute(
                """
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    season_id INTEGER,
                    week_number INTEGER NOT NULL,
                    games TEXT NOT NULL,
                    deadline DATETIME NOT NULL,
                    status TEXT DEFAULT 'open',
                    message_id TEXT,
                    channel_id TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "INSERT INTO fixtures (guild_id, season_id, week_number, games, deadline, status) VALUES (NULL, NULL, 1, 'A - B', ?, 'open')",
                (datetime.now(UTC).isoformat(),),
            )
            await conn.commit()

        with pytest.raises(RuntimeError, match="fixtures.guild_id has empty rows"):
            await db.initialize()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "sql",
        [
            "UPDATE fixtures SET season_id = NULL WHERE id = ?",
            "UPDATE fixtures SET season_id = 999999 WHERE id = ?",
        ],
    )
    async def test_initialize_rejects_fixture_without_valid_season(self, temp_db_path, sql):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.fixtures.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(sql, (fixture_id,))
            await conn.commit()

        with pytest.raises(RuntimeError, match="same-guild season_id"):
            await db.initialize()

    @pytest.mark.asyncio
    async def test_initialize_rejects_fixture_with_other_guild_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.fixtures.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        other_fixture_id = await db.fixtures.create_fixture(
            "222222", 1, ["C - D"], datetime.now(UTC)
        )
        other_fixture = await db.fixtures.get_fixture_by_id(other_fixture_id, "222222")
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "UPDATE fixtures SET season_id = ? WHERE id = ?",
                (other_fixture["season_id"], fixture_id),
            )
            await conn.commit()

        with pytest.raises(RuntimeError, match="same-guild season_id"):
            await db.initialize()
