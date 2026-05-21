from datetime import UTC, datetime

import aiosqlite
import pytest

from tests.database.helpers import start_new_active_season
from typer_bot.database import Database
from typer_bot.database import scores as scores_module


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
        await start_new_active_season(temp_db_path, "111111")
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
    async def test_get_standings_for_season_returns_archived_season_scores(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        old_fixture_id = await db.create_fixture(
            "111111", 1, ["Old Team A - Old Team B"], datetime.now(UTC)
        )
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
        old_season = await db.get_active_season("111111")
        await start_new_active_season(temp_db_path, "111111")
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

        standings = await db.get_standings_for_season("111111", old_season["id"])
        wrong_guild_standings = await db.get_standings_for_season("222222", old_season["id"])

        assert [row["user_id"] for row in standings] == ["old-user"]
        assert standings[0]["total_points"] == 30
        assert wrong_guild_standings == []

    @pytest.mark.asyncio
    async def test_last_fixture_scores_are_active_season_scoped(self, temp_db_path):
        db = Database(temp_db_path)
        await db.initialize()
        await start_new_active_season(temp_db_path, "111111")
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
        await start_new_active_season(temp_db_path, "111111", "Archived Later Season")
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
