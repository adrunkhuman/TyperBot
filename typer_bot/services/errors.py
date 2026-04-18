"""Typed service-layer exceptions for recoverable admin flows."""


class AdminFlowError(ValueError):
    """Base class for recoverable admin workflow failures."""


class FixtureNotFoundError(AdminFlowError):
    def __init__(self):
        super().__init__("Fixture not found")


class NoPredictionsSavedError(AdminFlowError):
    def __init__(self):
        super().__init__("No predictions saved for this fixture")


class PredictionNotFoundError(AdminFlowError):
    def __init__(self):
        super().__init__("Prediction not found for that user")


class PredictionDisappearedError(AdminFlowError):
    """Prediction vanished after a successful admin write, usually due to stale state."""

    def __init__(self, action: str):
        super().__init__(f"Prediction disappeared after {action}")
