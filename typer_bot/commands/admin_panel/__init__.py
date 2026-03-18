"""Admin panel views and interaction components."""

from .base import AdminPanelHomeView
from .fixtures import (
    DeleteConfirmView,
    FixturesPanelView,
    OpenFixtureWarningView,
    _build_delete_confirmation_content,
)
from .modals import CorrectResultsModal, ReplacePredictionModal
from .predictions import PredictionsPanelView
from .results import ResultsPanelView

__all__ = [
    "AdminPanelHomeView",
    "CorrectResultsModal",
    "DeleteConfirmView",
    "FixturesPanelView",
    "OpenFixtureWarningView",
    "PredictionsPanelView",
    "ReplacePredictionModal",
    "ResultsPanelView",
    "_build_delete_confirmation_content",
]
