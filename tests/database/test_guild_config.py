import pytest

from typer_bot.database import Database


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
