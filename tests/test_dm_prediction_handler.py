"""Tests for the DM prediction handler."""

from datetime import UTC, datetime, timedelta

import pytest

from typer_bot.handlers.dm_prediction_handler import DMPredictionHandler
from typer_bot.utils import now


@pytest.fixture
async def prediction_handler(database, workflow_state):
    return DMPredictionHandler(database, workflow_state)


class TestHandleDM:
    @pytest.mark.asyncio
    async def test_ignores_bot_messages(self, prediction_handler, mock_message):
        mock_message.author.bot = True
        mock_message.guild = None

        handled = await prediction_handler.handle_dm(mock_message)

        assert not handled
        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_ignores_guild_messages(self, prediction_handler, mock_message):
        handled = await prediction_handler.handle_dm(mock_message)

        assert not handled
        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_ignores_dms_during_results_entry(
        self, prediction_handler, mock_message, workflow_state
    ):
        mock_message.guild = None
        user_id = str(mock_message.author.id)
        session = workflow_state.start_results_session(user_id, 1, 123456)
        session.created_at = datetime.now(UTC)

        handled = await prediction_handler.handle_dm(mock_message)

        assert not handled
        assert len(mock_message.author.dm_sent) == 0

    @pytest.mark.asyncio
    async def test_rejects_message_too_long(self, prediction_handler, mock_message):
        mock_message.guild = None
        mock_message.content = "x" * 5001

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 1
        assert "too long" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_no_fixture_shows_info_message(self, prediction_handler, mock_message):
        mock_message.guild = None

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 1
        assert "No active fixture" in mock_message.author.dm_sent[0]

    @pytest.mark.asyncio
    async def test_saves_valid_predictions(self, prediction_handler, fixture_with_dm, mock_message):
        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 2

        predictions = await prediction_handler.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["user_id"] == "123456"
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]
        assert not predictions[0]["is_late"]

    @pytest.mark.asyncio
    async def test_updates_existing_prediction(
        self, prediction_handler, fixture_with_dm, mock_message
    ):
        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        await prediction_handler.handle_dm(mock_message)

        mock_message.content = "Team A - Team B 3-0\nTeam C - Team D 2-2\nTeam E - Team F 1-1"
        await prediction_handler.handle_dm(mock_message)

        predictions = await prediction_handler.db.get_all_predictions(fixture_with_dm["id"])
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["3-0", "2-2", "1-1"]

    @pytest.mark.asyncio
    async def test_marks_late_predictions(
        self, prediction_handler, database, mock_message, sample_games
    ):
        mock_message.guild = None
        deadline = datetime.now(UTC) - timedelta(hours=1)
        fixture_id = await database.create_fixture(1, sample_games, deadline)

        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        predictions = await database.get_all_predictions(fixture_id)
        assert len(predictions) == 1
        assert predictions[0]["is_late"]
        assert "Late prediction" in mock_message.author.dm_sent[-1]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_invalid_prediction_format(self, prediction_handler, mock_message):
        mock_message.guild = None
        mock_message.content = "Team A - Team B invalid\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 2
        assert "Invalid predictions" in mock_message.author.dm_sent[-1]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_handles_database_error(self, prediction_handler, mock_message, monkeypatch):
        mock_message.guild = None

        async def raise_error(*_args, **_kwargs):
            raise Exception("Database connection failed")

        monkeypatch.setattr(prediction_handler.db, "save_prediction", raise_error)
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 2
        assert "Error processing predictions" in mock_message.author.dm_sent[-1]


