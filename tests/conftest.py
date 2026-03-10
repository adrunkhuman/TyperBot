"""Shared pytest fixtures for typer-bot tests."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest

from typer_bot.database import Database
from typer_bot.handlers.results_handler import _pending_results
from typer_bot.handlers.thread_prediction_handler import ThreadPredictionHandler


@pytest.fixture(autouse=True)
def clear_results_sessions():
    _pending_results.clear()
    yield


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def mock_bot():
    bot = MagicMock(spec=discord.Client)
    bot.user = MagicMock()
    bot.user.id = 999999
    bot.user.name = "TestBot"
    return bot


@pytest.fixture
async def database(temp_db_path):
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
        self.reactions_added.append(emoji)

    async def clear_reactions(self):
        self.reactions_added.clear()


class MockGuild:
    def __init__(self, guild_id: str = "111111"):
        self.id = int(guild_id)
        self.name = "Test Guild"
        self._members = {}

    def add_member(self, user_id: str, roles: list[str] = None):
        mock_member = MagicMock()
        mock_member.id = int(user_id)
        mock_member.roles = [MockRole(role) for role in (roles or [])]
        self._members[int(user_id)] = mock_member
        return mock_member

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class MockUser:
    def __init__(self, user_id: str = "123456", name: str = "TestUser"):
        self.id = int(user_id)
        self.name = name
        self.display_name = name
        self.bot = False
        self.dm_sent = []

    async def send(self, content: str, **_kwargs):
        self.dm_sent.append(content)

        dm_sent = self.dm_sent

        class MockDMMessage:
            async def edit(self, content=None, **_kwargs):
                if content:
                    dm_sent.append(content)

        return MockDMMessage()


class MockMessage:
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
        # Allow None guild for DM messages
        self.guild = guild

        self.reactions_added = []
        self.reactions_cleared = False
        self._clear_reactions_count = 0

    async def add_reaction(self, emoji: str):
        self.reactions_added.append(emoji)

    async def clear_reactions(self):
        self.reactions_cleared = True
        self._clear_reactions_count += 1
        self.reactions_added.clear()

    async def remove_reaction(self, emoji: str, member):
        if not hasattr(self, "reactions_removed"):
            self.reactions_removed = []
        self.reactions_removed.append((emoji, member.id if hasattr(member, "id") else member))


@pytest.fixture
def mock_user():
    return MockUser()


@pytest.fixture
def mock_thread(mock_guild):
    return MockThread(guild=mock_guild)


@pytest.fixture
def mock_guild():
    return MockGuild()


@pytest.fixture
def mock_message(mock_user, mock_thread, mock_guild):
    return MockMessage(author=mock_user, channel=mock_thread, guild=mock_guild)


@pytest.fixture
def sample_games():
    return [
        "Team A - Team B",
        "Team C - Team D",
        "Team E - Team F",
    ]


@pytest.fixture
def sample_predictions():
    return "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"


@pytest.fixture
async def fixture_with_thread(database, sample_games):
    deadline = datetime.now(UTC) + timedelta(days=1)
    fixture_id = await database.create_fixture(1, sample_games, deadline)
    await database.update_fixture_announcement(fixture_id, message_id="789012")
    fixture = await database.get_fixture_by_id(fixture_id)
    yield fixture


@pytest.fixture
async def fixture_with_dm(database, sample_games):
    """Fixture for DM prediction tests (no thread needed)."""
    deadline = datetime.now(UTC) + timedelta(days=1)
    fixture_id = await database.create_fixture(1, sample_games, deadline)
    fixture = await database.get_fixture_by_id(fixture_id)
    yield fixture


@pytest.fixture
def handler(mock_bot, database):
    return ThreadPredictionHandler(mock_bot, database)


class MockRole:
    def __init__(self, name: str):
        self.name = name


class MockAdminUser(MockUser):
    def __init__(self, user_id: str = "123456", name: str = "AdminUser"):
        super().__init__(user_id, name)
        self.roles = [MockRole("admin")]


class MockTextChannel:
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
        msg = {"content": content}
        msg.update(kwargs)
        self.messages_sent.append(msg)
        mock_msg = MagicMock()
        mock_msg.id = 999999
        return mock_msg

    async def create_thread(self, name: str, _auto_archive_duration: int = 1440):
        thread = MockThread(thread_id="999999", name=name, guild=self._guild)
        self.threads_created.append(thread)
        return thread


class MockGuildWithMembers(MockGuild):
    def __init__(self, guild_id: str = "111111"):
        super().__init__(guild_id)
        self._members = {}

    def add_member(self, user_id: str, roles: list[str] = None):
        mock_member = MagicMock()
        mock_member.id = int(user_id)
        mock_member.roles = [MockRole(role) for role in (roles or [])]
        self._members[int(user_id)] = mock_member
        return mock_member

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class MockInteraction:
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

        self.response = MagicMock()
        self.response.is_done.return_value = False
        self.response.send_message = self._response_send_message
        self.response.edit_message = self._response_edit_message
        self.response.send_modal = self._response_send_modal

        self.followup = MagicMock()
        self.followup.send = self._followup_send

    async def _response_send_message(self, content: str = None, **kwargs):
        msg = {"content": content}
        msg.update(kwargs)
        self.response_sent.append(msg)

    async def _followup_send(self, content: str = None, **kwargs):
        msg = {"content": content}
        msg.update(kwargs)
        self.followup_sent.append(msg)

    async def _response_edit_message(self, content: str = None, **kwargs):
        msg = {"content": content}
        msg.update(kwargs)
        self.response_sent.append(msg)

    async def _response_send_modal(self, modal, **kwargs):
        self.modal_sent = {"modal": modal, **kwargs}

    async def response_send_message(self, content: str = None, **kwargs):
        msg = {"content": content}
        msg.update(kwargs)
        self.response_sent.append(msg)

    async def followup_send(self, content: str = None, **kwargs):
        msg = {"content": content}
        msg.update(kwargs)
        self.followup_sent.append(msg)


@pytest.fixture
def mock_text_channel(mock_guild):
    return MockTextChannel(guild=mock_guild)


@pytest.fixture
def mock_admin_user():
    return MockAdminUser()


@pytest.fixture
def mock_guild_with_members():
    return MockGuildWithMembers()


@pytest.fixture
def mock_interaction(mock_user, mock_guild, mock_text_channel):
    mock_guild.add_member(str(mock_user.id), roles=["user"])
    return MockInteraction(user=mock_user, guild=mock_guild, channel=mock_text_channel)


@pytest.fixture
def mock_interaction_admin(mock_admin_user, mock_guild_with_members, mock_text_channel):
    mock_guild_with_members.add_member("123456", roles=["admin"])
    return MockInteraction(
        user=mock_admin_user, guild=mock_guild_with_members, channel=mock_text_channel
    )


@pytest.fixture
def mock_admin_check():
    def check(member):
        return any(role.name.lower() in {"admin", "typer-admin"} for role in member.roles)

    return check
