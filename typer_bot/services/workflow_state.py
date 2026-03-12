"""Process-local DM workflow and cooldown state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from typer_bot.utils import now

SESSION_TIMEOUT = timedelta(hours=1)
COOLDOWN_ENTRY_EXPIRY = timedelta(hours=1)
PredictionStep = Literal["select", "predict", "continue"]


@dataclass(slots=True)
class FixtureSession:
    """In-memory state for fixture creation DMs."""

    channel_id: int
    guild_id: int
    step: str = "games"
    games: list[str] = field(default_factory=list)
    default_deadline: datetime | None = None
    deadline: datetime | None = None
    week_number: int | None = None
    preview: str | None = None
    created_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class ResultsSession:
    """In-memory state for result-entry DMs."""

    fixture_id: int
    guild_id: int
    created_at: datetime = field(default_factory=now)


@dataclass(slots=True)
class PredictionSession:
    """In-memory state for one user's DM prediction flow."""

    step: PredictionStep
    fixture_ids: list[int] = field(default_factory=list)
    fixture_id: int | None = None
    completed_fixture_ids: list[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=now)


class WorkflowStateStore:
    """Owns process-local DM sessions and cooldowns."""

    def __init__(self):
        self._fixture_sessions: dict[str, FixtureSession] = {}
        self._results_sessions: dict[str, ResultsSession] = {}
        self._prediction_sessions: dict[str, PredictionSession] = {}
        self._thread_prediction_cooldowns: dict[str, datetime] = {}
        self._calculate_cooldowns: dict[str, float] = {}

    @staticmethod
    def _is_expired(created_at: datetime, *, current_time: datetime | None = None) -> bool:
        current_time = current_time or now()
        return current_time - created_at > SESSION_TIMEOUT

    def _cleanup_fixture_sessions(self) -> None:
        current_time = now()
        expired_users = [
            user_id
            for user_id, session in self._fixture_sessions.items()
            if self._is_expired(session.created_at, current_time=current_time)
        ]
        for user_id in expired_users:
            self._fixture_sessions.pop(user_id, None)

    def start_fixture_session(self, user_id: str, channel_id: int, guild_id: int) -> FixtureSession:
        self._cleanup_fixture_sessions()
        session = FixtureSession(channel_id=channel_id, guild_id=guild_id)
        self._fixture_sessions[user_id] = session
        return session

    def get_fixture_session(self, user_id: str) -> FixtureSession | None:
        self._cleanup_fixture_sessions()
        return self._fixture_sessions.get(user_id)

    def has_fixture_session(self, user_id: str) -> bool:
        return self.get_fixture_session(user_id) is not None

    def clear_fixture_session(self, user_id: str) -> None:
        self._fixture_sessions.pop(user_id, None)

    def _cleanup_results_sessions(self) -> None:
        current_time = now()
        expired_users = [
            user_id
            for user_id, session in self._results_sessions.items()
            if self._is_expired(session.created_at, current_time=current_time)
        ]
        for user_id in expired_users:
            self._results_sessions.pop(user_id, None)

    def start_results_session(self, user_id: str, fixture_id: int, guild_id: int) -> ResultsSession:
        self._cleanup_results_sessions()
        session = ResultsSession(fixture_id=fixture_id, guild_id=guild_id)
        self._results_sessions[user_id] = session
        return session

    def get_results_session(self, user_id: str) -> ResultsSession | None:
        self._cleanup_results_sessions()
        return self._results_sessions.get(user_id)

    def has_results_session(self, user_id: str) -> bool:
        return self.get_results_session(user_id) is not None

    def clear_results_session(self, user_id: str) -> None:
        self._results_sessions.pop(user_id, None)

    def _cleanup_prediction_sessions(self) -> None:
        current_time = now()
        expired_users = [
            user_id
            for user_id, session in self._prediction_sessions.items()
            if self._is_expired(session.created_at, current_time=current_time)
        ]
        for user_id in expired_users:
            self._prediction_sessions.pop(user_id, None)

    def set_prediction_session(
        self,
        user_id: str,
        *,
        step: PredictionStep,
        fixture_ids: list[int] | None = None,
        fixture_id: int | None = None,
        completed_fixture_ids: list[int] | None = None,
    ) -> PredictionSession:
        session = PredictionSession(
            step=step,
            fixture_ids=fixture_ids or [],
            fixture_id=fixture_id,
            completed_fixture_ids=completed_fixture_ids or [],
        )
        self._prediction_sessions[user_id] = session
        return session

    def get_prediction_session(self, user_id: str) -> PredictionSession | None:
        self._cleanup_prediction_sessions()
        return self._prediction_sessions.get(user_id)

    def clear_prediction_session(self, user_id: str) -> None:
        self._prediction_sessions.pop(user_id, None)

    def record_thread_prediction_attempt(
        self, user_id: str, current_time: datetime
    ) -> datetime | None:
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

    def get_calculate_cooldown(self, user_id: str) -> float | None:
        return self._calculate_cooldowns.get(user_id)

    def clear_calculate_cooldowns(self) -> None:
        self._calculate_cooldowns.clear()
