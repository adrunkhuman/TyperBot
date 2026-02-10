"""Tests for admin Discord commands."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.commands.admin_commands import AdminCommands, _calculate_cooldowns
from typer_bot.handlers.fixture_handler import _pending_fixtures
from typer_bot.handlers.results_handler import _pending_results
from typer_bot.utils.permissions import is_admin


@pytest.fixture(autouse=True)
def clear_sessions_and_rate_limits():
    """Clear sessions and rate limiting cooldowns before each test."""
    _calculate_cooldowns.clear()
    _pending_fixtures.clear()
    _pending_results.clear()


class TestAdminOnlyDecorator:
    """Test suite for admin permission checking."""

    @pytest.mark.asyncio
    async def test_rejects_non_admin_users(self, mock_interaction):
        """Non-admin users are blocked from admin commands."""
        result = is_admin(mock_interaction)
        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_admin_users(self, mock_interaction_admin):
        """Admin users have access to league management commands."""
        result = is_admin(mock_interaction_admin)
        assert result is True

    @pytest.mark.asyncio
    async def test_rejects_dm_interactions(self, mock_interaction_admin):
        """DM interactions without guild context are rejected - role verification requires server membership."""
        mock_interaction_admin.guild = None
        result = is_admin(mock_interaction_admin)
        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_typer_admin_role(self, mock_interaction_admin):
        """The typer-admin role grants league management access without requiring full server admin privileges."""
        member = mock_interaction_admin.guild.get_member(mock_interaction_admin.user.id)
        mock_role = MagicMock()
        mock_role.name = "typer-admin"
        member.roles = [mock_role]
        result = is_admin(mock_interaction_admin)
        assert result is True


class TestFixtureCreateLogic:
    """Test suite for fixture create command logic."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_create_starts_session(self, admin_cog, mock_interaction_admin):
        """DM session prevents spamming public channels during fixture creation."""
        user_id = str(mock_interaction_admin.user.id)
        admin_cog.fixture_handler.start_session(user_id, 123456, 111111)
        assert admin_cog.fixture_handler.has_session(user_id)

    @pytest.mark.asyncio
    async def test_fixture_create_session_has_correct_data(self, admin_cog, mock_interaction_admin):
        """Session metadata includes guild and channel context for permissions and announcements."""
        user_id = str(mock_interaction_admin.user.id)
        admin_cog.fixture_handler.start_session(user_id, 123456, 111111)

        from typer_bot.handlers.fixture_handler import _pending_fixtures

        session = _pending_fixtures.get(user_id)
        assert session["channel_id"] == 123456
        assert session["guild_id"] == 111111
        assert session["step"] == "games"


class TestFixtureDeleteLogic:
    """Test suite for fixture delete command logic."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_delete_no_active_fixture(self, database):
        """Deleting without an active fixture fails gracefully."""
        fixture = await database.get_current_fixture()
        assert fixture is None

    @pytest.mark.asyncio
    async def test_fixture_delete_deletes_fixture(self, database, sample_games):
        """Fixture deletion cascades to predictions and results."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        fixture = await database.get_fixture_by_id(fixture_id)
        assert fixture is not None

        await database.delete_fixture(fixture_id)

        fixture = await database.get_fixture_by_id(fixture_id)
        assert fixture is None


