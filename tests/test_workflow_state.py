"""Tests for centralized workflow state ownership."""

from datetime import UTC, datetime, timedelta

from typer_bot.utils import now


class TestCleanupAllExpired:
    def test_returns_zero_when_nothing_expired(self, workflow_state):
        workflow_state.record_thread_prediction_attempt("user-1", datetime.now(UTC))
        workflow_state.record_calculate_cooldown("user-2", current_time=now().timestamp())

        assert workflow_state.cleanup_all_expired() == 0

    def test_counts_expired_sessions_across_all_types(self, workflow_state):
        workflow_state.record_thread_prediction_attempt(
            "user-1", datetime.now(UTC) - timedelta(hours=2)
        )
        workflow_state.record_calculate_cooldown(
            "user-2",
            current_time=(datetime.now(UTC) - timedelta(hours=2)).timestamp(),
        )

        assert workflow_state.cleanup_all_expired() == 2

    def test_only_removes_expired_leaves_fresh(self, workflow_state):
        workflow_state.record_thread_prediction_attempt(
            "stale-user", datetime.now(UTC) - timedelta(hours=2)
        )
        fresh_time = datetime.now(UTC)
        workflow_state.record_thread_prediction_attempt("fresh-user", fresh_time)

        removed = workflow_state.cleanup_all_expired()

        assert removed == 0
        assert workflow_state.get_thread_prediction_cooldown("fresh-user") == fresh_time


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
