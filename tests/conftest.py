"""Shared pytest fixtures for typer-bot tests."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest

from typer_bot.database import Database
from typer_bot.handlers.thread_prediction_handler import ThreadPredictionHandler


@pytest.fixture
def temp_db_path():
    """Provide a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def mock_bot():
    """Provide a mocked Discord bot client."""
    bot = MagicMock(spec=discord.Client)
    bot.user = MagicMock()
    bot.user.id = 999999
    bot.user.name = "TestBot"
    return bot


@pytest.fixture
async def database(temp_db_path):
    """Provide an initialized database instance."""
    db = Database(temp_db_path)
    await db.initialize()
    yield db


class MockThread(discord.Thread):
    """Mock Discord thread that properly inherits from discord.Thread."""

    def __init__(self, thread_id: str = "789012", name: str = "test-thread", guild=None):
        # Don't call super().__init__ to avoid Discord API requirements
        self._id = int(thread_id)
        self._name = name
        self._guild = guild
        self.reactions_added = []
        self.reactions_cleared = False

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        self._id = value

    @property
    def name(self):
        return self._name

    @property
    def guild(self):
        return self._guild

    async def add_reaction(self, emoji: str):
        """Mock add reaction method."""
        self.reactions_added.append(emoji)

    async def clear_reactions(self):
        """Mock clear reactions method."""
        self.reactions_added.clear()


class MockGuild:
    """Mock Discord guild for testing."""

    def __init__(self, guild_id: str = "111111"):
        self.id = int(guild_id)
        self.name = "Test Guild"
        self._members = {}

    def add_member(self, user_id: str, roles: list[str] = None):
        """Add a member to the guild."""
        mock_member = MagicMock()
        mock_member.id = int(user_id)
        mock_member.roles = [MockRole(role) for role in (roles or [])]
        self._members[int(user_id)] = mock_member
        return mock_member

    def get_member(self, user_id: int):
        """Get a member by ID."""
        return self._members.get(user_id)


class MockUser:
    """Mock Discord user for testing."""

    def __init__(self, user_id: str = "123456", name: str = "TestUser"):
        self.id = int(user_id)
        self.name = name
        self.display_name = name
        self.bot = False
        self.dm_sent = []

    async def send(self, content: str, **_kwargs):
        """Mock send DM method."""
        self.dm_sent.append(content)
        return MagicMock()


class MockMessage:
    """Mock Discord message for testing."""

    def __init__(
        self,
        content: str = "",
        message_id: str = "555555",
        author: MockUser | None = None,
        channel: MockThread | None = None,
        guild: MockGuild | None = None,
    ):
        self.id = int(message_id)
        self.content = content
        self.author = author or MockUser()
        self.channel = channel or MockThread()
        self.guild = guild or MockGuild()

        # Track reactions and clear operations
        self.reactions_added = []
        self.reactions_cleared = False
        self._clear_reactions_count = 0

    async def add_reaction(self, emoji: str):
        """Mock add reaction method."""
        self.reactions_added.append(emoji)

    async def clear_reactions(self):
        """Mock clear reactions method - tracks that it was called and clears list."""
        self.reactions_cleared = True
        self._clear_reactions_count += 1
        self.reactions_added.clear()

    async def remove_reaction(self, emoji: str, member):
        """Mock remove reaction method - tracks removed reactions."""
        if not hasattr(self, "reactions_removed"):
            self.reactions_removed = []
        self.reactions_removed.append((emoji, member.id if hasattr(member, "id") else member))


@pytest.fixture
def mock_user():
    """Provide a mocked Discord user."""
    return MockUser()


@pytest.fixture
def mock_thread(mock_guild):
    """Provide a mocked Discord thread."""
    return MockThread(guild=mock_guild)


@pytest.fixture
def mock_guild():
    """Provide a mocked Discord guild."""
    return MockGuild()


@pytest.fixture
def mock_message(mock_user, mock_thread, mock_guild):
    """Provide a mocked Discord message."""
    return MockMessage(author=mock_user, channel=mock_thread, guild=mock_guild)


@pytest.fixture
def sample_games():
    """Provide sample game fixtures."""
    return [
        "Team A - Team B",
        "Team C - Team D",
        "Team E - Team F",
    ]


@pytest.fixture
def sample_predictions():
    """Provide sample predictions text."""
    return "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"


