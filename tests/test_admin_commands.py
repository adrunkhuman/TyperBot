"""Tests for admin Discord commands."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.commands.admin_commands import CALCULATE_COOLDOWN, AdminCommands
from typer_bot.commands.admin_panel import OpenFixtureWarningView
from typer_bot.utils.permissions import is_admin


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

        session = admin_cog.fixture_handler.get_session(user_id)
        assert session.channel_id == 123456
        assert session.guild_id == 111111
        assert session.step == "games"

    @pytest.mark.asyncio
    async def test_fixture_create_shows_warning_when_open_fixture_exists(
        self, admin_cog, mock_interaction_admin, sample_games
    ):
        """Accidental duplicate fixtures are blocked by a confirmation step."""
        mock_interaction_admin.channel_id = int(mock_interaction_admin.channel.id)
        mock_interaction_admin.guild_id = mock_interaction_admin.guild.id
        await admin_cog.db.create_fixture(5, sample_games, datetime.now(UTC) + timedelta(days=1))

        await admin_cog.fixture_create.callback(admin_cog, mock_interaction_admin)

        response = mock_interaction_admin.response_sent[-1]
        assert "already open" in response["content"]
        assert isinstance(response["view"], OpenFixtureWarningView)

    @pytest.mark.asyncio
    async def test_fixture_create_proceeds_directly_when_no_open_fixtures(
        self, admin_cog, mock_interaction_admin
    ):
        """When no fixtures are open the DM session starts without any confirmation gate."""
        mock_interaction_admin.channel_id = int(mock_interaction_admin.channel.id)
        mock_interaction_admin.guild_id = mock_interaction_admin.guild.id
        mock_interaction_admin.user.send = AsyncMock()

        await admin_cog.fixture_create.callback(admin_cog, mock_interaction_admin)

        response = mock_interaction_admin.response_sent[-1]
        assert "Check your DMs" in response["content"]
        assert admin_cog.fixture_handler.has_session(str(mock_interaction_admin.user.id))


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

        session = admin_cog.results_handler.get_session(user_id)
        assert session.fixture_id == 42
        assert session.guild_id == 111111


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

    def test_cooldown_enforced(self, workflow_state):
        """Rate limiting prevents leaderboard recalculation spam."""
        import time

        user_id = "user123"
        current_time = time.time()
        workflow_state.record_calculate_cooldown(user_id, current_time=current_time)

        remaining = workflow_state.get_calculate_cooldown_remaining(
            user_id,
            current_time=current_time,
            cooldown_seconds=CALCULATE_COOLDOWN,
        )
        assert remaining > 0

    def test_cooldown_expires(self, workflow_state):
        """Cooldown expires after 30 seconds."""
        import time

        user_id = "user123"
        current_time = time.time()
        workflow_state.record_calculate_cooldown(user_id, current_time=current_time - 31)

        remaining = workflow_state.get_calculate_cooldown_remaining(
            user_id,
            current_time=current_time,
            cooldown_seconds=CALCULATE_COOLDOWN,
        )
        assert remaining == 0.0


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


class TestMultiOpenFixtureTargeting:
    """Test suite for explicit week targeting when multiple fixtures are open."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_delete_requires_week_when_multiple_open(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Delete command blocks ambiguous actions when multiple fixtures are open."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        await admin_cog.db.create_fixture(1, sample_games, deadline)
        await admin_cog.db.create_fixture(2, sample_games, deadline)

        await admin_cog.fixture_delete.callback(admin_cog, mock_interaction_admin, None)

        assert len(mock_interaction_admin.response_sent) == 1
        content = mock_interaction_admin.response_sent[0]["content"]
        assert "Multiple fixtures are currently open" in content
        assert "Open weeks: 1, 2" in content

    @pytest.mark.asyncio
    async def test_results_enter_targets_explicit_week(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Results entry session starts for the requested open fixture week."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_week_1 = await admin_cog.db.create_fixture(1, sample_games, deadline)
        await admin_cog.db.create_fixture(2, sample_games, deadline)

        mock_interaction_admin.guild_id = mock_interaction_admin.guild.id

        await admin_cog.results_enter.callback(admin_cog, mock_interaction_admin, 1)

        user_id = str(mock_interaction_admin.user.id)
        assert admin_cog.results_handler.get_session(user_id).fixture_id == fixture_week_1
        assert "Check your DMs" in mock_interaction_admin.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_results_calculate_requires_week_when_multiple_open(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Calculate command requires explicit week when more than one fixture is open."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        await admin_cog.db.create_fixture(1, sample_games, deadline)
        await admin_cog.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_interaction_admin.user.id)
        await admin_cog.results_calculate.callback(admin_cog, mock_interaction_admin, None)

        assert len(mock_interaction_admin.response_sent) == 1
        content = mock_interaction_admin.response_sent[0]["content"]
        assert "Multiple fixtures are currently open" in content
        assert admin_cog.workflow_state.get_calculate_cooldown(user_id) is None

    @pytest.mark.asyncio
    async def test_results_enter_rejects_duplicate_open_week_numbers(
        self,
        admin_cog,
        mock_interaction_admin,
        sample_games,
    ):
        """Duplicate open week numbers are rejected to avoid targeting wrong fixture."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        await admin_cog.db.create_fixture(5, sample_games, deadline)
        await admin_cog.db.create_fixture(5, sample_games, deadline)

        await admin_cog.results_enter.callback(admin_cog, mock_interaction_admin, 5)

        assert len(mock_interaction_admin.response_sent) == 1
        content = mock_interaction_admin.response_sent[0]["content"]
        assert "More than one open fixture was found for week 5" in content
