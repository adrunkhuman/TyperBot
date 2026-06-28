"""Utility functions and helpers."""

from .discord_messages import (
    DISCORD_MESSAGE_LIMIT,
    build_discord_message_chunks,
    build_scoreboard_with_mentions_chunks,
    chunk_discord_message,
)
from .permissions import (
    SETUP_REQUIRED_MESSAGE,
    get_admin_permission_error,
    get_admin_role_mention,
    get_configured_admin_role_mention,
    has_setup_permission,
    is_admin,
    is_admin_member,
    is_configured_admin,
)
from .prediction_parser import (
    ascii_username,
    format_fixture_results,
    format_predictions_preview,
    format_standings,
    parse_prediction_lines,
)
from .prediction_submission import PredictionSubmission, build_prediction_submission
from .scoring import (
    DEFAULT_SCORING_RULES,
    align_predictions_to_fixture,
    build_fixture_scores,
    calculate_points,
    normalize_scoring_rules,
)
from .timezone import APP_TZ, format_for_discord, now, parse_deadline, parse_iso

__all__ = [
    "ascii_username",
    "get_admin_role_mention",
    "get_configured_admin_role_mention",
    "get_admin_permission_error",
    "has_setup_permission",
    "is_admin",
    "is_admin_member",
    "is_configured_admin",
    "parse_prediction_lines",
    "format_fixture_results",
    "format_predictions_preview",
    "format_standings",
    "PredictionSubmission",
    "build_prediction_submission",
    "align_predictions_to_fixture",
    "DEFAULT_SCORING_RULES",
    "calculate_points",
    "build_fixture_scores",
    "normalize_scoring_rules",
    "now",
    "parse_deadline",
    "format_for_discord",
    "parse_iso",
    "APP_TZ",
    "SETUP_REQUIRED_MESSAGE",
    "DISCORD_MESSAGE_LIMIT",
    "build_discord_message_chunks",
    "build_scoreboard_with_mentions_chunks",
    "chunk_discord_message",
]
