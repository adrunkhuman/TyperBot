"""Tests for user command wiring."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.conftest import MockInteraction, MockUser
from typer_bot.commands.user_commands import (
    ContinuePredictView,
    FixtureSelectView,
    PredictModal,
    UserCommands,
)
from typer_bot.database import SaveResult
from typer_bot.utils import format_standings


@pytest.fixture
async def user_commands(mock_bot, database):
    mock_bot.db = database
    return UserCommands(mock_bot)


class TestPredictCommand:
    @pytest.mark.asyncio
    async def test_no_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.predict.callback(user_commands, mock_interaction)

        assert len(mock_interaction.response_sent) == 1
        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_single_open_fixture_opens_predict_modal(self, user_commands, mock_interaction):
        await user_commands.predict.callback(user_commands, mock_interaction)

        assert isinstance(mock_interaction.modal_sent["modal"], PredictModal)

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_show_picker(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert isinstance(mock_interaction.response_sent[0]["view"], FixtureSelectView)

    @pytest.mark.asyncio
    async def test_multiple_open_fixture_picker_opens_modal_for_selection(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        view = mock_interaction.response_sent[0]["view"]
        select = view.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        assert isinstance(mock_interaction.modal_sent["modal"], PredictModal)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_prefills_existing_prediction(
        self, user_commands, mock_interaction, database
    ):
        await database.save_prediction(
            1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        assert modal.predictions_input.default == (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_shows_parse_errors(self, user_commands, mock_interaction):
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = "Team A - Team B\nTeam C - Team D\nTeam E - Team F"
        await modal.on_submit(mock_interaction)

        assert "Could not find score" in mock_interaction.response_sent[-1]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_saves_prediction_and_offers_continue(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        picker = mock_interaction.response_sent[0]["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert await database.get_prediction(1, str(mock_interaction.user.id)) is not None
        assert isinstance(mock_interaction.response_sent[-1]["view"], ContinuePredictView)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_terminal_success_without_other_open_fixtures(
        self, user_commands, mock_interaction, database
    ):
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert await database.get_prediction(1, str(mock_interaction.user.id)) is not None
        assert "You're done for now." in mock_interaction.response_sent[-1]["content"]
        assert "view" not in mock_interaction.response_sent[-1]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_overwrites_existing_prediction(
        self, user_commands, mock_interaction, database
    ):
        await database.save_prediction(
            1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 3-0\nTeam C - Team D 0-0\nTeam E - Team F 1-1"
        )
        await modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id))
        assert prediction is not None
        assert prediction["predictions"] == ["3-0", "0-0", "1-1"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_marks_late_prediction(
        self, user_commands, mock_interaction, database
    ):
        fixture = await database.get_fixture_by_id(1)
        assert fixture is not None
        fixture["deadline"] = datetime.now(UTC) - timedelta(minutes=1)
        user_commands.db.get_open_fixtures = AsyncMock(return_value=[fixture])
        user_commands.db.get_fixture_by_id = AsyncMock(return_value=fixture)

        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id))
        assert prediction is not None
        assert prediction["is_late"] == 1
        assert "Late prediction" in mock_interaction.response_sent[-1]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_reports_closed_fixture_during_submit(
        self, user_commands, mock_interaction, monkeypatch
    ):
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        monkeypatch.setattr(
            user_commands.db,
            "save_prediction_guarded",
            AsyncMock(return_value=SaveResult.FIXTURE_CLOSED),
        )

        await modal.on_submit(mock_interaction)

        assert (
            "closed before your prediction could be saved"
            in mock_interaction.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_reports_database_error(
        self, user_commands, mock_interaction, monkeypatch
    ):
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("db failed")

        monkeypatch.setattr(user_commands.db, "save_prediction_guarded", _raise)
        await modal.on_submit(mock_interaction)

        assert (
            "Something went wrong while saving your prediction"
            in mock_interaction.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_continue_predict_button_opens_next_modal(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)
        picker = mock_interaction.response_sent[0]["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        continue_view = mock_interaction.response_sent[-1]["view"]
        button = continue_view.children[0]
        await button.callback(mock_interaction)

        assert mock_interaction.modal_sent["modal"].title == "Predict Week 2"

    @pytest.mark.asyncio
    async def test_multi_fixture_flow_ends_without_continue_view_after_last_save(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)
        picker = mock_interaction.response_sent[0]["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        first_modal = mock_interaction.modal_sent["modal"]
        first_modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await first_modal.on_submit(mock_interaction)

        continue_view = mock_interaction.response_sent[-1]["view"]
        continue_button = continue_view.children[0]
        await continue_button.callback(mock_interaction)

        second_modal = mock_interaction.modal_sent["modal"]
        second_modal.predictions_input._value = (
            "Team A - Team B 1-0\nTeam C - Team D 2-2\nTeam E - Team F 3-1"
        )
        await second_modal.on_submit(mock_interaction)

        assert await database.get_prediction(1, str(mock_interaction.user.id)) is not None
        assert await database.get_prediction(2, str(mock_interaction.user.id)) is not None
        assert "You're done for now." in mock_interaction.response_sent[-1]["content"]
        assert "view" not in mock_interaction.response_sent[-1]

    @pytest.mark.asyncio
    async def test_fixture_picker_rejects_wrong_user(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        other_user_interaction = MockInteraction(
            user=MockUser("999", "OtherUser"),
            guild=mock_interaction.guild,
            channel=mock_interaction.channel,
        )
        view = mock_interaction.response_sent[0]["view"]
        select = view.children[0]
        select._values = ["1"]
        await select.callback(other_user_interaction)

        assert (
            "don't have permission" in other_user_interaction.response_sent[-1]["content"].lower()
        )

    @pytest.mark.asyncio
    async def test_fixture_picker_reports_closed_fixture(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        await database.save_scores(
            1,
            [
                {
                    "user_id": "u1",
                    "user_name": "User One",
                    "points": 0,
                    "exact_scores": 0,
                    "correct_results": 0,
                }
            ],
        )
        view = mock_interaction.response_sent[0]["view"]
        select = view.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        assert "no longer open" in mock_interaction.response_sent[-1]["content"].lower()
        assert mock_interaction.response_sent[-1]["view"] is None

    @pytest.mark.asyncio
    async def test_continue_predict_button_rejects_wrong_user(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)
        picker = mock_interaction.response_sent[0]["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        other_user_interaction = MockInteraction(
            user=MockUser("999", "OtherUser"),
            guild=mock_interaction.guild,
            channel=mock_interaction.channel,
        )
        continue_view = mock_interaction.response_sent[-1]["view"]
        button = continue_view.children[0]
        await button.callback(other_user_interaction)

        assert (
            "don't have permission" in other_user_interaction.response_sent[-1]["content"].lower()
        )

    @pytest.mark.asyncio
    async def test_continue_predict_button_reports_closed_fixture(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)
        picker = mock_interaction.response_sent[0]["view"]
        select = picker.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)
        await database.save_scores(
            2,
            [
                {
                    "user_id": "u1",
                    "user_name": "User One",
                    "points": 0,
                    "exact_scores": 0,
                    "correct_results": 0,
                }
            ],
        )

        continue_view = mock_interaction.response_sent[-1]["view"]
        button = continue_view.children[0]
        await button.callback(mock_interaction)

        assert "no longer open" in mock_interaction.response_sent[-1]["content"].lower()
        assert mock_interaction.response_sent[-1]["view"] is None


class TestFixturesCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.fixtures.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == "❌ No active fixture found!"

    @pytest.mark.asyncio
    async def test_single_open_fixture_lists_games_and_deadline(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(1, sample_games, datetime.now(UTC) + timedelta(days=1))

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Week 1 Fixtures" in content
        assert sample_games[0] in content
        assert "Deadline:" in content
        assert mock_interaction.response_sent[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_list_each_week(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)

        await user_commands.fixtures.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Open Fixtures" in content
        assert "Week 1" in content
        assert "Week 2" in content


class TestStandingsCommand:
    @pytest.mark.asyncio
    async def test_standings_sends_empty_state(self, user_commands, mock_interaction):
        await user_commands.standings.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == format_standings([], None)
        assert mock_interaction.response_sent[0]["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_standings_sends_formatted_leaderboard(self, user_commands, mock_interaction):
        standings = [
            {
                "user_id": "123",
                "user_name": "User1",
                "total_points": 9,
                "total_exact": 3,
                "total_correct": 3,
            }
        ]
        last_fixture = {
            "week_number": 4,
            "games": ["A - B"],
            "results": ["2-1"],
            "scores": [
                {
                    "user_id": "123",
                    "user_name": "User1",
                    "points": 3,
                    "exact_scores": 1,
                    "correct_results": 1,
                }
            ],
        }
        user_commands.db.get_standings = AsyncMock(return_value=standings)
        user_commands.db.get_last_fixture_scores = AsyncMock(return_value=last_fixture)

        await user_commands.standings.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == format_standings(
            standings, last_fixture
        )


class TestMyPredictionsCommand:
    @pytest.mark.asyncio
    async def test_no_open_fixture_shows_error(self, user_commands, mock_interaction):
        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        assert mock_interaction.response_sent[0]["content"] == "❌ No active fixture found!"

    @pytest.mark.asyncio
    async def test_single_fixture_without_prediction_shows_prompt(
        self, user_commands, mock_interaction, database, sample_games
    ):
        await database.create_fixture(1, sample_games, datetime.now(UTC) + timedelta(days=1))

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "haven't submitted predictions" in content
        assert "Use `/predict`" in content

    @pytest.mark.asyncio
    async def test_single_fixture_prediction_shows_saved_scores(
        self, user_commands, mock_interaction, database, sample_games
    ):
        fixture_id = await database.create_fixture(
            1, sample_games, datetime.now(UTC) + timedelta(days=1)
        )
        await database.save_prediction(
            fixture_id,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions:" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "Status:" in content
        assert "Submitted:" in content

    @pytest.mark.asyncio
    async def test_multiple_open_fixtures_show_mixed_prediction_state(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_week_1 = await database.create_fixture(1, sample_games, deadline)
        await database.create_fixture(2, sample_games, deadline)
        await database.save_prediction(
            fixture_week_1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["2-1", "1-1", "0-2"],
            False,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[0]["content"]
        assert "Your Predictions (Open Fixtures):" in content
        assert "Week 1" in content
        assert "Week 2" in content
        assert f"1. {sample_games[0]} **2-1**" in content
        assert "No prediction submitted yet." in content