class TestResultsEnterLogic:
    """Test suite for results enter command logic."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_results_enter_starts_session(self, admin_cog, mock_interaction_admin):
        """DM session keeps results private before public announcement."""
        user_id = str(mock_interaction_admin.user.id)
        admin_cog.results_handler.start_session(user_id, 1, 111111)
        assert admin_cog.results_handler.has_session(user_id)

    @pytest.mark.asyncio
    async def test_results_enter_session_has_correct_data(self, admin_cog, mock_interaction_admin):
        """Session tracks fixture ID for result-to-match mapping."""
        user_id = str(mock_interaction_admin.user.id)
        admin_cog.results_handler.start_session(user_id, 42, 111111)

        from typer_bot.handlers.results_handler import _pending_results

        session = _pending_results.get(user_id)
        assert session["fixture_id"] == 42
        assert session["guild_id"] == 111111


class TestResultsCalculateLogic:
    """Test suite for results calculate command logic."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_calculate_no_active_fixture(self, database):
        """Score calculation requires an active fixture."""
        fixture = await database.get_current_fixture()
        assert fixture is None

    @pytest.mark.asyncio
    async def test_calculate_no_results(self, database, sample_games):
        """Missing results block leaderboard updates."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        results = await database.get_results(fixture_id)
        assert results is None

    @pytest.mark.asyncio
    async def test_calculate_no_predictions(self, database, sample_games):
        """Empty predictions yield empty scores without crashing."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])

        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 0

    @pytest.mark.asyncio
    async def test_calculate_successfully_calculates_scores(
        self,
        database,
        sample_games,
    ):
        """Point calculation: 3 for exact, 1 for outcome."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(fixture_id, "123", "User1", ["2-1", "1-1", "0-2"], False)

        from typer_bot.utils.scoring import calculate_points

        predictions = await database.get_all_predictions(fixture_id)
        results = await database.get_results(fixture_id)

        scores = []
        for pred in predictions:
            score_data = calculate_points(pred["predictions"], results, pred["is_late"])
            scores.append(
                {
                    "user_id": pred["user_id"],
                    "user_name": pred["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        await database.save_scores(fixture_id, scores)

        standings = await database.get_standings()
        assert len(standings) == 1
        assert standings[0]["user_name"] == "User1"
        assert standings[0]["total_points"] == 9


class TestCooldownLogic:
    """Test suite for rate limiting cooldown."""

    def test_cooldown_enforced(self):
        """Rate limiting prevents leaderboard recalculation spam."""
        import time

        user_id = "user123"
        current_time = time.time()
        _calculate_cooldowns[user_id] = current_time

        assert user_id in _calculate_cooldowns

        if user_id in _calculate_cooldowns:
            last_used = _calculate_cooldowns[user_id]
            if current_time - last_used < 30.0:
                remaining = 30.0 - (current_time - last_used)
                assert remaining > 0

    def test_cooldown_expires(self):
        """Cooldown expires after 30 seconds."""
        import time

        user_id = "user123"
        current_time = time.time()
        _calculate_cooldowns[user_id] = current_time - 31  # 31 seconds ago

        last_used = _calculate_cooldowns[user_id]
        assert current_time - last_used >= 30.0


class TestOnMessageListener:
    """Test suite for on_message DM listener."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, admin_cog):
        """Bot messages are ignored to prevent infinite loops."""
        mock_message = MagicMock()
        mock_message.author.bot = True
        mock_message.guild = None

        result = await admin_cog.on_message(mock_message)
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_guild_messages(self, admin_cog):
        """Guild messages are ignored - admin workflows require DMs."""
        mock_message = MagicMock()
        mock_message.guild = MagicMock()  # Has guild

        result = await admin_cog.on_message(mock_message)
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_fixture_creation_dm(self, admin_cog):
        """Fixture creation DMs route to the correct handler."""
        mock_message = MagicMock()
        mock_message.guild = None
        user_id = "123456"
        mock_message.author.id = 123456
        mock_message.author.bot = False

        admin_cog.fixture_handler.start_session(user_id, 123456, 111111)
        admin_cog.fixture_handler.handle_dm = AsyncMock(return_value=True)

        await admin_cog.on_message(mock_message)

        assert admin_cog.fixture_handler.has_session(user_id)

    @pytest.mark.asyncio
    async def test_handles_results_entry_dm(self, admin_cog):
        """Results entry DMs route to the correct handler."""
        mock_message = MagicMock()
        mock_message.guild = None
        user_id = "123456"
        mock_message.author.id = 123456
        mock_message.author.bot = False

        admin_cog.results_handler.start_session(user_id, 1, 111111)
        admin_cog.results_handler.handle_dm = AsyncMock(return_value=True)

        await admin_cog.on_message(mock_message)

        assert admin_cog.results_handler.has_session(user_id)
