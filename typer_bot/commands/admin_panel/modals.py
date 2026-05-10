"""Compatibility exports for admin panel modal interactions."""

from .fixture_modals import CreateFixtureConfirmView, CreateFixtureModal
from .prediction_modals import CorrectResultsModal, ReplacePredictionModal
from .result_modals import EnterResultsConfirmView, EnterResultsModal

__all__ = [
    "CorrectResultsModal",
    "CreateFixtureConfirmView",
    "CreateFixtureModal",
    "EnterResultsConfirmView",
    "EnterResultsModal",
    "ReplacePredictionModal",
]