class TestStartFlow:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_single_open_fixture_sends_prediction_prompt(self, prediction_handler, mock_user):
        open_fixtures = await prediction_handler.db.get_open_fixtures()

        await prediction_handler.start_flow(mock_user, open_fixtures)

        assert len(mock_user.dm_sent) == 1
        assert "Week 1 - Submit Your Predictions" in mock_user.dm_sent[0]
        session = prediction_handler.workflow_state.get_prediction_session(str(mock_user.id))
        assert session.step == "predict"

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_prompt_for_selection(
        self, prediction_handler, mock_user, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await prediction_handler.db.create_fixture(1, sample_games, deadline)
        await prediction_handler.db.create_fixture(2, sample_games, deadline)
        open_fixtures = await prediction_handler.db.get_open_fixtures()

        await prediction_handler.start_flow(mock_user, open_fixtures)

        assert len(mock_user.dm_sent) == 1
        assert "Multiple fixtures are open" in mock_user.dm_sent[0]
        session = prediction_handler.workflow_state.get_prediction_session(str(mock_user.id))
        assert session.step == "select"


class TestMultiOpenPredictionFlow:
    @pytest.mark.asyncio
    async def test_direct_dm_with_multiple_open_requests_week_first(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await prediction_handler.db.create_fixture(1, sample_games, deadline)
        await prediction_handler.db.create_fixture(2, sample_games, deadline)

        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert len(mock_message.author.dm_sent) == 1
        assert "Multiple fixtures are open" in mock_message.author.dm_sent[0]
        session = prediction_handler.workflow_state.get_prediction_session(
            str(mock_message.author.id)
        )
        assert session.step == "select"

    @pytest.mark.asyncio
    async def test_inline_week_selection_with_predictions_saves_selected_fixture(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="select",
            fixture_ids=[fixture_one_id, fixture_two_id],
            completed_fixture_ids=[],
        )

        mock_message.guild = None
        mock_message.content = "2\nTeam A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert "Would you like to predict another open fixture" in mock_message.author.dm_sent[-1]
        predictions = await prediction_handler.db.get_all_predictions(fixture_two_id)
        assert len(predictions) == 1
        assert predictions[0]["predictions"] == ["2-1", "1-1", "0-2"]
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        assert session.step == "continue"

    @pytest.mark.asyncio
    async def test_saving_one_fixture_prompts_for_another_when_open(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="predict",
            fixture_id=fixture_one_id,
            completed_fixture_ids=[],
        )

        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert "Would you like to predict another open fixture" in mock_message.author.dm_sent[-1]
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        assert session.step == "continue"

        predictions = await prediction_handler.db.get_all_predictions(fixture_one_id)
        assert len(predictions) == 1

    @pytest.mark.asyncio
    async def test_continue_yes_moves_to_remaining_fixture(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="continue",
            fixture_ids=[fixture_two_id],
            completed_fixture_ids=[fixture_one_id],
        )

        mock_message.guild = None
        mock_message.content = "yes"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert "Week 2 - Submit Your Predictions" in mock_message.author.dm_sent[-1]
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        assert session.step == "predict"
        assert session.fixture_id == fixture_two_id

    @pytest.mark.asyncio
    async def test_continue_no_clears_session(self, prediction_handler, mock_message, sample_games):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="continue",
            fixture_ids=[fixture_two_id],
            completed_fixture_ids=[fixture_one_id],
        )

        mock_message.guild = None
        mock_message.content = "no"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert mock_message.author.dm_sent[-1] == "👍 Got it. You're done for now."
        assert prediction_handler.workflow_state.get_prediction_session(user_id) is None

    @pytest.mark.asyncio
    async def test_continue_invalid_reply_keeps_session(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="continue",
            fixture_ids=[fixture_two_id],
            completed_fixture_ids=[fixture_one_id],
        )

        mock_message.guild = None
        mock_message.content = "maybe"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert mock_message.author.dm_sent[-1] == "Please reply with `yes` or `no`."
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        assert session.step == "continue"

    @pytest.mark.asyncio
    async def test_continue_yes_with_no_remaining_fixture_clears_session(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)
        await prediction_handler.db.save_scores(fixture_two_id, [])

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="continue",
            fixture_ids=[fixture_two_id],
            completed_fixture_ids=[fixture_one_id],
        )

        mock_message.guild = None
        mock_message.content = "yes"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert mock_message.author.dm_sent[-1] == "ℹ️ There are no other open fixtures right now."
        assert prediction_handler.workflow_state.get_prediction_session(user_id) is None

    @pytest.mark.asyncio
    async def test_expired_session_is_cleaned_up_before_handling(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="predict",
            fixture_id=fixture_one_id,
            completed_fixture_ids=[],
        )
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        session.created_at = now() - timedelta(hours=2)

        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert mock_message.author.dm_sent[-1].startswith("Multiple fixtures are open")
        session = prediction_handler.workflow_state.get_prediction_session(user_id)
        assert session.step == "select"

    @pytest.mark.asyncio
    async def test_selected_fixture_closed_mid_flow_does_not_auto_route(
        self, prediction_handler, mock_message, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_one_id = await prediction_handler.db.create_fixture(1, sample_games, deadline)
        fixture_two_id = await prediction_handler.db.create_fixture(2, sample_games, deadline)

        user_id = str(mock_message.author.id)
        prediction_handler._set_prediction_session(
            user_id,
            step="predict",
            fixture_id=fixture_one_id,
            completed_fixture_ids=[],
        )

        await prediction_handler.db.save_scores(fixture_one_id, [])

        mock_message.guild = None
        mock_message.content = "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"

        handled = await prediction_handler.handle_dm(mock_message)

        assert handled
        assert "no longer open" in mock_message.author.dm_sent[-1]
        predictions = await prediction_handler.db.get_all_predictions(fixture_two_id)
        assert len(predictions) == 0
