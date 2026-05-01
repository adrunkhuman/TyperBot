"""Guild configuration repository."""

import aiosqlite


class GuildConfigRepository:
    """CRUD for per-guild bot configuration."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def upsert_guild_config(
        self,
        guild_id: str,
        admin_role_id: str,
        league_channel_id: str,
    ) -> None:
        """Create or replace the minimal config for one guild."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO guild_config (guild_id, admin_role_id, league_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    admin_role_id = excluded.admin_role_id,
                    league_channel_id = excluded.league_channel_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, admin_role_id, league_channel_id),
            )
            await db.commit()

    async def get_guild_config(self, guild_id: str) -> dict | None:
        """Return stored config for one guild, if setup has been completed."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT guild_id, admin_role_id, league_channel_id, created_at, updated_at
                FROM guild_config
                WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cursor:
                row = await cursor.fetchone()

        return dict(row) if row else None
