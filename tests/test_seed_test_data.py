"""Tests for the manual Discord test data seeder."""

from pathlib import Path

import pytest

from typer_bot.dev.seed_test_data import (
    DEFAULT_MANUAL_GUILD_ID,
    maybe_auto_seed_test_data,
    seed_mixed_test_data,
)
from typer_bot.utils import now


def _write_cleanup_artifacts(temp_db_path: str, backup_dir: Path) -> None:
    Path(f"{temp_db_path}-wal").write_text("wal", encoding="utf-8")
    Path(f"{temp_db_path}-shm").write_text("shm", encoding="utf-8")
    (backup_dir / "backup.sql").write_text("old", encoding="utf-8")


def _cleanup_artifacts_removed(temp_db_path: str, backup_dir: Path) -> bool:
    return not any(
        [
            Path(f"{temp_db_path}-wal").exists(),
            Path(f"{temp_db_path}-shm").exists(),
            (backup_dir / "backup.sql").exists(),
        ]
    )


@pytest.mark.asyncio
async def test_seed_mixed_data_creates_expected_fixture_states(temp_db_path, tmp_path):
    backup_dir = tmp_path / "backups"

    await seed_mixed_test_data(temp_db_path, str(backup_dir), None, force_reset=True)

    from typer_bot.database import Database

    db = Database(temp_db_path)
    open_fixtures = await db.fixtures.get_open_fixtures(DEFAULT_MANUAL_GUILD_ID)
    standings = await db.scores.get_standings(DEFAULT_MANUAL_GUILD_ID)
    week_one = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 1)
    week_two = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 2)
    week_three = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 3)

    assert [fixture["week_number"] for fixture in open_fixtures] == [2, 3]
    assert week_one is not None
    assert week_one["status"] == "closed"
    assert week_two is not None
    assert week_two["status"] == "open"
    assert week_three is not None
    assert week_three["status"] == "open"
    assert len(standings) == 2
    assert standings[0]["user_name"] == "Seed Alpha"
    assert standings[0]["total_points"] == 9
    assert standings[1]["user_name"] == "Seed Beta"
    assert standings[1]["total_points"] == 5

    week_one_results = await db.results.get_results(week_one["id"])
    week_one_scores = await db.scores.get_scores_for_fixture(week_one["id"])

    open_predictions = await db.predictions.get_all_predictions(week_two["id"])
    late_predictions = await db.predictions.get_all_predictions(week_three["id"])

    assert week_one_results == ["2-1", "1-1", "0-2"]
    assert [score["user_name"] for score in week_one_scores] == ["Seed Alpha", "Seed Beta"]
    assert [score["points"] for score in week_one_scores] == [9, 5]
    assert len(open_predictions) == 2
    assert len(late_predictions) == 1
    assert late_predictions[0]["is_late"] == 1


@pytest.mark.asyncio
async def test_seed_mixed_data_includes_real_tester_when_user_id_provided(temp_db_path, tmp_path):
    backup_dir = tmp_path / "backups"
    tester_user_id = "123456789012345678"

    await seed_mixed_test_data(temp_db_path, str(backup_dir), tester_user_id, force_reset=True)

    from typer_bot.database import Database

    db = Database(temp_db_path)
    week_one = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 1)
    week_two = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 2)
    assert week_one is not None
    assert week_two is not None

    tester_scored_prediction = await db.predictions.get_prediction(
        week_one["id"], tester_user_id, DEFAULT_MANUAL_GUILD_ID
    )
    tester_prediction = await db.predictions.get_prediction(
        week_two["id"], tester_user_id, DEFAULT_MANUAL_GUILD_ID
    )
    synthetic_prediction = await db.predictions.get_prediction(
        week_two["id"], "seed-user-1", DEFAULT_MANUAL_GUILD_ID
    )

    assert tester_scored_prediction is None
    assert tester_prediction is not None
    assert tester_prediction["user_name"] == "Manual Tester"
    assert synthetic_prediction is not None


