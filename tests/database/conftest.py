import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from typer_bot.database import Database


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
async def prediction_db(temp_db_path):
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
