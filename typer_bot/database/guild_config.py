import aiosqlite


class GuildConfigRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def upsert_guild_config(
        self,
        guild_id: str,
        admin_role_id: str,
        league_channel_id: str,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO guild_config (guild_id, admin_role_id, league_channel_id, active_season_id)
                VALUES (
                    ?,
                    ?,
                    ?,
                    (SELECT id FROM seasons WHERE guild_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1)
                )
                ON CONFLICT(guild_id) DO UPDATE SET
                    admin_role_id = excluded.admin_role_id,
                    league_channel_id = excluded.league_channel_id,
                    active_season_id = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM seasons s
                            WHERE s.id = guild_config.active_season_id
                              AND s.guild_id = guild_config.guild_id
                              AND s.status = 'active'
                        ) THEN guild_config.active_season_id
                        ELSE excluded.active_season_id
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (guild_id, admin_role_id, league_channel_id, guild_id),
            )
            await db.commit()

    async def get_guild_config(self, guild_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT guild_id, admin_role_id, league_channel_id, active_season_id, created_at, updated_at
                FROM guild_config
                WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cursor:
                row = await cursor.fetchone()

        return dict(row) if row else None