@pytest.mark.asyncio
async def test_seed_mixed_data_resets_existing_database(temp_db_path, tmp_path):
    backup_dir = tmp_path / "backups"

    await seed_mixed_test_data(temp_db_path, str(backup_dir), None, force_reset=True)

    from typer_bot.database import Database

    db = Database(temp_db_path)
    week_two = await db.fixtures.get_fixture_by_week(DEFAULT_MANUAL_GUILD_ID, 2)
    assert week_two is not None
    await db.fixtures.delete_fixture(week_two["id"])

    await seed_mixed_test_data(temp_db_path, str(backup_dir), None, force_reset=True)

    refreshed_db = Database(temp_db_path)
    open_fixtures = await refreshed_db.fixtures.get_open_fixtures(DEFAULT_MANUAL_GUILD_ID)
    assert [fixture["week_number"] for fixture in open_fixtures] == [2, 3]


@pytest.mark.asyncio
async def test_seed_mixed_data_cleans_wal_shm_and_backups(temp_db_path, tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _write_cleanup_artifacts(temp_db_path, backup_dir)

    await seed_mixed_test_data(temp_db_path, str(backup_dir), None, force_reset=True)

    assert _cleanup_artifacts_removed(temp_db_path, backup_dir)


@pytest.mark.asyncio
async def test_seed_mixed_data_refuses_non_default_reset_without_force(tmp_path):
    db_path = tmp_path / "manual.db"
    backup_dir = tmp_path / "backups"

    with pytest.raises(ValueError, match="--force-reset"):
        await seed_mixed_test_data(str(db_path), str(backup_dir), None)


@pytest.mark.asyncio
async def test_auto_seed_skips_when_disabled(tmp_path):
    db_path = tmp_path / "preview.db"

    seeded = await maybe_auto_seed_test_data(str(db_path), enabled=False)

    assert seeded is False
    assert not db_path.exists()


@pytest.mark.asyncio
async def test_auto_seed_skips_in_production(tmp_path):
    db_path = tmp_path / "preview.db"

    seeded = await maybe_auto_seed_test_data(
        str(db_path),
        enabled=True,
        is_production=True,
        environment="production",
        guild_id="111111",
    )

    assert seeded is False
    assert not db_path.exists()


@pytest.mark.asyncio
async def test_auto_seed_requires_guild_id(temp_db_path):
    from typer_bot.database import Database

    db = Database(temp_db_path)
    await db.initialize()

    with pytest.raises(RuntimeError, match="TEST_GUILD_ID"):
        await maybe_auto_seed_test_data(
            temp_db_path,
            enabled=True,
            is_production=False,
            environment="preview",
            guild_id=None,
        )


@pytest.mark.asyncio
async def test_auto_seed_populates_empty_non_production_database(temp_db_path):
    from typer_bot.database import Database

    db = Database(temp_db_path)
    await db.initialize()

    seeded = await maybe_auto_seed_test_data(
        temp_db_path,
        enabled=True,
        is_production=False,
        environment="preview",
        guild_id="111111",
        tester_user_id="tester-1",
    )

    seeded_db = Database(temp_db_path)
    open_fixtures = await seeded_db.fixtures.get_open_fixtures("111111")
    week_two = await seeded_db.fixtures.get_fixture_by_week("111111", 2)

    assert seeded is True
    assert [fixture["week_number"] for fixture in open_fixtures] == [2, 3]
    assert week_two is not None
    assert (
        await seeded_db.predictions.get_prediction(week_two["id"], "tester-1", "111111") is not None
    )


@pytest.mark.asyncio
async def test_auto_seed_does_not_reset_non_empty_database(temp_db_path):
    from typer_bot.database import Database

    db = Database(temp_db_path)
    await db.initialize()
    fixture_id = await db.fixtures.create_fixture("111111", 99, ["Existing A - Existing B"], now())

    seeded = await maybe_auto_seed_test_data(
        temp_db_path,
        enabled=True,
        is_production=False,
        environment="preview",
        guild_id="111111",
    )

    existing_fixture = await db.fixtures.get_fixture_by_id(fixture_id, "111111")
    open_fixtures = await db.fixtures.get_open_fixtures("111111")

    assert seeded is False
    assert existing_fixture is not None
    assert [fixture["week_number"] for fixture in open_fixtures] == [99]
