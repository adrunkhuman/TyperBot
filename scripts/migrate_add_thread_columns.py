"""Migration script to add thread_id and announcement_message_id columns to fixtures table."""

import os
from pathlib import Path

import aiosqlite

# Get database path from environment or use default
DB_PATH = os.getenv("DB_PATH", "/app/data/typer.db")


async def migrate():
    """Add missing columns to fixtures table."""
    print(f"Migrating database at: {DB_PATH}")

    # Ensure directory exists
    db_dir = Path(DB_PATH).parent
    if db_dir and not db_dir.exists():
        db_dir.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        # Check if columns exist
        async with db.execute("PRAGMA table_info(fixtures)") as cursor:
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            print(f"Existing columns: {column_names}")

            # Add announcement_message_id if missing
            if "announcement_message_id" not in column_names:
                print("Adding announcement_message_id column...")
                await db.execute("ALTER TABLE fixtures ADD COLUMN announcement_message_id TEXT")
                print("✓ announcement_message_id added")
            else:
                print("✓ announcement_message_id already exists")

            # Add thread_id if missing
            if "thread_id" not in column_names:
                print("Adding thread_id column...")
                await db.execute("ALTER TABLE fixtures ADD COLUMN thread_id TEXT")
                print("✓ thread_id added")
            else:
                print("✓ thread_id already exists")

            await db.commit()
            print("\nMigration complete!")


if __name__ == "__main__":
    import asyncio

    asyncio.run(migrate())
