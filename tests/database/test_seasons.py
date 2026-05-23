from datetime import UTC, datetime

import aiosqlite
import pytest

from tests.database.helpers import start_new_active_season
from typer_bot.database import Database, SaveResult


class TestSeasons:
    @pytest.mark.asyncio
    async def test_create_fixture_uses_fresh_guild_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.guild_config.upsert_guild_config("111111", "role-1", "channel-1")

        first_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        second_fixture_id = await db.fixtures.create_fixture(
            "111111", 2, ["Team C - Team D"], datetime.now(UTC)
        )

        active_season = await db.seasons.get_active_season("111111")
        first_fixture = await db.fixtures.get_fixture_by_id(first_fixture_id, "111111")
        second_fixture = await db.fixtures.get_fixture_by_id(second_fixture_id, "111111")
        config = await db.guild_config.get_guild_config("111111")

        assert active_season is not None
        assert active_season["guild_id"] == "111111"
        assert active_season["name"]
        assert active_season["status"] == "active"
        assert first_fixture["season_id"] == active_season["id"]
        assert second_fixture["season_id"] == active_season["id"]
        assert config["active_season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_active_seasons_are_guild_isolated(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()

        guild_one_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        guild_two_fixture_id = await db.fixtures.create_fixture(
            "222222", 1, ["Team C - Team D"], datetime.now(UTC)
        )

        guild_one_season = await db.seasons.get_active_season("111111")
        guild_two_season = await db.seasons.get_active_season("222222")
        guild_one_fixture = await db.fixtures.get_fixture_by_id(guild_one_fixture_id, "111111")
        guild_two_fixture = await db.fixtures.get_fixture_by_id(guild_two_fixture_id, "222222")

        assert guild_one_season is not None
        assert guild_two_season is not None
        assert guild_one_season["guild_id"] == "111111"
        assert guild_two_season["guild_id"] == "222222"
        assert guild_one_season["id"] != guild_two_season["id"]
        assert guild_one_fixture["season_id"] == guild_one_season["id"]
        assert guild_two_fixture["season_id"] == guild_two_season["id"]

    @pytest.mark.asyncio
    async def test_get_or_create_active_season_repairs_stale_config_pointer(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.guild_config.upsert_guild_config("111111", "role-1", "channel-1")
        fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        fixture = await db.fixtures.get_fixture_by_id(fixture_id, "111111")

        async with aiosqlite.connect(temp_db_path) as conn:
            cursor = await conn.execute(
                "INSERT INTO seasons (guild_id, name, status) VALUES ('222222', 'Wrong Guild', 'active')"
            )
            await conn.execute(
                "UPDATE guild_config SET active_season_id = ? WHERE guild_id = '111111'",
                (cursor.lastrowid,),
            )
            await conn.commit()

        active_season = await db.seasons.get_or_create_active_season("111111")
        config = await db.guild_config.get_guild_config("111111")

        assert active_season["id"] == fixture["season_id"]
        assert config["active_season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_create_next_fixture_restarts_week_numbers_per_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        _old_fixture_id, old_week = await db.fixtures.create_next_fixture(
            "111111", ["Team A - Team B"], datetime.now(UTC)
        )
        await start_new_active_season(temp_db_path, "111111")

        new_fixture_id, new_week = await db.fixtures.create_next_fixture(
            "111111", ["Team C - Team D"], datetime.now(UTC)
        )
        new_fixture = await db.fixtures.get_fixture_by_id(new_fixture_id, "111111")
        active_season = await db.seasons.get_active_season("111111")

        assert old_week == 1
        assert new_week == 1
        assert new_fixture["season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_start_new_season_archives_previous_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.guild_config.upsert_guild_config("111111", "role-1", "channel-1")
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 7, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.scores.save_scores(
            old_fixture_id,
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
        old_season = await db.seasons.get_active_season("111111")

        new_season = await db.seasons.start_new_season("111111", "2026/27")
        config = await db.guild_config.get_guild_config("111111")
        seasons = await db.seasons.get_seasons("111111")

        assert old_season is not None
        assert new_season["name"] == "2026/27"
        assert new_season["status"] == "active"
        assert config["active_season_id"] == new_season["id"]
        assert [(season["id"], season["status"]) for season in seasons] == [
            (old_season["id"], "archived"),
            (new_season["id"], "active"),
        ]
        assert seasons[0]["ended_at"] is not None

    @pytest.mark.asyncio
    async def test_start_new_season_blocks_open_active_fixture(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.fixtures.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        old_season = await db.seasons.get_active_season("111111")

        with pytest.raises(ValueError, match="Close all open fixtures"):
            await db.seasons.start_new_season("111111", "2026/27")

        assert await db.seasons.get_active_season("111111") == old_season

    @pytest.mark.asyncio
    async def test_start_new_season_rejects_blank_name_without_mutating(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.scores.save_scores(
            old_fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )
        old_season = await db.seasons.get_active_season("111111")

        with pytest.raises(ValueError, match="Season name is required"):
            await db.seasons.start_new_season("111111", "   ")

        assert await db.seasons.get_active_season("111111") == old_season
        assert await db.seasons.get_seasons("111111") == [old_season]

    @pytest.mark.asyncio
    async def test_start_new_season_rolls_back_when_new_season_insert_fails(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.scores.save_scores(
            old_fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )
        old_season = await db.seasons.get_active_season("111111")
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TRIGGER fail_broken_season_insert
                BEFORE INSERT ON seasons
                WHEN NEW.name = 'Broken Season'
                BEGIN
                    SELECT RAISE(FAIL, 'broken season insert');
                END
                """
            )
            await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError, match="broken season insert"):
            await db.seasons.start_new_season("111111", "Broken Season")

        assert await db.seasons.get_active_season("111111") == old_season
        assert await db.seasons.get_seasons("111111") == [old_season]

    @pytest.mark.asyncio
    async def test_start_new_season_resets_next_fixture_week(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id, old_week = await db.fixtures.create_next_fixture(
            "111111", ["Team A - Team B"], datetime.now(UTC)
        )
        await db.scores.save_scores(
            old_fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )

        await db.seasons.start_new_season("111111", "2026/27")
        new_fixture_id, new_week = await db.fixtures.create_next_fixture(
            "111111", ["Team C - Team D"], datetime.now(UTC)
        )
        new_fixture = await db.fixtures.get_fixture_by_id(new_fixture_id, "111111")
        active_season = await db.seasons.get_active_season("111111")

        assert old_week == 1
        assert new_week == 1
        assert new_fixture["season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_start_new_season_uses_fresh_default_scoring_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        custom_rules = {
            "exact_score_points": 5,
            "correct_outcome_points": 2,
            "wrong_outcome_points": 1,
            "late_prediction_points": 1,
        }
        await db.seasons.update_active_scoring_rules("111111", custom_rules)

        await db.seasons.start_new_season("111111", "Next Season")

        seasons = await db.seasons.get_seasons("111111")
        active_rules = await db.seasons.get_active_scoring_rules("111111")
        assert seasons[0]["scoring_rules"] == custom_rules
        assert active_rules == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

    @pytest.mark.asyncio
    async def test_scoring_rule_updates_preserve_omitted_values(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.seasons.update_active_scoring_rules(
            "111111",
            {
                "exact_score_points": 5,
                "correct_outcome_points": 2,
                "wrong_outcome_points": 1,
                "late_prediction_points": 1,
            },
        )

        await db.seasons.update_active_scoring_rules("111111", {"late_prediction_points": 2})

        assert await db.seasons.get_active_scoring_rules("111111") == {
            "exact_score_points": 5,
            "correct_outcome_points": 2,
            "wrong_outcome_points": 1,
            "late_prediction_points": 2,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rules", "message"),
        [
            ({"exact_score_points": -1}, "zero or greater"),
            ({"exact_score_points": "many"}, "whole numbers"),
            ({"exact_points": 5}, "Unknown scoring rule"),
        ],
    )
    async def test_invalid_scoring_rule_updates_do_not_mutate_existing_rules(
        self, temp_db_path, rules, message
    ):
        db = Database(temp_db_path)
        await db.initialize()
        await db.seasons.update_active_scoring_rules("111111", {"exact_score_points": 5})
        existing_rules = await db.seasons.get_active_scoring_rules("111111")

        with pytest.raises(ValueError, match=message):
            await db.seasons.update_active_scoring_rules("111111", rules)

        assert await db.seasons.get_active_scoring_rules("111111") == existing_rules

    @pytest.mark.asyncio
    async def test_fixture_queries_default_to_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.fixtures.update_fixture_announcement(old_fixture_id, "old-message", "channel-1")
        await start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        await db.fixtures.update_fixture_announcement(active_fixture_id, "new-message", "channel-1")

        current_fixture = await db.fixtures.get_current_fixture("111111")
        open_fixtures = await db.fixtures.get_open_fixtures("111111")
        recent_fixtures = await db.fixtures.get_recent_fixtures("111111")
        week_fixture = await db.fixtures.get_open_fixture_by_week("111111", 1)
        any_status_week_fixture = await db.fixtures.get_fixture_by_week("111111", 1)
        message_fixture = await db.fixtures.get_fixture_by_message_id("new-message", "111111")
        global_message_fixture = await db.fixtures.get_fixture_by_message_id("new-message")

        assert await db.fixtures.get_fixture_by_id(old_fixture_id, "111111") is None
        assert current_fixture["id"] == active_fixture_id
        assert [fixture["id"] for fixture in open_fixtures] == [active_fixture_id]
        assert [fixture["id"] for fixture in recent_fixtures] == [active_fixture_id]
        assert week_fixture["id"] == active_fixture_id
        assert any_status_week_fixture["id"] == active_fixture_id
        assert message_fixture["id"] == active_fixture_id
        assert global_message_fixture["id"] == active_fixture_id
        assert await db.fixtures.get_fixture_by_message_id("old-message", "111111") is None
        assert await db.fixtures.get_fixture_by_message_id("old-message") is None

    @pytest.mark.asyncio
    async def test_all_open_fixtures_only_returns_active_season_fixtures(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        other_guild_fixture_id = await db.fixtures.create_fixture(
            "222222", 1, ["Other Team A - Other Team B"], datetime.now(UTC)
        )

        open_fixture_ids = [fixture["id"] for fixture in await db.fixtures.get_all_open_fixtures()]

        assert set(open_fixture_ids) == {active_fixture_id, other_guild_fixture_id}

    @pytest.mark.asyncio
    async def test_archived_fixture_prediction_writes_are_rejected(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.predictions.save_prediction(old_fixture_id, "user-1", "User One", ["1-0"], False)
        await start_new_active_season(temp_db_path, "111111")

        first_write = await db.predictions.try_save_prediction(
            old_fixture_id, "user-2", "User Two", ["2-0"]
        )
        guarded_write = await db.predictions.save_prediction_guarded(
            old_fixture_id, "user-1", "User One", ["9-9"]
        )
        admin_write = await db.predictions.admin_update_prediction_with_recalc(
            old_fixture_id, "user-1", ["8-8"], "admin-1"
        )

        assert first_write == SaveResult.FIXTURE_CLOSED
        assert guarded_write == SaveResult.FIXTURE_CLOSED
        assert admin_write is False
        assert await db.predictions.get_prediction(old_fixture_id, "user-1", "111111") is None
        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute(
                "SELECT predictions FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (old_fixture_id, "user-1"),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        assert row == ("1-0",)

    @pytest.mark.asyncio
    async def test_archived_pending_partials_are_hidden_and_not_mutated(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.predictions.save_prediction(
            old_fixture_id,
            "user-1",
            "User One",
            ["1-0"],
            True,
            pending_partial_approval=True,
        )
        await start_new_active_season(temp_db_path, "111111")

        approved = await db.predictions.approve_partial_prediction_with_recalc(
            old_fixture_id, "user-1", "admin-1"
        )
        rejected = await db.predictions.reject_partial_prediction_with_recalc(
            old_fixture_id, "user-1"
        )
        pending = await db.predictions.get_pending_partial_predictions("111111")

        assert approved is False
        assert rejected is False
        assert pending == []
        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute(
                "SELECT pending_partial_approval FROM predictions WHERE fixture_id = ? AND user_id = ?",
                (old_fixture_id, "user-1"),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        assert row == (1,)

    @pytest.mark.asyncio
    async def test_archived_fixture_result_and_score_writes_are_rejected(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.results.save_results(old_fixture_id, ["0-0"])
        await db.scores.save_scores(
            old_fixture_id,
            [
                {
                    "user_id": "old-user",
                    "user_name": "Old User",
                    "points": 30,
                    "exact_scores": 10,
                    "correct_results": 0,
                }
            ],
        )
        await start_new_active_season(temp_db_path, "111111")

        with pytest.raises(ValueError):
            await db.results.save_results(old_fixture_id, ["1-0"])
        with pytest.raises(ValueError):
            await db.results.save_results_with_recalc(old_fixture_id, ["1-0"])
        with pytest.raises(ValueError):
            await db.scores.recalculate_fixture_scores(old_fixture_id)
        with pytest.raises(ValueError):
            await db.scores.save_scores(
                old_fixture_id,
                [
                    {
                        "user_id": "new-user",
                        "user_name": "New User",
                        "points": 1,
                        "exact_scores": 0,
                        "correct_results": 1,
                    }
                ],
            )

        assert await db.results.get_results(old_fixture_id) == ["0-0"]
        scores = await db.scores.get_scores_for_fixture(old_fixture_id)
        assert [(score["user_id"], score["points"]) for score in scores] == [("old-user", 30)]

    @pytest.mark.asyncio
    async def test_archived_fixture_delete_requires_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.fixtures.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await start_new_active_season(temp_db_path, "111111")

        assert await db.fixtures.delete_fixture(old_fixture_id, "111111") is False
        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute("SELECT 1 FROM fixtures WHERE id = ?", (old_fixture_id,)) as cursor,
        ):
            assert await cursor.fetchone() == (1,)
