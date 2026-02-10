"""Tests for admin Discord commands."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from typer_bot.commands.admin_commands import (
    AdminCommands,
    DeleteConfirmView,
    PostResultsConfirmView,
    _calculate_cooldowns,
)
from typer_bot.utils.permissions import is_admin


@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limiting cooldowns before each test."""
    _calculate_cooldowns.clear()


class TestAdminOnlyDecorator:
    """Test suite for admin permission checking."""

    @pytest.mark.asyncio
    async def test_rejects_non_admin_users(self, mock_interaction):
        """Should reject users without admin role."""
        mock_interaction.guild = mock_interaction.guild

        result = is_admin(mock_interaction)

        assert result is False

    @pytest.mark.asyncio
    async def test_accepts_admin_users(self, mock_interaction_admin):
        """Should accept users with admin role."""
        result = is_admin(mock_interaction_admin)

        assert result is True

    @pytest.mark.asyncio
    async def test_rejects_dm_interactions(self, mock_interaction_admin):
        """Should reject interactions from DMs (no guild)."""
        mock_interaction_admin.guild = None

        result = is_admin(mock_interaction_admin)

        assert result is False

    @pytest.mark.asyncio
    async def test_rejects_typer_admin_role(self, mock_interaction_admin):
        """Should accept users with typer-admin role."""
        # Change role name to typer-admin
        mock_interaction_admin.user.roles = [MagicMock(name="typer-admin", spec_set=["name"])]
        mock_interaction_admin.user.roles[0].name = "typer-admin"

        result = is_admin(mock_interaction_admin)

        assert result is True


