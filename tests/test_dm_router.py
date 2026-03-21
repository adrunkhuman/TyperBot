"""Tests for DM routing precedence."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from typer_bot.services.dm_router import DMRouter


def _make_dm_message(user_id: str = "123456", bot: bool = False, in_guild: bool = False):
    message = MagicMock()
    message.author.id = int(user_id)
    message.author.bot = bot
    message.guild = MagicMock() if in_guild else None
    return message


@pytest.fixture
def fixture_handler():
    h = MagicMock()
    h.has_session.return_value = False
    h.handle_dm = AsyncMock(return_value=True)
    return h


@pytest.fixture
def results_handler():
    h = MagicMock()
    h.has_session.return_value = False
    h.handle_dm = AsyncMock(return_value=True)
    return h


@pytest.fixture
def prediction_handler():
    h = MagicMock()
    h.handle_dm = AsyncMock(return_value=True)
    return h


@pytest.fixture
def router(fixture_handler, results_handler, prediction_handler):
    return DMRouter(fixture_handler, results_handler, prediction_handler)


class TestRouterIgnoresNonDMs:
    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, router):
        result = await router.route(_make_dm_message(bot=True))
        assert result is False

    @pytest.mark.asyncio
    async def test_ignores_guild_messages(self, router):
        result = await router.route(_make_dm_message(in_guild=True))
        assert result is False


class TestRoutingPrecedence:
    @pytest.mark.asyncio
    async def test_fixture_session_routes_to_fixture_handler(
        self, router, fixture_handler, results_handler, prediction_handler
    ):
        fixture_handler.has_session.return_value = True
        message = _make_dm_message()

        result = await router.route(message)

        assert result is True
        fixture_handler.handle_dm.assert_awaited_once()
        results_handler.handle_dm.assert_not_awaited()
        prediction_handler.handle_dm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_results_session_routes_to_results_handler(
        self, router, fixture_handler, results_handler, prediction_handler
    ):
        results_handler.has_session.return_value = True
        message = _make_dm_message()

        result = await router.route(message)

        assert result is True
        results_handler.handle_dm.assert_awaited_once()
        fixture_handler.handle_dm.assert_not_awaited()
        prediction_handler.handle_dm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_admin_session_falls_through_to_prediction(
        self, router, fixture_handler, results_handler, prediction_handler
    ):
        message = _make_dm_message()

        result = await router.route(message)

        assert result is True
        prediction_handler.handle_dm.assert_awaited_once_with(message)
        fixture_handler.handle_dm.assert_not_awaited()
        results_handler.handle_dm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fixture_session_takes_precedence_over_results(
        self, router, fixture_handler, results_handler
    ):
        """Fixture check runs first; results handler should never be reached."""
        fixture_handler.has_session.return_value = True
        results_handler.has_session.return_value = True
        message = _make_dm_message()

        await router.route(message)

        fixture_handler.handle_dm.assert_awaited_once()
        results_handler.handle_dm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_session_takes_precedence_over_prediction(
        self, router, results_handler, prediction_handler
    ):
        """Any active admin session blocks the prediction handler."""
        results_handler.has_session.return_value = True
        message = _make_dm_message()

        await router.route(message)

        results_handler.handle_dm.assert_awaited_once()
        prediction_handler.handle_dm.assert_not_awaited()
