import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from tests.database.helpers import start_new_active_season
from typer_bot.database import Database


class TestGetMaxWeekNumber:
    @pytest.mark.asyncio
    async def test_get_max_week_number_empty_db(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        result = await db.get_max_week_number("111111")
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_max_week_number_with_fixtures(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        await db.create_fixture("111111", 3, ["Team C - Team D"], datetime.now(UTC))
        await db.create_fixture("111111", 5, ["Team E - Team F"], datetime.now(UTC))

        result = await db.get_max_week_number("111111")
        assert result == 5

    @pytest.mark.asyncio
    async def test_get_max_week_number_closed_fixtures(self, temp_db_path):
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

    @pytest.mark.asyncio
    async def test_get_max_week_number_is_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.create_fixture("111111", 10, ["Team A - Team B"], datetime.now(UTC))
        await start_new_active_season(temp_db_path, "111111")

        assert await db.get_max_week_number("111111") == 0

        await db.create_fixture("111111", 1, ["Team C - Team D"], datetime.now(UTC))

        assert await db.get_max_week_number("111111") == 1


class TestOpenFixturesQueries:
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
        assert await db.get_fixture_by_id(fixture_id, "guild-2") is not None

        assert await db.delete_fixture(fixture_id, "guild-2") is True
        assert await db.get_fixture_by_id(fixture_id, "guild-2") is None

    @pytest.mark.asyncio
    async def test_get_prediction_requires_fixture_guild_ownership(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture("guild-2", 1, ["Team A - Team B"], datetime.now(UTC))
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)

        assert await db.get_prediction(fixture_id, "user-1", "111111") is None
        assert await db.get_prediction(fixture_id, "user-1", "guild-2") is not None

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

        fixture_one = await db.get_fixture_by_id(fixture_one_id, "111111")
        fixture_two = await db.get_fixture_by_id(fixture_two_id, "111111")

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

        fixture = await db.get_fixture_by_id(fixture_id, "guild-2")
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

        guild_one_second = await db.get_fixture_by_id(guild_one_second_id, "111111")
        assert guild_one_second is not None
        assert guild_one_second["guild_id"] == "111111"


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

        fixtures = [await db.get_fixture_by_id(fixture_id, "111111") for fixture_id in fixture_ids]
        assert all(fixture is not None for fixture in fixtures)
        assert sorted(fixture["week_number"] for fixture in fixtures if fixture is not None) == [
            1,
            2,
        ]


class TestRowToFixture:
    @pytest.mark.asyncio
    async def test_empty_games_column_returns_empty_list(self, temp_db_path):
        """Empty games column must deserialize to [] not [''] (split artefact)."""
        db = Database(temp_db_path)
        await db.initialize()
        season = await db.get_or_create_active_season("111111")

        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "INSERT INTO fixtures (guild_id, season_id, week_number, games, deadline, status) VALUES (?, ?, ?, ?, ?, ?)",
                ("111111", season["id"], 99, "", "2030-01-01T00:00:00+00:00", "open"),
            )
            await conn.commit()

        fixture = await db.get_current_fixture("111111")
        assert fixture is not None
        assert fixture["games"] == []
