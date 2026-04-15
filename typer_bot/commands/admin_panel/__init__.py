"""Admin panel views and interaction components."""

from .base import AdminPanelHomeView
from .fixtures import (
    DeleteConfirmView,
    FixturesPanelView,
    _build_delete_confirmation_content,
)
from .modals import (
    CorrectResultsModal,
    CreateFixtureModal,
    EnterResultsModal,
    ReplacePredictionModal,
)
from .predictions import PredictionsPanelView
from .results import ResultsPanelView

__all__ = [
    "AdminPanelHomeView",
    "CorrectResultsModal",
    "CreateFixtureModal",
    "DeleteConfirmView",
    "EnterResultsModal",
    "FixturesPanelView",
    "PredictionsPanelView",
    "ReplacePredictionModal",
    "ResultsPanelView",
    "_build_delete_confirmation_content",
]
