"""Tests for database operations and defensive coding patterns."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from typer_bot.database import Database, SaveResult
from typer_bot.database import scores as scores_module
from typer_bot.database.predictions import PredictionRepository


@pytest.fixture
def temp_db_path():
    """Provide a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


async def _start_new_active_season(db_path: str, guild_id: str, name: str = "Next Season") -> int:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE seasons SET status = 'archived' WHERE guild_id = ? AND status = 'active'",
            (guild_id,),
        )
        cursor = await conn.execute(
            "INSERT INTO seasons (guild_id, name, status) VALUES (?, ?, 'active')",
            (guild_id, name),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Failed to create test season")
        await conn.execute(
            "UPDATE guild_config SET active_season_id = ? WHERE guild_id = ?",
            (cursor.lastrowid, guild_id),
        )
        await conn.commit()
        return cursor.lastrowid


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

    @pytest.mark.asyncio
    async def test_get_max_week_number_is_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.create_fixture("111111", 10, ["Team A - Team B"], datetime.now(UTC))
        await _start_new_active_season(temp_db_path, "111111")

        assert await db.get_max_week_number("111111") == 0

        await db.create_fixture("111111", 1, ["Team C - Team D"], datetime.now(UTC))

        assert await db.get_max_week_number("111111") == 1


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


class TestSeasons:
    @pytest.mark.asyncio
    async def test_create_fixture_uses_fresh_guild_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.upsert_guild_config("111111", "role-1", "channel-1")

        first_fixture_id = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        second_fixture_id = await db.create_fixture(
            "111111", 2, ["Team C - Team D"], datetime.now(UTC)
        )

        active_season = await db.get_active_season("111111")
        first_fixture = await db.get_fixture_by_id(first_fixture_id, "111111")
        second_fixture = await db.get_fixture_by_id(second_fixture_id, "111111")
        config = await db.get_guild_config("111111")

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

        guild_one_fixture_id = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        guild_two_fixture_id = await db.create_fixture(
            "222222", 1, ["Team C - Team D"], datetime.now(UTC)
        )

        guild_one_season = await db.get_active_season("111111")
        guild_two_season = await db.get_active_season("222222")
        guild_one_fixture = await db.get_fixture_by_id(guild_one_fixture_id, "111111")
        guild_two_fixture = await db.get_fixture_by_id(guild_two_fixture_id, "222222")

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
        await db.upsert_guild_config("111111", "role-1", "channel-1")
        fixture_id = await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        fixture = await db.get_fixture_by_id(fixture_id, "111111")

        async with aiosqlite.connect(temp_db_path) as conn:
            cursor = await conn.execute(
                "INSERT INTO seasons (guild_id, name, status) VALUES ('222222', 'Wrong Guild', 'active')"
            )
            await conn.execute(
                "UPDATE guild_config SET active_season_id = ? WHERE guild_id = '111111'",
                (cursor.lastrowid,),
            )
            await conn.commit()

        active_season = await db.get_or_create_active_season("111111")
        config = await db.get_guild_config("111111")

        assert active_season["id"] == fixture["season_id"]
        assert config["active_season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_create_next_fixture_restarts_week_numbers_per_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        _old_fixture_id, old_week = await db.create_next_fixture(
            "111111", ["Team A - Team B"], datetime.now(UTC)
        )
        await _start_new_active_season(temp_db_path, "111111")

        new_fixture_id, new_week = await db.create_next_fixture(
            "111111", ["Team C - Team D"], datetime.now(UTC)
        )
        new_fixture = await db.get_fixture_by_id(new_fixture_id, "111111")
        active_season = await db.get_active_season("111111")

        assert old_week == 1
        assert new_week == 1
        assert new_fixture["season_id"] == active_season["id"]

    @pytest.mark.asyncio
    async def test_start_new_season_archives_previous_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.upsert_guild_config("111111", "role-1", "channel-1")
        old_fixture_id = await db.create_fixture(
            "111111", 7, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.save_scores(
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
        old_season = await db.get_active_season("111111")

        new_season = await db.start_new_season("111111", "2026/27")
        config = await db.get_guild_config("111111")
        seasons = await db.get_seasons("111111")

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
        await db.create_fixture("111111", 1, ["Team A - Team B"], datetime.now(UTC))
        old_season = await db.get_active_season("111111")

        with pytest.raises(ValueError, match="Close all open fixtures"):
            await db.start_new_season("111111", "2026/27")

        assert await db.get_active_season("111111") == old_season

    @pytest.mark.asyncio
    async def test_start_new_season_rejects_blank_name_without_mutating(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.save_scores(
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
        old_season = await db.get_active_season("111111")

        with pytest.raises(ValueError, match="Season name is required"):
            await db.start_new_season("111111", "   ")

        assert await db.get_active_season("111111") == old_season
        assert await db.get_seasons("111111") == [old_season]

    @pytest.mark.asyncio
    async def test_start_new_season_rolls_back_when_new_season_insert_fails(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Team A - Team B"], datetime.now(UTC)
        )
        await db.save_scores(
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
        old_season = await db.get_active_season("111111")
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
            await db.start_new_season("111111", "Broken Season")

        assert await db.get_active_season("111111") == old_season
        assert await db.get_seasons("111111") == [old_season]

    @pytest.mark.asyncio
    async def test_start_new_season_resets_next_fixture_week(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id, old_week = await db.create_next_fixture(
            "111111", ["Team A - Team B"], datetime.now(UTC)
        )
        await db.save_scores(
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

        await db.start_new_season("111111", "2026/27")
        new_fixture_id, new_week = await db.create_next_fixture(
            "111111", ["Team C - Team D"], datetime.now(UTC)
        )
        new_fixture = await db.get_fixture_by_id(new_fixture_id, "111111")
        active_season = await db.get_active_season("111111")

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
        await db.update_active_scoring_rules("111111", custom_rules)

        await db.start_new_season("111111", "Next Season")

        seasons = await db.get_seasons("111111")
        active_rules = await db.get_active_scoring_rules("111111")
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
        await db.update_active_scoring_rules(
            "111111",
            {
                "exact_score_points": 5,
                "correct_outcome_points": 2,
                "wrong_outcome_points": 1,
                "late_prediction_points": 1,
            },
        )

        await db.update_active_scoring_rules("111111", {"late_prediction_points": 2})

        assert await db.get_active_scoring_rules("111111") == {
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
        await db.update_active_scoring_rules("111111", {"exact_score_points": 5})
        existing_rules = await db.get_active_scoring_rules("111111")

        with pytest.raises(ValueError, match=message):
            await db.update_active_scoring_rules("111111", rules)

        assert await db.get_active_scoring_rules("111111") == existing_rules

    @pytest.mark.asyncio
    async def test_fixture_queries_default_to_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.update_fixture_announcement(old_fixture_id, "old-message", "channel-1")
        await _start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        await db.update_fixture_announcement(active_fixture_id, "new-message", "channel-1")

        current_fixture = await db.get_current_fixture("111111")
        open_fixtures = await db.get_open_fixtures("111111")
        recent_fixtures = await db.get_recent_fixtures("111111")
        week_fixture = await db.get_open_fixture_by_week("111111", 1)
        any_status_week_fixture = await db.get_fixture_by_week("111111", 1)
        message_fixture = await db.get_fixture_by_message_id("new-message", "111111")
        global_message_fixture = await db.get_fixture_by_message_id("new-message")

        assert await db.get_fixture_by_id(old_fixture_id, "111111") is None
        assert current_fixture["id"] == active_fixture_id
        assert [fixture["id"] for fixture in open_fixtures] == [active_fixture_id]
        assert [fixture["id"] for fixture in recent_fixtures] == [active_fixture_id]
        assert week_fixture["id"] == active_fixture_id
        assert any_status_week_fixture["id"] == active_fixture_id
        assert message_fixture["id"] == active_fixture_id
        assert global_message_fixture["id"] == active_fixture_id
        assert await db.get_fixture_by_message_id("old-message", "111111") is None
        assert await db.get_fixture_by_message_id("old-message") is None

    @pytest.mark.asyncio
    async def test_all_open_fixtures_only_returns_active_season_fixtures(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.create_fixture("111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC))
        await _start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        other_guild_fixture_id = await db.create_fixture(
            "222222", 1, ["Other Team A - Other Team B"], datetime.now(UTC)
        )

        open_fixture_ids = [fixture["id"] for fixture in await db.get_all_open_fixtures()]

        assert set(open_fixture_ids) == {active_fixture_id, other_guild_fixture_id}

    @pytest.mark.asyncio
    async def test_archived_fixture_prediction_writes_are_rejected(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.save_prediction(old_fixture_id, "user-1", "User One", ["1-0"], False)
        await _start_new_active_season(temp_db_path, "111111")

        first_write = await db.try_save_prediction(old_fixture_id, "user-2", "User Two", ["2-0"])
        guarded_write = await db.save_prediction_guarded(
            old_fixture_id, "user-1", "User One", ["9-9"]
        )
        admin_write = await db.admin_update_prediction_with_recalc(
            old_fixture_id, "user-1", ["8-8"], "admin-1"
        )

        assert first_write == SaveResult.FIXTURE_CLOSED
        assert guarded_write == SaveResult.FIXTURE_CLOSED
        assert admin_write is False
        assert await db.get_prediction(old_fixture_id, "user-1", "111111") is None
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
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.save_prediction(
            old_fixture_id,
            "user-1",
            "User One",
            ["1-0"],
            True,
            pending_partial_approval=True,
        )
        await _start_new_active_season(temp_db_path, "111111")

        approved = await db.approve_partial_prediction(old_fixture_id, "user-1", "admin-1")
        rejected = await db.reject_partial_prediction(old_fixture_id, "user-1")
        pending = await db.get_pending_partial_predictions("111111")

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
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.save_results(old_fixture_id, ["0-0"])
        await db.save_scores(
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
        await _start_new_active_season(temp_db_path, "111111")

        with pytest.raises(ValueError):
            await db.save_results(old_fixture_id, ["1-0"])
        with pytest.raises(ValueError):
            await db.save_results_with_recalc(old_fixture_id, ["1-0"])
        with pytest.raises(ValueError):
            await db.recalculate_fixture_scores(old_fixture_id)
        with pytest.raises(ValueError):
            await db.save_scores(
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

        assert await db.get_results(old_fixture_id) == ["0-0"]
        scores = await db.get_scores_for_fixture(old_fixture_id)
        assert [(score["user_id"], score["points"]) for score in scores] == [("old-user", 30)]

    @pytest.mark.asyncio
    async def test_archived_fixture_delete_requires_active_season(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await _start_new_active_season(temp_db_path, "111111")

        assert await db.delete_fixture(old_fixture_id, "111111") is False
        async with (
            aiosqlite.connect(temp_db_path) as conn,
            conn.execute("SELECT 1 FROM fixtures WHERE id = ?", (old_fixture_id,)) as cursor,
        ):
            assert await cursor.fetchone() == (1,)


class TestScores:
    @pytest.mark.asyncio
    async def test_result_correction_recalculates_with_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules(
            "111111", {"exact_score_points": 5, "correct_outcome_points": 2}
        )
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
        await db.recalculate_fixture_scores(fixture_id)

        await db.save_results_with_recalc(fixture_id, ["2-0"])

        scores = await db.get_scores_for_fixture(fixture_id)
        assert scores[0]["points"] == 2

    @pytest.mark.asyncio
    async def test_prediction_replacement_recalculates_with_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules(
            "111111", {"exact_score_points": 5, "wrong_outcome_points": 1}
        )
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
        await db.recalculate_fixture_scores(fixture_id)

        updated = await db.admin_update_prediction_with_recalc(
            fixture_id, "user-1", ["1-2"], "admin-1"
        )

        scores = await db.get_scores_for_fixture(fixture_id)
        assert updated is True
        assert scores[0]["points"] == 1

    @pytest.mark.asyncio
    async def test_waiver_toggle_recalculates_with_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules(
            "111111", {"exact_score_points": 5, "late_prediction_points": 1}
        )
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], True)
        await db.recalculate_fixture_scores(fixture_id)

        waived = await db.toggle_late_penalty_waiver_with_recalc(fixture_id, "user-1")

        scores = await db.get_scores_for_fixture(fixture_id)
        assert waived is True
        assert scores[0]["points"] == 5

    @pytest.mark.asyncio
    async def test_partial_approval_recalculates_with_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules("111111", {"exact_score_points": 5})
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
        await db.recalculate_fixture_scores(fixture_id)
        await db.save_prediction(
            fixture_id,
            "partial",
            "Partial User",
            ["2-1"],
            True,
            predicted_game_indexes=[0],
            pending_partial_approval=True,
        )

        approved = await db.approve_partial_prediction(fixture_id, "partial", "admin-1")

        scores = await db.get_scores_for_fixture(fixture_id)
        assert approved is True
        assert [(score["user_id"], score["points"]) for score in scores] == [
            ("partial", 5),
            ("user-1", 5),
        ]

    @pytest.mark.asyncio
    async def test_partial_rejection_recalculates_with_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules("111111", {"exact_score_points": 5})
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
        await db.save_prediction(
            fixture_id,
            "partial",
            "Partial User",
            ["2-1"],
            True,
            predicted_game_indexes=[0],
            pending_partial_approval=True,
        )
        await db.recalculate_fixture_scores(fixture_id)

        rejected = await db.reject_partial_prediction(fixture_id, "partial")

        scores = await db.get_scores_for_fixture(fixture_id)
        assert rejected is True
        assert [(score["user_id"], score["points"]) for score in scores] == [("user-1", 5)]

    @pytest.mark.asyncio
    async def test_recalculate_fixture_scores_uses_active_season_rules(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules(
            "111111",
            {
                "exact_score_points": 5,
                "correct_outcome_points": 2,
                "wrong_outcome_points": 1,
                "late_prediction_points": 0,
            },
        )
        fixture_id = await db.create_fixture(
            "111111", 1, ["A - B", "C - D", "E - F"], datetime.now(UTC)
        )
        await db.save_results(fixture_id, ["2-1", "1-1", "2-0"])
        await db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["2-1", "2-2", "0-2"],
            False,
        )

        await db.recalculate_fixture_scores(fixture_id)

        scores = await db.get_scores_for_fixture(fixture_id)
        assert scores[0]["points"] == 8
        assert scores[0]["exact_scores"] == 1
        assert scores[0]["correct_results"] == 1

    @pytest.mark.asyncio
    async def test_late_prediction_uses_active_season_penalty_rule(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules("111111", {"late_prediction_points": 1})
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "late", "Late User", ["2-1"], True)
        await db.save_prediction(fixture_id, "waived", "Waived User", ["2-1"], True)
        await db.set_late_penalty_waiver(fixture_id, "waived", True)

        await db.recalculate_fixture_scores(fixture_id)

        scores = await db.get_scores_for_fixture(fixture_id)
        assert [(score["user_id"], score["points"]) for score in scores] == [
            ("waived", 3),
            ("late", 1),
        ]

    @pytest.mark.asyncio
    async def test_scoring_rules_are_guild_isolated(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await db.update_active_scoring_rules("111111", {"exact_score_points": 5})
        guild_one_fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        guild_two_fixture_id = await db.create_fixture("222222", 1, ["A - B"], datetime.now(UTC))
        for fixture_id in (guild_one_fixture_id, guild_two_fixture_id):
            await db.save_results(fixture_id, ["2-1"])
            await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
            await db.recalculate_fixture_scores(fixture_id)

        guild_one_scores = await db.get_scores_for_fixture(guild_one_fixture_id)
        guild_two_scores = await db.get_scores_for_fixture(guild_two_fixture_id)
        assert guild_one_scores[0]["points"] == 5
        assert guild_two_scores[0]["points"] == 3

    @pytest.mark.asyncio
    async def test_scoring_rule_changes_are_blocked_after_scores_exist(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        assert await db.active_season_has_scores("111111") is False
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["2-1"])
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1"], False)
        await db.recalculate_fixture_scores(fixture_id)

        assert await db.active_season_has_scores("111111") is True

        with pytest.raises(ValueError, match="Cannot change scoring rules"):
            await db.update_active_scoring_rules("111111", {"exact_score_points": 5})

        assert await db.get_active_scoring_rules("111111") == {
            "exact_score_points": 3,
            "correct_outcome_points": 1,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

    @pytest.mark.asyncio
    async def test_scoring_rule_change_block_is_guild_and_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_scores(
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
        await db.start_new_season("111111", "Next Season")

        await db.update_active_scoring_rules("111111", {"exact_score_points": 5})

        other_guild_fixture_id = await db.create_fixture("222222", 1, ["C - D"], datetime.now(UTC))
        await db.save_scores(
            other_guild_fixture_id,
            [
                {
                    "user_id": "user-2",
                    "user_name": "User Two",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        await db.update_active_scoring_rules("111111", {"correct_outcome_points": 2})

        active_fixture_id = await db.create_fixture("111111", 1, ["E - F"], datetime.now(UTC))
        await db.save_scores(
            active_fixture_id,
            [
                {
                    "user_id": "user-1",
                    "user_name": "User One",
                    "points": 5,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        with pytest.raises(ValueError, match="Cannot change scoring rules"):
            await db.update_active_scoring_rules("111111", {"wrong_outcome_points": 1})

        assert await db.get_active_scoring_rules("111111") == {
            "exact_score_points": 5,
            "correct_outcome_points": 2,
            "wrong_outcome_points": 0,
            "late_prediction_points": 0,
        }

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

        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TRIGGER fail_second_score_insert
                BEFORE INSERT ON scores
                WHEN NEW.user_id = 'user-3'
                BEGIN
                    SELECT RAISE(FAIL, 'forced score insert failure');
                END
                """
            )
            await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError, match="forced score insert failure"):
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
                        "user_id": "user-3",
                        "user_name": "User Three",
                        "points": 0,
                        "exact_scores": 0,
                        "correct_results": 0,
                    },
                ],
            )

        scores = await db.get_scores_for_fixture(fixture_id)
        fixture = await db.get_fixture_by_id(fixture_id, "111111")
        assert scores == [
            {
                "user_id": "user-1",
                "user_name": "User One",
                "points": 3,
                "exact_scores": 1,
                "correct_results": 0,
            }
        ]
        assert fixture["status"] == "closed"

    @pytest.mark.asyncio
    async def test_recalculate_fixture_scores_rolls_back_after_partial_write_failure(
        self, temp_db_path
    ):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture(
            "111111",
            1,
            ["Team A - Team B", "Team C - Team D"],
            datetime.now(UTC),
        )
        await db.save_prediction(fixture_id, "user-1", "User One", ["2-1", "1-1"], False)
        await db.save_results(fixture_id, ["2-1", "1-1"])
        await db.save_scores(
            fixture_id,
            [
                {
                    "user_id": "original",
                    "user_name": "Original User",
                    "points": 1,
                    "exact_scores": 0,
                    "correct_results": 1,
                }
            ],
        )

        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                """
                CREATE TRIGGER fail_recalculated_score_insert
                BEFORE INSERT ON scores
                WHEN NEW.user_id = 'user-1'
                BEGIN
                    SELECT RAISE(FAIL, 'forced score insert failure');
                END
                """
            )
            await conn.commit()

        with pytest.raises(aiosqlite.IntegrityError, match="forced score insert failure"):
            await db.recalculate_fixture_scores(fixture_id)

        scores = await db.get_scores_for_fixture(fixture_id)
        assert [(score["user_id"], score["points"]) for score in scores] == [("original", 1)]

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

    @pytest.mark.asyncio
    async def test_standings_are_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
        await db.save_scores(
            old_fixture_id,
            [
                {
                    "user_id": "shared-user",
                    "user_name": "Old Shared User",
                    "points": 30,
                    "exact_scores": 10,
                    "correct_results": 0,
                }
            ],
        )
        await _start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        await db.save_scores(
            active_fixture_id,
            [
                {
                    "user_id": "shared-user",
                    "user_name": "Active Shared User",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )

        standings = await db.get_standings("111111")

        assert [row["user_id"] for row in standings] == ["shared-user"]
        assert standings[0]["user_name"] == "Active Shared User"
        assert standings[0]["total_points"] == 3

    @pytest.mark.asyncio
    async def test_last_fixture_scores_are_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await _start_new_active_season(temp_db_path, "111111")
        active_fixture_id = await db.create_fixture(
            "111111", 1, ["New Team A - New Team B"], datetime.now(UTC)
        )
        await db.save_scores(
            active_fixture_id,
            [
                {
                    "user_id": "active-user",
                    "user_name": "Active User",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 0,
                }
            ],
        )
        await _start_new_active_season(temp_db_path, "111111", "Archived Later Season")
        later_archived_fixture_id = await db.create_fixture(
            "111111", 1, ["Archived Team A - Archived Team B"], datetime.now(UTC)
        )
        await db.save_scores(
            later_archived_fixture_id,
            [
                {
                    "user_id": "archived-user",
                    "user_name": "Archived User",
                    "points": 30,
                    "exact_scores": 10,
                    "correct_results": 0,
                }
            ],
        )
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute(
                "UPDATE seasons SET status = 'archived' WHERE id = (SELECT season_id FROM fixtures WHERE id = ?)",
                (later_archived_fixture_id,),
            )
            await conn.execute(
                "UPDATE seasons SET status = 'active' WHERE id = (SELECT season_id FROM fixtures WHERE id = ?)",
                (active_fixture_id,),
            )
            await conn.commit()

        last_fixture = await db.get_last_fixture_scores("111111")

        assert last_fixture["fixture_id"] == active_fixture_id
        assert [score["user_id"] for score in last_fixture["scores"]] == ["active-user"]


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


class TestSchemaValidation:
    """Test suite for startup schema validation."""

    @pytest.mark.asyncio
    async def test_initialize_is_safe_for_current_schema_existing_data(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_prediction(
            fixture_id,
            "user-1",
            "User One",
            ["2-1"],
            public_message_id="message-1",
            public_message_kind="thread_prediction",
        )
        await db.save_results(fixture_id, ["2-1"])

        restarted_db = Database(temp_db_path)
        await restarted_db.initialize()
        await restarted_db.save_results(fixture_id, ["3-1"])

        fixture = await restarted_db.get_fixture_by_id(fixture_id, "111111")
        prediction = await restarted_db.get_prediction(fixture_id, "user-1", "111111")
        results = await restarted_db.get_results(fixture_id)

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
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
        await db.save_results(fixture_id, ["1-0"])
        async with aiosqlite.connect(temp_db_path) as conn:
            await conn.execute("DROP INDEX idx_results_fixture_id_unique")
            await conn.commit()

        await db.initialize()
        await db.save_results(fixture_id, ["2-0"])

        assert await db.get_results(fixture_id) == ["2-0"]
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
        fixture_id = await db.create_fixture("111111", 1, ["A - B"], datetime.now(UTC))
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
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_duplicate_when_prior_prediction_exists(self, prediction_db, open_fixture_id):
        await prediction_db.try_save_prediction(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.try_save_prediction(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.DUPLICATE
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
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
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1", "111111")
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


class TestPredictionSaveMetadata:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name",
        ["save_prediction", "try_save_prediction", "save_prediction_guarded"],
    )
    async def test_save_paths_preserve_non_default_metadata(
        self, prediction_db, open_fixture_id, method_name
    ):
        save = getattr(prediction_db, method_name)

        result = await save(
            open_fixture_id,
            "u1",
            "User",
            ["1-1"],
            True,
            predicted_game_indexes=[1],
            pending_partial_approval=True,
            public_message_id="message-1",
            public_message_kind="bot_post",
        )

        if method_name != "save_prediction":
            assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["user_name"] == "User"
        assert prediction["predictions"] == ["1-1"]
        assert prediction["is_late"] == 1
        assert prediction["predicted_game_indexes"] == [1]
        assert prediction["pending_partial_approval"] is True
        assert prediction["public_message_id"] == "message-1"
        assert prediction["public_message_kind"] == "bot_post"


class TestSavePredictionGuarded:
    """Upsert with fixture-open guard (DM re-submission path)."""

    @pytest.mark.asyncio
    async def test_saved_when_fixture_open(self, prediction_db, open_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["predictions"] == ["2-1", "0-0"]

    @pytest.mark.asyncio
    async def test_fixture_closed_blocks_write(self, prediction_db, closed_fixture_id):
        result = await prediction_db.save_prediction_guarded(
            closed_fixture_id, "u1", "User", ["2-1", "0-0"]
        )
        assert result == SaveResult.FIXTURE_CLOSED
        prediction = await prediction_db.get_prediction(closed_fixture_id, "u1", "111111")
        assert prediction is None

    @pytest.mark.asyncio
    async def test_allows_overwrite_of_existing_prediction(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(open_fixture_id, "u1", "User", ["2-1", "0-0"])
        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["3-0", "1-1"]
        )
        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["predictions"] == ["3-0", "1-1"]

    @pytest.mark.asyncio
    async def test_updates_user_name_on_resubmission(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "OldName", ["2-1", "0-0"]
        )
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "NewName", ["3-0", "1-1"]
        )
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction["user_name"] == "NewName"

    @pytest.mark.asyncio
    async def test_resubmission_clears_admin_and_waiver_metadata(
        self, prediction_db, open_fixture_id
    ):
        await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["2-1", "0-0"], True
        )
        await prediction_db.set_late_penalty_waiver(open_fixture_id, "u1", True)
        await prediction_db.admin_update_prediction(
            open_fixture_id, "u1", ["2-0", "1-1"], "admin-1"
        )
        async with aiosqlite.connect(prediction_db.db_path) as conn:
            await conn.execute(
                "UPDATE predictions SET submitted_at = '2000-01-01T00:00:00+00:00' WHERE fixture_id = ? AND user_id = ?",
                (open_fixture_id, "u1"),
            )
            await conn.commit()

        result = await prediction_db.save_prediction_guarded(
            open_fixture_id, "u1", "User", ["3-0", "1-1"], False
        )

        assert result == SaveResult.SAVED
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")
        assert prediction is not None
        assert prediction["late_penalty_waived"] == 0
        assert prediction["admin_edited_at"] is None
        assert prediction["admin_edited_by"] is None
        assert prediction["submitted_at"] > datetime(2000, 1, 1, tzinfo=UTC)


class TestPartialApprovalPending:
    @pytest.mark.asyncio
    async def test_clearing_pending_does_not_clear_late_status(
        self, prediction_db, open_fixture_id
    ):
        await prediction_db.save_prediction(
            open_fixture_id,
            "u1",
            "User",
            ["2-1", "0-0"],
            is_late=True,
            pending_partial_approval=True,
        )

        predictions = PredictionRepository(prediction_db.db_path)
        updated = await predictions.set_partial_approval_pending(open_fixture_id, "u1", False)
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")

        assert updated is True
        assert prediction is not None
        assert prediction["pending_partial_approval"] is False
        assert prediction["is_late"] == 1

    @pytest.mark.asyncio
    async def test_setting_pending_does_not_force_late_status(self, prediction_db, open_fixture_id):
        await prediction_db.save_prediction(
            open_fixture_id,
            "u1",
            "User",
            ["2-1", "0-0"],
            is_late=False,
            pending_partial_approval=False,
        )

        predictions = PredictionRepository(prediction_db.db_path)
        updated = await predictions.set_partial_approval_pending(open_fixture_id, "u1", True)
        prediction = await prediction_db.get_prediction(open_fixture_id, "u1", "111111")

        assert updated is True
        assert prediction is not None
        assert prediction["pending_partial_approval"] is True
        assert prediction["is_late"] == 0


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
    """Test edge cases in _row_to_fixture deserialization."""

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
