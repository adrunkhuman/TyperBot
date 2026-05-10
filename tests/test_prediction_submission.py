"""Tests for shared prediction submission decisions."""

from datetime import UTC, datetime, timedelta

from typer_bot.utils import build_prediction_submission


def test_on_time_full_submission_is_ready_for_scoring():
    deadline = datetime.now(UTC) + timedelta(hours=1)
    fixture = {"deadline": deadline, "games": ["A - B", "C - D"]}

    submission = build_prediction_submission(
        fixture=fixture,
        predicted_game_indexes=[0, 1],
        submitted_at=deadline - timedelta(minutes=1),
        public_message_id="123",
        public_message_kind="thread_message",
    )

    assert submission.is_late is False
    assert submission.is_partial is False
    assert submission.pending_partial_approval is False
    assert submission.public_message_id is None
    assert submission.public_message_kind is None


def test_late_partial_submission_keeps_public_review_metadata():
    deadline = datetime.now(UTC) - timedelta(hours=1)
    fixture = {"deadline": deadline, "games": ["A - B", "C - D"]}

    submission = build_prediction_submission(
        fixture=fixture,
        predicted_game_indexes=[0],
        submitted_at=deadline + timedelta(minutes=1),
        public_message_id="123",
        public_message_kind="thread_message",
    )

    assert submission.is_late is True
    assert submission.is_partial is True
    assert submission.pending_partial_approval is True
    assert submission.public_message_id == "123"
    assert submission.public_message_kind == "thread_message"
