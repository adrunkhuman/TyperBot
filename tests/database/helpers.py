import aiosqlite


async def start_new_active_season(db_path: str, guild_id: str, name: str = "Next Season") -> int:
    """Create a new active season while bypassing production season guards."""
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