@pytest.fixture
async def fixture_with_thread(database, sample_games):
    """Provide a fixture with an associated thread."""
    deadline = datetime.now(UTC) + timedelta(days=1)
    fixture_id = await database.create_fixture(1, sample_games, deadline)
    await database.update_fixture_announcement(fixture_id, thread_id="789012")
    fixture = await database.get_fixture_by_id(fixture_id)
    yield fixture


@pytest.fixture
def handler(mock_bot, database):
    """Provide a ThreadPredictionHandler instance."""
    return ThreadPredictionHandler(mock_bot, database)


class MockRole:
    """Mock Discord role for testing."""

    def __init__(self, name: str):
        self.name = name


class MockAdminUser(MockUser):
    """Mock Discord user with admin role for testing."""

    def __init__(self, user_id: str = "123456", name: str = "AdminUser"):
        super().__init__(user_id, name)
        self.roles = [MockRole("admin")]


class MockTextChannel:
    """Mock Discord text channel for testing."""

    def __init__(self, channel_id: str = "123456", name: str = "test-channel", guild=None):
        self.id = int(channel_id)
        self.name = name
        self._guild = guild
        self.messages_sent = []
        self.threads_created = []

    @property
    def guild(self):
        return self._guild

    async def send(self, content: str = None, **kwargs):
        """Mock send message method."""
        msg = {"content": content}
        msg.update(kwargs)
        self.messages_sent.append(msg)
        mock_msg = MagicMock()
        mock_msg.id = 999999
        return mock_msg

    async def create_thread(self, name: str, auto_archive_duration: int = 1440):
        """Mock create thread method."""
        thread = MockThread(thread_id="999999", name=name, guild=self._guild)
        self.threads_created.append(thread)
        return thread


class MockGuildWithMembers(MockGuild):
    """Mock Discord guild with member lookup for testing."""

    def __init__(self, guild_id: str = "111111"):
        super().__init__(guild_id)
        self._members = {}

    def add_member(self, user_id: str, roles: list[str] = None):
        """Add a member to the guild."""
        mock_member = MagicMock()
        mock_member.id = int(user_id)
        mock_member.roles = [MockRole(role) for role in (roles or [])]
        self._members[int(user_id)] = mock_member
        return mock_member

    def get_member(self, user_id: int):
        """Get a member by ID."""
        return self._members.get(user_id)


class MockInteraction:
    """Mock Discord interaction for slash command testing."""

    def __init__(
        self,
        user: MockUser | None = None,
        guild: MockGuild | None = None,
        channel: MockTextChannel | None = None,
    ):
        self.user = user or MockUser()
        self.guild = guild
        self.channel = channel
        self.response_sent = []
        self.followup_sent = []
        self.id = 123456789

        # Create a mock response object
        self.response = MagicMock()
        self.response.is_done.return_value = False

        # Create a mock followup object
        self.followup = MagicMock()

    async def response_send_message(self, content: str = None, **kwargs):
        """Mock send message via interaction response."""
        msg = {"content": content}
        msg.update(kwargs)
        self.response_sent.append(msg)

    async def followup_send(self, content: str = None, **kwargs):
        """Mock send followup message."""
        msg = {"content": content}
        msg.update(kwargs)
        self.followup_sent.append(msg)


@pytest.fixture
def mock_text_channel(mock_guild):
    """Provide a mocked text channel."""
    return MockTextChannel(guild=mock_guild)


@pytest.fixture
def mock_admin_user():
    """Provide a mocked admin user."""
    return MockAdminUser()


@pytest.fixture
def mock_guild_with_members():
    """Provide a mocked guild with member support."""
    return MockGuildWithMembers()


@pytest.fixture
def mock_interaction(mock_user, mock_guild, mock_text_channel):
    """Provide a mocked Discord interaction."""
    # Add user to guild as non-admin
    mock_guild.add_member(str(mock_user.id), roles=["user"])
    return MockInteraction(user=mock_user, guild=mock_guild, channel=mock_text_channel)


@pytest.fixture
def mock_interaction_admin(mock_admin_user, mock_guild_with_members, mock_text_channel):
    """Provide a mocked Discord interaction with admin user."""
    mock_guild_with_members.add_member("123456", roles=["admin"])
    return MockInteraction(
        user=mock_admin_user, guild=mock_guild_with_members, channel=mock_text_channel
    )


@pytest.fixture
def mock_admin_check():
    """Provide a mock admin check function that returns True."""

    def check(member):
        return any(role.name.lower() in {"admin", "typer-admin"} for role in member.roles)

    return check