class TestFixtureCreateCommand:
    """Test suite for /admin fixture create command."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_create_initiates_dm_workflow(
        self, admin_cog, mock_interaction_admin, mock_guild_with_members
    ):
        """Should start fixture creation session and send DM instructions."""
        # Add admin member to guild
        mock_guild_with_members.add_member(str(mock_interaction_admin.user.id), roles=["admin"])
        mock_interaction_admin.guild = mock_guild_with_members

        # Mock the response method
        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        # Mock user's send method for DM
        mock_interaction_admin.user.send = AsyncMock()

        await admin_cog.fixture_create(mock_interaction_admin)

        # Verify session was started
        assert admin_cog.fixture_handler.has_session(str(mock_interaction_admin.user.id))

        # Verify response was sent
        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "Check your DMs" in call_args.kwargs.get("content", "")
        assert call_args.kwargs.get("ephemeral") is True

        # Verify DM was sent
        mock_interaction_admin.user.send.assert_called_once()
        dm_content = mock_interaction_admin.user.send.call_args[0][0]
        assert "Create New Fixture" in dm_content
        assert "Step 1/2" in dm_content

    @pytest.mark.asyncio
    async def test_fixture_create_handles_dm_forbidden(
        self, admin_cog, mock_interaction_admin, mock_guild_with_members
    ):
        """Should handle when user has DMs disabled."""
        mock_guild_with_members.add_member(str(mock_interaction_admin.user.id), roles=["admin"])
        mock_interaction_admin.guild = mock_guild_with_members

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()
        mock_interaction_admin.followup = MagicMock()
        mock_interaction_admin.followup.send = AsyncMock()

        # Simulate DM forbidden
        async def raise_forbidden(*args, **kwargs):
            raise discord.Forbidden(MagicMock(), "Cannot send DMs")

        mock_interaction_admin.user.send = raise_forbidden

        await admin_cog.fixture_create(mock_interaction_admin)

        # Session should be cancelled
        assert not admin_cog.fixture_handler.has_session(str(mock_interaction_admin.user.id))

        # Followup should be sent with error
        mock_interaction_admin.followup.send.assert_called_once()
        call_args = mock_interaction_admin.followup.send.call_args
        assert "can't send you DMs" in call_args.kwargs.get("content", "").lower()


class TestFixtureDeleteCommand:
    """Test suite for /admin fixture delete command."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_fixture_delete_no_active_fixture(self, admin_cog, mock_interaction_admin):
        """Should error when no active fixture exists."""
        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.fixture_delete(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No active fixture found" in call_args.kwargs.get("content", "")
        assert call_args.kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_fixture_delete_shows_confirmation_view(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should show confirmation view with fixture details."""
        # Create a fixture
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.fixture_delete(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        content = call_args.kwargs.get("content", "")
        view = call_args.kwargs.get("view")

        assert "Delete Week 1" in content
        assert "Team A - Team B" in content
        assert view is not None
        assert isinstance(view, DeleteConfirmView)


class TestDeleteConfirmView:
    """Test suite for DeleteConfirmView interactions."""

    @pytest.fixture
    def delete_view(self, database):
        """Provide a DeleteConfirmView instance."""
        return DeleteConfirmView(database, "123456", 1, 1)

    @pytest.mark.asyncio
    async def test_confirm_deletes_fixture(self, delete_view, database, sample_games):
        """Should delete fixture when confirmed."""
        # Create a fixture
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        delete_view.fixture_id = fixture_id

        # Mock interaction
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.id = 123456
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        await delete_view.confirm(mock_interaction, MagicMock())

        # Verify fixture was deleted
        fixture = await database.get_fixture_by_id(fixture_id)
        assert fixture is None

        # Verify message was edited
        mock_interaction.response.edit_message.assert_called_once()
        call_args = mock_interaction.response.edit_message.call_args
        assert "Deleted" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_confirm_wrong_user_rejected(self, delete_view):
        """Should reject confirmation from wrong user."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.id = 999999  # Different user
        mock_interaction.response = MagicMock()
        mock_interaction.response.send_message = AsyncMock()

        await delete_view.confirm(mock_interaction, MagicMock())

        mock_interaction.response.send_message.assert_called_once()
        call_args = mock_interaction.response.send_message.call_args
        assert "permission" in call_args.kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_cancel_keeps_fixture(self, delete_view, database, sample_games):
        """Should keep fixture when cancelled."""
        # Create a fixture
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        delete_view.fixture_id = fixture_id

        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.id = 123456
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        await delete_view.cancel(mock_interaction, MagicMock())

        # Verify fixture still exists
        fixture = await database.get_fixture_by_id(fixture_id)
        assert fixture is not None


class TestResultsEnterCommand:
    """Test suite for /admin results enter command."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_results_enter_no_active_fixture(self, admin_cog, mock_interaction_admin):
        """Should error when no active fixture exists."""
        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_enter(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No active fixture found" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_results_enter_already_has_results(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should error when results already entered."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_enter(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "Results already entered" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_results_enter_initiates_dm_workflow(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should start results entry session and send DM."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()
        mock_interaction_admin.user.send = AsyncMock()

        await admin_cog.results_enter(mock_interaction_admin)

        # Verify session was started
        assert admin_cog.results_handler.has_session(str(mock_interaction_admin.user.id))

        # Verify DM was sent with instructions
        mock_interaction_admin.user.send.assert_called_once()
        dm_content = mock_interaction_admin.user.send.call_args[0][0]
        assert "Enter Results" in dm_content
        assert sample_games[0] in dm_content


class TestResultsCalculateCommand:
    """Test suite for /admin results calculate command."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        mock_bot.loop = MagicMock()
        mock_bot.loop.run_in_executor = AsyncMock(return_value=None)
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_calculate_no_active_fixture(self, admin_cog, mock_interaction_admin):
        """Should error when no active fixture exists."""
        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_calculate(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No active fixture found" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_calculate_no_results_entered(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should error when no results have been entered."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_calculate(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No results entered" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_calculate_no_predictions(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should error when no predictions exist."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_calculate(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No predictions found" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_calculate_enforces_cooldown(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should enforce 30 second cooldown between calculations."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(fixture_id, "123", "User1", "2-1\n1-1\n0-2", False)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()
        mock_interaction_admin.channel = MagicMock()
        mock_interaction_admin.channel.send = AsyncMock()

        # First calculation
        await admin_cog.results_calculate(mock_interaction_admin)

        # Second calculation immediately
        mock_interaction_admin.response.send_message.reset_mock()
        await admin_cog.results_calculate(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "wait" in call_args.kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_calculate_successfully_calculates_scores(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should calculate and save scores successfully."""
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(fixture_id, "123", "User1", "2-1\n1-1\n0-2", False)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()
        mock_interaction_admin.channel = MagicMock()
        mock_interaction_admin.channel.send = AsyncMock()

        await admin_cog.results_calculate(mock_interaction_admin)

        # Verify scores were saved
        scores = await database.get_standings()
        assert len(scores) == 1
        assert scores[0]["user_name"] == "User1"
        assert scores[0]["points"] == 9  # 3 exact scores * 3 points

        # Verify success message
        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "results calculated" in call_args.kwargs.get("content", "").lower()


class TestResultsPostCommand:
    """Test suite for /admin results post command."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_results_post_no_completed_fixtures(self, admin_cog, mock_interaction_admin):
        """Should error when no completed fixtures exist."""
        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()

        await admin_cog.results_post(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        assert "No completed fixtures found" in call_args.kwargs.get("content", "")

    @pytest.mark.asyncio
    async def test_results_post_shows_preview_with_confirmation(
        self, admin_cog, mock_interaction_admin, database, sample_games
    ):
        """Should show preview with confirmation view."""
        deadline = datetime.now(UTC) - timedelta(days=1)  # Past deadline
        fixture_id = await database.create_fixture(1, sample_games, deadline)
        await database.save_results(fixture_id, ["2-1", "1-1", "0-2"])
        await database.save_prediction(fixture_id, "123", "User1", "2-1\n1-1\n0-2", False)

        # Close fixture by calculating scores
        from typer_bot.utils.scoring import calculate_points

        scores = [
            {
                "user_id": "123",
                "user_name": "User1",
                "points": 9,
                "exact_scores": 3,
                "correct_results": 0,
            }
        ]
        await database.save_scores(fixture_id, scores)

        mock_interaction_admin.response = MagicMock()
        mock_interaction_admin.response.send_message = AsyncMock()
        mock_interaction_admin.channel = MagicMock()

        await admin_cog.results_post(mock_interaction_admin)

        mock_interaction_admin.response.send_message.assert_called_once()
        call_args = mock_interaction_admin.response.send_message.call_args
        content = call_args.kwargs.get("content", "")
        view = call_args.kwargs.get("view")

        assert "Mention users" in content
        assert view is not None
        assert isinstance(view, PostResultsConfirmView)


class TestPostResultsConfirmView:
    """Test suite for PostResultsConfirmView interactions."""

    @pytest.fixture
    def post_view(self, database, mock_text_channel):
        """Provide a PostResultsConfirmView instance."""
        fixture_data = {
            "week_number": 1,
            "scores": [{"user_id": "123", "user_name": "User1", "points": 9}],
        }
        standings = [{"user_name": "User1", "points": 9}]
        return PostResultsConfirmView(database, fixture_data, standings, mock_text_channel)

    @pytest.mark.asyncio
    async def test_no_mentions_posts_without_mentions(self, post_view, mock_text_channel):
        """Should post results without mentions."""
        mock_interaction = MagicMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        await post_view.no_mentions(mock_interaction, MagicMock())

        # Verify message was posted to channel
        assert len(mock_text_channel.messages_sent) == 1

        # Verify confirmation
        mock_interaction.response.edit_message.assert_called_once()
        call_args = mock_interaction.response.edit_message.call_args
        assert "without mentions" in call_args.kwargs.get("content", "").lower()

    @pytest.mark.asyncio
    async def test_with_mentions_posts_with_mentions(self, post_view, mock_text_channel):
        """Should post results with user mentions."""
        mock_interaction = MagicMock()
        mock_interaction.response = MagicMock()
        mock_interaction.response.edit_message = AsyncMock()

        await post_view.with_mentions(mock_interaction, MagicMock())

        # Verify message was posted to channel
        assert len(mock_text_channel.messages_sent) == 1
        posted_content = mock_text_channel.messages_sent[0].get("content", "")
        assert "<@123>" in posted_content

        # Verify confirmation
        mock_interaction.response.edit_message.assert_called_once()
        call_args = mock_interaction.response.edit_message.call_args
        assert "with mentions" in call_args.kwargs.get("content", "").lower()


class TestOnMessageListener:
    """Test suite for on_message DM listener."""

    @pytest.fixture
    def admin_cog(self, mock_bot, database):
        """Provide an AdminCommands cog instance."""
        mock_bot.db = database
        return AdminCommands(mock_bot)

    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, admin_cog, mock_message):
        """Should ignore messages from bots."""
        mock_message.author.bot = True
        mock_message.guild = None  # DM

        result = await admin_cog.on_message(mock_message)

        assert result is None  # No return value expected

    @pytest.mark.asyncio
    async def test_ignores_guild_messages(self, admin_cog, mock_message):
        """Should ignore messages in guild channels."""
        mock_message.guild = mock_message.guild  # Has guild

        result = await admin_cog.on_message(mock_message)

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_fixture_creation_dm(self, admin_cog, mock_message):
        """Should route fixture creation DMs to handler."""
        mock_message.guild = None
        user_id = str(mock_message.author.id)

        # Start a fixture session
        admin_cog.fixture_handler.start_session(user_id, 123456, 111111)

        # Mock the handler's handle_dm
        admin_cog.fixture_handler.handle_dm = AsyncMock(return_value=True)

        await admin_cog.on_message(mock_message)

        admin_cog.fixture_handler.handle_dm.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_results_entry_dm(self, admin_cog, mock_message):
        """Should route results entry DMs to handler."""
        mock_message.guild = None
        user_id = str(mock_message.author.id)

        # Start a results session
        admin_cog.results_handler.start_session(user_id, 1, 111111)

        # Mock the handler's handle_dm
        admin_cog.results_handler.handle_dm = AsyncMock(return_value=True)

        await admin_cog.on_message(mock_message)

        admin_cog.results_handler.handle_dm.assert_called_once()
