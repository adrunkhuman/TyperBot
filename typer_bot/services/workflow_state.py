"""Process-local cooldown state."""

from __future__ import annotations

from datetime import datetime, timedelta

from typer_bot.utils import now

COOLDOWN_ENTRY_EXPIRY = timedelta(hours=1)


class WorkflowStateStore:
    """Own all process-local cooldowns used by active workflows.

    Thread prediction rate limiting and the admin score-calculation cooldown are
    intentionally process-local and reset on restart.
    """

    def __init__(self):
        self._thread_prediction_cooldowns: dict[str, datetime] = {}
        self._calculate_cooldowns: dict[str, float] = {}

    def record_thread_prediction_attempt(
        self, user_id: str, current_time: datetime
    ) -> datetime | None:
        """Record a thread prediction attempt and return the previous timestamp.

        Also prunes cooldown entries older than ``COOLDOWN_ENTRY_EXPIRY`` so
        callers do not need a separate cleanup step before rate-limit checks.
        """
        previous_attempt = self._thread_prediction_cooldowns.get(user_id)
        self._thread_prediction_cooldowns[user_id] = current_time

        cutoff = current_time - COOLDOWN_ENTRY_EXPIRY
        expired_users = [
            stored_user_id
            for stored_user_id, timestamp in self._thread_prediction_cooldowns.items()
            if timestamp < cutoff
        ]
        for stored_user_id in expired_users:
            self._thread_prediction_cooldowns.pop(stored_user_id, None)

        return previous_attempt

    def _cleanup_thread_cooldowns(self) -> None:
        cutoff = now() - COOLDOWN_ENTRY_EXPIRY
        expired = [uid for uid, ts in self._thread_prediction_cooldowns.items() if ts < cutoff]
        for uid in expired:
            self._thread_prediction_cooldowns.pop(uid, None)

    def get_thread_prediction_cooldown(self, user_id: str) -> datetime | None:
        return self._thread_prediction_cooldowns.get(user_id)

    def clear_thread_prediction_cooldowns(self) -> None:
        self._thread_prediction_cooldowns.clear()

    def get_calculate_cooldown_remaining(
        self,
        user_id: str,
        *,
        current_time: float,
        cooldown_seconds: float,
    ) -> float:
        """Return remaining calculate cooldown seconds for a user.

        ``current_time`` is expected to be a Unix timestamp float. Expired
        entries are pruned before the remaining duration is calculated.
        """
        cutoff = current_time - COOLDOWN_ENTRY_EXPIRY.total_seconds()
        expired_users = [
            stored_user_id
            for stored_user_id, timestamp in self._calculate_cooldowns.items()
            if timestamp < cutoff
        ]
        for stored_user_id in expired_users:
            self._calculate_cooldowns.pop(stored_user_id, None)

        last_used = self._calculate_cooldowns.get(user_id)
        if last_used is None:
            return 0.0

        return max(0.0, cooldown_seconds - (current_time - last_used))

    def record_calculate_cooldown(self, user_id: str, *, current_time: float) -> None:
        self._calculate_cooldowns[user_id] = current_time

    def _cleanup_calculate_cooldowns(self) -> None:
        cutoff = now().timestamp() - COOLDOWN_ENTRY_EXPIRY.total_seconds()
        expired = [uid for uid, ts in self._calculate_cooldowns.items() if ts < cutoff]
        for uid in expired:
            self._calculate_cooldowns.pop(uid, None)

    def get_calculate_cooldown(self, user_id: str) -> float | None:
        return self._calculate_cooldowns.get(user_id)

    def clear_calculate_cooldowns(self) -> None:
        self._calculate_cooldowns.clear()

    def cleanup_all_expired(self) -> int:
        """Run all cooldown cleanups and return removed entry count."""
        before = len(self._thread_prediction_cooldowns) + len(self._calculate_cooldowns)
        self._cleanup_thread_cooldowns()
        self._cleanup_calculate_cooldowns()
        after = len(self._thread_prediction_cooldowns) + len(self._calculate_cooldowns)
        return before - after
