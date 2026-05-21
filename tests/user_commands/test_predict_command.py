from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.conftest import MockInteraction, MockRole, MockThread, MockUser
from typer_bot.commands.user_commands import (
    ContinuePredictView,
    FixtureSelectView,
    PredictModal,
    UserCommands,
)
from typer_bot.database import Database, SaveResult


async def _attach_prediction_threads(user_commands, database, fixture_ids, mock_guild):
    """Attach fixture announcement IDs to mock threads for public prediction posts."""
    threads = {}
    for index, fixture_id in enumerate(fixture_ids, start=1):
        message_id = str(700000 + index)
        await database.update_fixture_announcement(
            fixture_id,
            message_id=message_id,
            channel_id="123456",
        )
        threads[int(message_id)] = MockThread(
            thread_id=message_id, name=f"week-{fixture_id}", guild=mock_guild
        )

    user_commands.bot.get_channel.side_effect = lambda channel_id: threads.get(channel_id)
    return threads


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
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert isinstance(mock_interaction.response_sent[0]["view"], FixtureSelectView)

    @pytest.mark.asyncio
    async def test_predict_only_uses_current_guild_fixtures(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture("guild-2", 1, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        assert "No active fixture" in mock_interaction.response_sent[0]["content"]

    @pytest.mark.asyncio
    async def test_fixture_picker_rejects_cross_guild_fixture(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture("guild-2", 1, sample_games, deadline)
        fixture = await database.get_fixture_by_id(fixture_id, "guild-2")

        view = FixtureSelectView(
            database,
            user_commands.bot,
            str(mock_interaction.user.id),
            "111111",
            [fixture],
        )
        select = view.children[0]
        select._values = [str(fixture_id)]
        await select.callback(mock_interaction)

        assert not hasattr(mock_interaction, "modal_sent")
        assert "no longer open" in mock_interaction.response_sent[-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_continue_predict_rejects_cross_guild_fixture(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_id = await database.create_fixture("guild-2", 1, sample_games, deadline)
        fixture = await database.get_fixture_by_id(fixture_id, "guild-2")

        view = ContinuePredictView(
            database,
            user_commands.bot,
            str(mock_interaction.user.id),
            "111111",
            [fixture],
            set(),
        )
        button = view.children[0]
        await button.callback(mock_interaction)

        assert not hasattr(mock_interaction, "modal_sent")
        assert "no longer open" in mock_interaction.response_sent[-1]["content"].lower()

    @pytest.mark.asyncio
    async def test_multiple_open_fixture_picker_opens_modal_for_selection(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        view = mock_interaction.response_sent[0]["view"]
        select = view.children[0]
        select._values = ["1"]
        await select.callback(mock_interaction)

        assert isinstance(mock_interaction.modal_sent["modal"], PredictModal)

    @pytest.mark.asyncio
    async def test_multiple_open_fixture_picker_paginates_past_25(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        for week in range(1, 27):
            await database.create_fixture("111111", week, sample_games, deadline)

        await user_commands.predict.callback(user_commands, mock_interaction)

        view = mock_interaction.response_sent[0]["view"]
        next_button = next(
            child for child in view.children if getattr(child, "label", None) == "Next"
        )
        await next_button.callback(mock_interaction)

        select = mock_interaction.response_sent[-1]["view"].children[0]
        select._values = ["26"]
        await select.callback(mock_interaction)

        assert mock_interaction.modal_sent["modal"].title == "Predict Week 26"

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
        prefilled_lines = modal.predictions_input.default.splitlines()
        expected_prefills = [
            ("Team A - Team B", "2-1"),
            ("Team C - Team D", "1-1"),
            ("Team E - Team F", "0-2"),
        ]
        assert len(prefilled_lines) == len(expected_prefills)
        for line, (game, score) in zip(prefilled_lines, expected_prefills, strict=True):
            assert game in line
            assert score in line

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_shows_parse_errors(self, user_commands, mock_interaction):
        await _attach_prediction_threads(
            user_commands, user_commands.db, [1], mock_interaction.guild
        )
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
        fixture_two_id = await database.create_fixture("111111", 2, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [1, fixture_two_id], mock_interaction.guild
        )

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

        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is not None
        assert isinstance(mock_interaction.response_sent[-1]["view"], ContinuePredictView)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_terminal_success_without_other_open_fixtures(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is not None
        assert "You're done for now." in mock_interaction.response_sent[-1]["content"]
        assert "view" not in mock_interaction.response_sent[-1]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_overwrites_existing_prediction(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
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

        prediction = await database.get_prediction(1, str(mock_interaction.user.id), "111111")
        assert prediction is not None
        assert prediction["predictions"] == ["3-0", "0-0", "1-1"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_marks_late_prediction(
        self, user_commands, mock_interaction, database
    ):
        await database.update_active_scoring_rules("111111", {"late_prediction_points": 1})
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        fixture = await database.get_fixture_by_id(1, "111111")
        assert fixture is not None
        fixture["deadline"] = datetime.now(UTC) - timedelta(minutes=1)
        user_commands.db.get_open_fixtures = AsyncMock(return_value=[fixture])
        user_commands.db.get_fixture_by_id = AsyncMock(
            side_effect=lambda fixture_id, guild_id: (
                fixture if (fixture_id, guild_id) == (1, "111111") else None
            )
        )

        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id), "111111")
        assert prediction is not None
        assert prediction["is_late"] == 1
        content = mock_interaction.response_sent[-1]["content"]
        assert "Late prediction" in content
        assert "active season's late penalty" in content
        assert "0 points" not in content

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_accepts_pre_deadline_partial_prediction(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = "Team C - Team D 1-1\nTeam E - Team F 0-2"
        await modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id), "111111")
        assert prediction is not None
        assert prediction["predictions"] == ["1-1", "0-2"]
        assert prediction["predicted_game_indexes"] == [1, 2]
        assert prediction["pending_partial_approval"] is False
        assert "Partial prediction saved" in mock_interaction.response_sent[-1]["content"]
        assert "fill the rest" in mock_interaction.response_sent[-1]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_marks_late_partial_as_pending(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        admin_role = MockRole("League Admin", role_id=4242)
        await database.upsert_guild_config("111111", str(admin_role.id), "123456")
        fixture = await database.get_fixture_by_id(1, "111111")
        assert fixture is not None
        fixture["deadline"] = datetime.now(UTC) - timedelta(minutes=1)
        user_commands.db.get_open_fixtures = AsyncMock(return_value=[fixture])
        user_commands.db.get_fixture_by_id = AsyncMock(
            side_effect=lambda fixture_id, guild_id: (
                fixture if (fixture_id, guild_id) == (1, "111111") else None
            )
        )

        await user_commands.predict.callback(user_commands, mock_interaction)
        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = "Team C - Team D 1-1\nTeam E - Team F 0-2"
        await modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id), "111111")
        assert prediction is not None
        assert prediction["pending_partial_approval"] is True
        assert prediction["predicted_game_indexes"] == [1, 2]
        assert prediction["public_message_id"] == "1"
        assert prediction["public_message_kind"] == "bot_post"
        assert (
            "Late prediction awaiting admin review" in mock_interaction.response_sent[-1]["content"]
        )
        assert "0 points" not in mock_interaction.response_sent[-1]["content"]
        thread = user_commands.bot.get_channel(700001)
        assert f"<@&{admin_role.id}>" in thread.messages_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_predict_modal_without_setup_does_not_ping_legacy_admin_role(
        self,
        mock_bot,
        mock_interaction,
        temp_db_path,
        sample_games,
    ):
        database = Database(temp_db_path)
        await database.initialize()
        mock_bot.db = database
        user_commands = UserCommands(mock_bot)
        deadline = datetime.now(UTC) - timedelta(minutes=1)
        fixture_id = await database.create_fixture("111111", 1, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [fixture_id], mock_interaction.guild
        )
        mock_interaction.guild.roles = [MockRole("typer-admin", role_id=4242)]

        fixture = await database.get_fixture_by_id(fixture_id, "111111")
        assert fixture is not None
        user_commands.db.get_open_fixtures = AsyncMock(return_value=[fixture])
        user_commands.db.get_fixture_by_id = AsyncMock(
            side_effect=lambda request_fixture_id, guild_id: (
                fixture if (request_fixture_id, guild_id) == (fixture_id, "111111") else None
            )
        )

        await user_commands.predict.callback(user_commands, mock_interaction)
        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = "Team C - Team D 1-1\nTeam E - Team F 0-2"
        await modal.on_submit(mock_interaction)

        thread = user_commands.bot.get_channel(700001)
        assert "awaiting admin review" in thread.messages_sent[-1]["content"]
        assert "<@&4242>" not in thread.messages_sent[-1]["content"]

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_replaces_previous_pending_bot_post(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        fixture = await database.get_fixture_by_id(1, "111111")
        assert fixture is not None
        fixture["deadline"] = datetime.now(UTC) - timedelta(minutes=1)
        user_commands.db.get_open_fixtures = AsyncMock(return_value=[fixture])
        user_commands.db.get_fixture_by_id = AsyncMock(
            side_effect=lambda fixture_id, guild_id: (
                fixture if (fixture_id, guild_id) == (1, "111111") else None
            )
        )

        await user_commands.predict.callback(user_commands, mock_interaction)
        first_modal = mock_interaction.modal_sent["modal"]
        first_modal.predictions_input._value = "Team C - Team D 1-1\nTeam E - Team F 0-2"
        await first_modal.on_submit(mock_interaction)

        thread = user_commands.bot.get_channel(700001)
        first_public_message = thread.message_objects[1]

        await user_commands.predict.callback(user_commands, mock_interaction)
        second_modal = mock_interaction.modal_sent["modal"]
        second_modal.predictions_input._value = "Team A - Team B 2-0\nTeam C - Team D 1-1"
        await second_modal.on_submit(mock_interaction)

        prediction = await database.get_prediction(1, str(mock_interaction.user.id), "111111")
        assert prediction is not None
        assert prediction["public_message_id"] == "2"
        first_public_message.delete.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_my_predictions_shows_sparse_pending_prediction(
        self, user_commands, mock_interaction, database
    ):
        await database.save_prediction(
            1,
            str(mock_interaction.user.id),
            mock_interaction.user.name,
            ["1-1", "0-2"],
            True,
            predicted_game_indexes=[1, 2],
            pending_partial_approval=True,
        )

        await user_commands.my_predictions.callback(user_commands, mock_interaction)

        content = mock_interaction.response_sent[-1]["content"]
        assert "2. Team C - Team D **1-1**" in content
        assert "3. Team E - Team F **0-2**" in content
        assert "Late prediction awaiting admin review" in content

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_reports_closed_fixture_during_submit(
        self, user_commands, mock_interaction, monkeypatch
    ):
        await _attach_prediction_threads(
            user_commands, user_commands.db, [1], mock_interaction.guild
        )
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
        await _attach_prediction_threads(
            user_commands, user_commands.db, [1], mock_interaction.guild
        )
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
        thread = user_commands.bot.get_channel(700001)
        thread.message_objects[1].delete.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_reports_missing_prediction_thread(
        self, user_commands, mock_interaction
    ):
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert (
            "does not have a usable prediction thread"
            in mock_interaction.response_sent[-1]["content"]
        )
        assert (
            await user_commands.db.get_prediction(1, str(mock_interaction.user.id), "111111")
            is None
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_uses_fetch_channel_fallback(
        self, user_commands, mock_interaction, database
    ):
        thread = MockThread(thread_id="700001", name="week-1", guild=mock_interaction.guild)
        await database.update_fixture_announcement(1, message_id="700001", channel_id="123456")
        user_commands.bot.get_channel.return_value = None
        user_commands.bot.fetch_channel = AsyncMock(return_value=thread)

        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is not None
        user_commands.bot.fetch_channel.assert_awaited_once_with(700001)

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_reports_thread_post_failure(
        self, user_commands, mock_interaction, database, monkeypatch
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        thread = user_commands.bot.get_channel(700001)

        import discord

        async def raise_http_exception(*_args, **_kwargs):
            raise discord.HTTPException(response=AsyncMock(status=500), message="boom")

        monkeypatch.setattr(thread, "send", raise_http_exception)

        await user_commands.predict.callback(user_commands, mock_interaction)
        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = (
            "Team A - Team B 2-1\nTeam C - Team D 1-1\nTeam E - Team F 0-2"
        )
        await modal.on_submit(mock_interaction)

        assert (
            "does not have a usable prediction thread"
            in mock_interaction.response_sent[-1]["content"]
        )
        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_deletes_public_post_if_fixture_closes_during_save(
        self, user_commands, mock_interaction, monkeypatch
    ):
        await _attach_prediction_threads(
            user_commands, user_commands.db, [1], mock_interaction.guild
        )
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

        thread = user_commands.bot.get_channel(700001)
        thread.message_objects[1].delete.assert_awaited_once()
        assert (
            "closed before your prediction could be saved"
            in mock_interaction.response_sent[-1]["content"]
        )

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_predict_modal_rejects_empty_partial_parse_result(
        self, user_commands, mock_interaction, database
    ):
        await _attach_prediction_threads(user_commands, database, [1], mock_interaction.guild)
        await user_commands.predict.callback(user_commands, mock_interaction)

        modal = mock_interaction.modal_sent["modal"]
        modal.predictions_input._value = ","
        await modal.on_submit(mock_interaction)

        assert (
            "Please enter at least one prediction before submitting."
            in mock_interaction.response_sent[-1]["content"]
        )
        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is None

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("fixture_with_dm")
    async def test_continue_predict_button_opens_next_modal(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_two_id = await database.create_fixture("111111", 2, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [1, fixture_two_id], mock_interaction.guild
        )

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
        fixture_one_id = await database.create_fixture("111111", 1, sample_games, deadline)
        fixture_two_id = await database.create_fixture("111111", 2, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [fixture_one_id, fixture_two_id], mock_interaction.guild
        )

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

        assert await database.get_prediction(1, str(mock_interaction.user.id), "111111") is not None
        assert await database.get_prediction(2, str(mock_interaction.user.id), "111111") is not None
        assert "You're done for now." in mock_interaction.response_sent[-1]["content"]
        assert "view" not in mock_interaction.response_sent[-1]

    @pytest.mark.asyncio
    async def test_continue_predict_view_paginates_past_25(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        fixture_ids = []
        for week in range(1, 28):
            fixture_ids.append(
                await database.create_fixture("111111", week, sample_games, deadline)
            )
        await _attach_prediction_threads(
            user_commands, database, fixture_ids, mock_interaction.guild
        )

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
        next_button = next(
            child for child in continue_view.children if getattr(child, "label", None) == "Next"
        )
        await next_button.callback(mock_interaction)

        paged_continue_view = mock_interaction.response_sent[-1]["view"]
        week_27_button = next(
            child
            for child in paged_continue_view.children
            if getattr(child, "label", None) == "Predict Week 27"
        )
        await week_27_button.callback(mock_interaction)

        assert mock_interaction.modal_sent["modal"].title == "Predict Week 27"

    @pytest.mark.asyncio
    async def test_fixture_picker_rejects_wrong_user(
        self, user_commands, mock_interaction, database, sample_games
    ):
        deadline = datetime.now(UTC) + timedelta(days=1)
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)

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
        await database.create_fixture("111111", 1, sample_games, deadline)
        await database.create_fixture("111111", 2, sample_games, deadline)

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
        fixture_one_id = await database.create_fixture("111111", 1, sample_games, deadline)
        fixture_two_id = await database.create_fixture("111111", 2, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [fixture_one_id, fixture_two_id], mock_interaction.guild
        )

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
        fixture_one_id = await database.create_fixture("111111", 1, sample_games, deadline)
        fixture_two_id = await database.create_fixture("111111", 2, sample_games, deadline)
        await _attach_prediction_threads(
            user_commands, database, [fixture_one_id, fixture_two_id], mock_interaction.guild
        )

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
            fixture_two_id,
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
