"""Shared admin workflows for commands and admin panel views."""

from __future__ import annotations

from dataclasses import dataclass

from typer_bot.database import Database
from typer_bot.services.errors import (
    FixtureNotFoundError,
    NoPredictionsSavedError,
    PredictionDisappearedError,
    PredictionNotFoundError,
)
from typer_bot.utils import align_predictions_to_fixture, calculate_points, parse_line_predictions


@dataclass(slots=True)
class FixtureScoreResult:
    """Calculated scoring payload for a fixture."""

    fixture: dict
    results: list[str]
    predictions: list[dict]
    scores: list[dict]
    standings: list[dict]
    last_fixture: dict | None


class AdminService:
    """Admin workflows shared by slash commands and interaction views."""

    def __init__(self, db: Database):
        self.db = db

    async def _build_score_result(
        self, fixture_id: int, guild_id: str | None = None
    ) -> FixtureScoreResult:
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        results = await self.db.get_results(fixture_id)
        if not results:
            raise ValueError("No results entered for this fixture")

        predictions = await self.db.get_all_predictions(fixture_id)
        if not predictions:
            raise ValueError("No predictions found for this fixture")

        scores = await self.db.get_scores_for_fixture(fixture_id)
        standings = await self.db.get_standings()
        last_fixture = await self.db.get_last_fixture_scores()
        return FixtureScoreResult(
            fixture=fixture,
            results=results,
            predictions=predictions,
            scores=scores,
            standings=standings,
            last_fixture=last_fixture,
        )

    async def calculate_fixture_scores(
        self, fixture_id: int, guild_id: str | None = None
    ) -> FixtureScoreResult:
        """Recalculate one fixture and refresh standings."""
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        results = await self.db.get_results(fixture_id)
        if not results:
            raise ValueError("No results entered for this fixture")

        predictions = await self.db.get_all_predictions(fixture_id)
        if not predictions:
            raise ValueError("No predictions found for this fixture")

        scores = []
        for prediction in predictions:
            aligned_predictions = align_predictions_to_fixture(
                prediction["predictions"],
                prediction["predicted_game_indexes"],
                len(results),
            )
            score_data = calculate_points(
                aligned_predictions,
                results,
                prediction["is_late"],
                prediction["late_penalty_waived"],
            )
            scores.append(
                {
                    "user_id": prediction["user_id"],
                    "user_name": prediction["user_name"],
                    "points": score_data["points"],
                    "exact_scores": score_data["exact_scores"],
                    "correct_results": score_data["correct_results"],
                }
            )

        scores.sort(
            key=lambda score: (
                -score["points"],
                -score["exact_scores"],
                -score["correct_results"],
                score["user_name"].lower(),
            )
        )
        await self.db.save_scores(fixture_id, scores)

        return await self._build_score_result(fixture_id, guild_id)

    async def maybe_recalculate_fixture(
        self, fixture_id: int, guild_id: str | None = None
    ) -> FixtureScoreResult | None:
        """Recalculate a fixture only when it has already been scored."""
        if not await self.db.fixture_has_scores(fixture_id):
            return None
        return await self.calculate_fixture_scores(fixture_id, guild_id)

    async def get_fixture_prediction_summary(
        self, fixture_id: int, guild_id: str | None = None
    ) -> tuple[dict, list[dict]]:
        """Return fixture and its predictions for panel display.

        Raises:
            FixtureNotFoundError: Fixture was deleted before the panel action ran.
            NoPredictionsSavedError: Fixture still exists but has no saved predictions.
        """
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        predictions = await self.db.get_all_predictions(fixture_id, include_pending=True)
        if not predictions:
            raise NoPredictionsSavedError

        predictions.sort(key=lambda prediction: prediction["user_name"].lower())
        return fixture, predictions

    async def approve_partial_prediction(
        self,
        fixture_id: int,
        user_id: str,
        admin_user_id: str,
        guild_id: str | None = None,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        prediction = await self.db.get_prediction(fixture_id, user_id)
        if prediction is None or not prediction["pending_partial_approval"]:
            raise ValueError("No late prediction awaiting review for that user")

        approved = await self.db.approve_partial_prediction(fixture_id, user_id, admin_user_id)
        if not approved:
            raise ValueError("Partial approval failed")

        refreshed_prediction = await self.db.get_prediction(fixture_id, user_id)
        if refreshed_prediction is None:
            raise ValueError("Prediction disappeared after approval")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id, guild_id)
        return fixture, refreshed_prediction, recalculation

    async def reject_partial_prediction(
        self,
        fixture_id: int,
        user_id: str,
        guild_id: str | None = None,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        prediction = await self.db.get_prediction(fixture_id, user_id)
        if prediction is None or not prediction["pending_partial_approval"]:
            raise ValueError("No late prediction awaiting review for that user")

        rejected = await self.db.reject_partial_prediction(fixture_id, user_id)
        if not rejected:
            raise ValueError("Partial rejection failed")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id, guild_id)
        return fixture, prediction, recalculation

    async def replace_prediction(
        self,
        fixture_id: int,
        user_id: str,
        prediction_lines: str,
        admin_user_id: str,
        guild_id: str | None = None,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        """Replace a stored prediction through an explicit admin action.

        Raises:
            FixtureNotFoundError: Fixture was deleted before the admin action ran.
            PredictionNotFoundError: Selected prediction no longer exists.
            PredictionDisappearedError: Update succeeded but the refreshed row vanished.
            ValueError: Validation or write failures that should surface directly to admins.
        """
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        existing_prediction = await self.db.get_prediction(fixture_id, user_id)
        if existing_prediction is None:
            raise PredictionNotFoundError

        predictions, errors = parse_line_predictions(prediction_lines, fixture["games"])
        if errors:
            raise ValueError("\n".join(errors))

        updated = await self.db.admin_update_prediction_with_recalc(
            fixture_id,
            user_id,
            predictions,
            admin_user_id,
        )
        if not updated:
            raise ValueError("Prediction update failed")

        refreshed_prediction = await self.db.get_prediction(fixture_id, user_id)
        if refreshed_prediction is None:
            raise PredictionDisappearedError("update")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id, guild_id)
        return fixture, refreshed_prediction, recalculation

    async def toggle_late_penalty_waiver(
        self,
        fixture_id: int,
        user_id: str,
        guild_id: str | None = None,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        """Toggle the waiver flag for a stored late prediction.

        Raises:
            FixtureNotFoundError: Fixture was deleted before the admin action ran.
            PredictionNotFoundError: Selected prediction no longer exists.
            PredictionDisappearedError: Waiver update succeeded but the refreshed row vanished.
            ValueError: On-time or write-failure cases that should surface directly to admins.
        """
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        prediction = await self.db.get_prediction(fixture_id, user_id)
        if prediction is None:
            raise PredictionNotFoundError
        if not prediction["is_late"]:
            raise ValueError("That prediction was submitted on time")

        waived = await self.db.toggle_late_penalty_waiver_with_recalc(fixture_id, user_id)
        if waived is None:
            raise ValueError("Late waiver update failed")

        refreshed_prediction = await self.db.get_prediction(fixture_id, user_id)
        if refreshed_prediction is None:
            raise PredictionDisappearedError("waiver update")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id, guild_id)
        return fixture, refreshed_prediction, recalculation

    async def correct_results(
        self,
        fixture_id: int,
        results_lines: str,
        guild_id: str | None = None,
    ) -> tuple[dict, list[str], FixtureScoreResult | None]:
        """Replace stored results and recalculate scored fixtures."""
        fixture = await self.db.get_fixture_by_id(fixture_id, guild_id)
        if fixture is None:
            raise FixtureNotFoundError

        results, errors = parse_line_predictions(results_lines, fixture["games"])
        if errors:
            raise ValueError("\n".join(errors))

        recalculated = await self.db.save_results_with_recalc(fixture_id, results)
        recalculation = (
            await self._build_score_result(fixture_id, guild_id) if recalculated else None
        )
        return fixture, results, recalculation
