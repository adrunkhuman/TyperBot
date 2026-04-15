"""Admin panel views and interaction components."""

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
from .unified import UnifiedAdminPanelView

__all__ = [
    "CorrectResultsModal",
    "CreateFixtureModal",
    "DeleteConfirmView",
    "EnterResultsModal",
    "FixturesPanelView",
    "PredictionsPanelView",
    "ReplacePredictionModal",
    "ResultsPanelView",
    "UnifiedAdminPanelView",
    "_build_delete_confirmation_content",
]
