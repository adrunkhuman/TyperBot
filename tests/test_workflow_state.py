"""Tests for centralized workflow state ownership."""

from datetime import UTC, datetime, timedelta

from typer_bot.utils import now


class TestSessionCleanup:
    def test_fixture_sessions_expire_when_accessed(self, workflow_state):
        session = workflow_state.start_fixture_session("user-1", 123, 456)
        session.created_at = datetime.now(UTC) - timedelta(hours=2)

        assert workflow_state.get_fixture_session("user-1") is None

    def test_results_sessions_expire_when_accessed(self, workflow_state):
        session = workflow_state.start_results_session("user-1", 99, 456)
        session.created_at = datetime.now(UTC) - timedelta(hours=2)

        assert workflow_state.get_results_session("user-1") is None

    def test_prediction_sessions_expire_when_accessed(self, workflow_state):
        session = workflow_state.set_prediction_session("user-1", step="select")
        session.created_at = now() - timedelta(hours=2)

        assert workflow_state.get_prediction_session("user-1") is None


class TestCooldownTracking:
    def test_thread_cooldowns_drop_entries_older_than_one_hour(self, workflow_state):
        stale_time = datetime.now(UTC) - timedelta(hours=2)
        current_time = datetime.now(UTC)

        workflow_state.record_thread_prediction_attempt("stale-user", stale_time)
        workflow_state.record_thread_prediction_attempt("fresh-user", current_time)

        assert workflow_state.get_thread_prediction_cooldown("stale-user") is None
        assert workflow_state.get_thread_prediction_cooldown("fresh-user") == current_time

    def test_calculate_cooldown_cleanup_removes_stale_entries(self, workflow_state):
        current_time = now().timestamp()
        workflow_state.record_calculate_cooldown(
            "stale-user",
            current_time=current_time - timedelta(hours=2).total_seconds(),
        )

        remaining = workflow_state.get_calculate_cooldown_remaining(
            "stale-user",
            current_time=current_time,
            cooldown_seconds=30.0,
        )

        assert remaining == 0.0
        assert workflow_state.get_calculate_cooldown("stale-user") is None
