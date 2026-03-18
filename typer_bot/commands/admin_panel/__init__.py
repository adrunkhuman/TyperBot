"""Admin panel views and interaction components."""

from .base import AdminPanelHomeView
from .fixtures import DeleteConfirmView, FixturesPanelView, OpenFixtureWarningView
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
]
