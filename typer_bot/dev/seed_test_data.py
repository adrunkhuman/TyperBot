"""Reset and seed a local database for manual Discord testing."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from datetime import timedelta
from pathlib import Path

import aiosqlite

from typer_bot.database import Database
from typer_bot.utils import calculate_points, now
from typer_bot.utils.config import (
    BACKUP_DIR,
    DB_PATH,
    ENVIRONMENT,
    IS_PRODUCTION,
    SEED_TEST_DATA,
    TEST_GUILD_ID,
    TEST_USER_ID,
)
from typer_bot.utils.logger import setup_logging

SAMPLE_GAMES = [
    "Team A - Team B",
    "Team C - Team D",
    "Team E - Team F",
]

SYNTHETIC_USERS = [
    {"user_id": "seed-user-1", "user_name": "Seed Alpha"},
    {"user_id": "seed-user-2", "user_name": "Seed Beta"},
]

logger = logging.getLogger(__name__)
DEFAULT_MANUAL_TEST_DIR = Path(".local/manual-discord-test").resolve()
DEFAULT_MANUAL_GUILD_ID = "manual-test-guild"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset the configured database and seed mixed manual test data."
    )
    parser.add_argument(
        "--tester-user-id",
        help="Real Discord user ID to include in seeded open-fixture predictions.",
    )
    parser.add_argument(
        "--guild-id",
        default=DEFAULT_MANUAL_GUILD_ID,
        help="Discord guild ID to assign to seeded fixtures.",
    )
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Allow resetting a database outside ./.local/manual-discord-test.",
    )
    return parser


def _ensure_safe_reset_target(db_path: str, backup_dir: str, force_reset: bool) -> None:
    db_parent = Path(db_path).resolve().parent
    backup_path = Path(backup_dir).resolve()
    if force_reset:
        return

    if db_parent == DEFAULT_MANUAL_TEST_DIR and backup_path == DEFAULT_MANUAL_TEST_DIR / "backups":
        return

    message = (
        "Refusing to reset a non-default database without --force-reset. "
        f"DB_PATH={Path(db_path).resolve()} BACKUP_DIR={backup_path}"
    )
    raise ValueError(message)


def _reset_database_files(db_path: str, backup_dir: str, force_reset: bool) -> None:
    _ensure_safe_reset_target(db_path, backup_dir, force_reset)

    db_file = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        db_file.with_name(f"{db_file.name}{suffix}").unlink(missing_ok=True)

    backup_path = Path(backup_dir)
    if backup_path.exists():
        shutil.rmtree(backup_path)


def _build_seed_users() -> list[dict[str, str]]:
    return [user.copy() for user in SYNTHETIC_USERS]


async def _database_has_seedable_data(db_path: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        for table_name in (
            "guild_config",
            "seasons",
            "fixtures",
            "predictions",
            "results",
            "scores",
        ):
            async with db.execute(f"SELECT 1 FROM {table_name} LIMIT 1") as cursor:
                if await cursor.fetchone() is not None:
                    return True
    return False


async def _seed_mixed_rows(db: Database, tester_user_id: str | None, guild_id: str) -> None:
    current_time = now()
    users = _build_seed_users()

    scored_fixture_id = await db.fixtures.create_fixture(
        guild_id,
        1,
        SAMPLE_GAMES,
        current_time - timedelta(days=7),
    )
    scored_results = ["2-1", "1-1", "0-2"]
    scored_predictions = {
        users[0]["user_id"]: ["2-1", "1-1", "0-2"],
        users[1]["user_id"]: ["1-0", "1-1", "1-2"],
    }
    scored_scores: list[dict[str, int | str]] = []
    for user in users:
        predictions = scored_predictions[user["user_id"]]
        await db.predictions.save_prediction(
            scored_fixture_id,
            user["user_id"],
            user["user_name"],
            predictions,
            False,
        )
        score_data = calculate_points(predictions, scored_results, False)
        scored_scores.append(
            {
                "user_id": user["user_id"],
                "user_name": user["user_name"],
                "points": score_data["points"],
                "exact_scores": score_data["exact_scores"],
                "correct_results": score_data["correct_results"],
            }
        )

    scored_scores.sort(
        key=lambda score: (
            -int(score["points"]),
            -int(score["exact_scores"]),
            -int(score["correct_results"]),
            str(score["user_name"]).lower(),
        )
    )
    await db.results.save_results(scored_fixture_id, scored_results)
    await db.scores.save_scores(scored_fixture_id, scored_scores)

    open_fixture_id = await db.fixtures.create_fixture(
        guild_id,
        2,
        SAMPLE_GAMES,
        current_time + timedelta(days=2),
    )
    open_fixture_users = list(users)
    if tester_user_id:
        open_fixture_users.insert(0, {"user_id": tester_user_id, "user_name": "Manual Tester"})

    open_predictions = [
        (open_fixture_users[0], ["1-0", "2-1", "0-0"], False),
        (open_fixture_users[1], ["2-0", "2-2", "1-1"], False),
    ]
    if len(open_fixture_users) > 2:
        open_predictions.append((open_fixture_users[2], ["0-1", "1-1", "0-2"], False))

    for user, predictions, is_late in open_predictions:
        await db.predictions.save_prediction(
            open_fixture_id,
            user["user_id"],
            user["user_name"],
            predictions,
            is_late,
        )

    late_fixture_id = await db.fixtures.create_fixture(
        guild_id,
        3,
        SAMPLE_GAMES,
        current_time - timedelta(hours=3),
    )
    await db.predictions.save_prediction(
        late_fixture_id,
        users[1]["user_id"],
        users[1]["user_name"],
        ["3-1", "0-0", "1-2"],
        True,
    )


async def maybe_auto_seed_test_data(
    db_path: str,
    *,
    enabled: bool = SEED_TEST_DATA,
    environment: str = ENVIRONMENT,
    is_production: bool = IS_PRODUCTION,
    guild_id: str | None = TEST_GUILD_ID,
    tester_user_id: str | None = TEST_USER_ID,
) -> bool:
    """Seed disposable non-production data when explicitly enabled and empty."""
    if not enabled:
        return False
    if is_production:
        logger.warning("Skipping test data auto-seed in production environment")
        return False
    if not guild_id:
        raise RuntimeError("TEST_GUILD_ID is required when SEED_TEST_DATA is enabled.")
    if await _database_has_seedable_data(db_path):
        logger.info("Skipping test data auto-seed because database is not empty")
        return False

    await _seed_mixed_rows(Database(db_path), tester_user_id, guild_id)
    logger.info(
        "Seeded non-production test data", extra={"environment": environment, "guild_id": guild_id}
    )
    return True


async def seed_mixed_test_data(
    db_path: str,
    backup_dir: str,
    tester_user_id: str | None,
    guild_id: str = DEFAULT_MANUAL_GUILD_ID,
    *,
    force_reset: bool = False,
) -> None:
    """Reset the target SQLite files and seed one mixed manual-testing scenario.

    Args:
        db_path: SQLite database file to recreate and seed.
        backup_dir: Backup directory removed before reseeding.
        tester_user_id: Real Discord user ID added only to the open-fixture seed data.
        guild_id: Discord guild ID assigned to seeded fixtures.
        force_reset: Allows resetting paths outside ``./.local/manual-discord-test``.

    Raises:
        ValueError: Target paths are outside the default manual-test directory and
            ``force_reset`` is not enabled.

    Notes:
        The reset removes the database file, its WAL/SHM sidecars, and the backup
        directory. The seed creates three fixtures: one scored past fixture, one
        open fixture with saved predictions, and one overdue fixture with a late
        prediction. This touches SQLite only. It does not post announcements,
        create threads, or run any Discord workflows.
    """
    _reset_database_files(db_path, backup_dir, force_reset)

    db = Database(db_path)
    await db.initialize()
    await _seed_mixed_rows(db, tester_user_id, guild_id)


async def _async_main(tester_user_id: str | None, guild_id: str, force_reset: bool) -> None:
    await seed_mixed_test_data(
        DB_PATH,
        BACKUP_DIR,
        tester_user_id,
        guild_id,
        force_reset=force_reset,
    )
    logger.info("Seeded mixed manual test data into %s", DB_PATH)


def main() -> None:
    setup_logging()
    args = _build_argument_parser().parse_args()
    asyncio.run(_async_main(args.tester_user_id, args.guild_id, args.force_reset))


if __name__ == "__main__":
    main()
