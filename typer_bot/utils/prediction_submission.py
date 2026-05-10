"""Shared prediction submission decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PredictionSubmission:
    is_late: bool
    is_partial: bool
    pending_partial_approval: bool
    public_message_id: str | None
    public_message_kind: str | None


def build_prediction_submission(
    *,
    fixture: dict,
    predicted_game_indexes: list[int],
    submitted_at: datetime,
    public_message_id: str | None = None,
    public_message_kind: str | None = None,
) -> PredictionSubmission:
    is_late = submitted_at > fixture["deadline"]
    is_partial = len(predicted_game_indexes) < len(fixture["games"])
    pending_partial_approval = is_late and is_partial
    return PredictionSubmission(
        is_late=is_late,
        is_partial=is_partial,
        pending_partial_approval=pending_partial_approval,
        public_message_id=public_message_id if pending_partial_approval else None,
        public_message_kind=public_message_kind if pending_partial_approval else None,
    )
