"""Shared admin workflows for commands and admin panel views."""

from __future__ import annotations

from dataclasses import dataclass

from typer_bot.database import Database
from typer_bot.utils import calculate_points, parse_line_predictions


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

    async def get_fixture_by_week(self, week_number: int) -> dict | None:
        """Resolve a fixture by week, regardless of status."""
        return await self.db.get_fixture_by_week(week_number)

    async def get_recent_fixtures(self, limit: int = 25) -> list[dict]:
        """Return recent fixtures for panel selectors."""
        return await self.db.get_recent_fixtures(limit)

    async def _build_score_result(self, fixture_id: int) -> FixtureScoreResult:
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

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

    async def calculate_fixture_scores(self, fixture_id: int) -> FixtureScoreResult:
        """Recalculate one fixture and refresh standings."""
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

        results = await self.db.get_results(fixture_id)
        if not results:
            raise ValueError("No results entered for this fixture")

        predictions = await self.db.get_all_predictions(fixture_id)
        if not predictions:
            raise ValueError("No predictions found for this fixture")

        scores = []
        for prediction in predictions:
            score_data = calculate_points(
                prediction["predictions"],
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

        return await self._build_score_result(fixture_id)

    async def maybe_recalculate_fixture(self, fixture_id: int) -> FixtureScoreResult | None:
        """Recalculate a fixture only when it has already been scored."""
        if not await self.db.fixture_has_scores(fixture_id):
            return None
        return await self.calculate_fixture_scores(fixture_id)

    async def get_fixture_prediction_summary(self, fixture_id: int) -> tuple[dict, list[dict]]:
        """Return fixture and its predictions for panel display."""
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

        predictions = await self.db.get_all_predictions(fixture_id)
        if not predictions:
            raise ValueError("No predictions saved for this fixture")

        predictions.sort(key=lambda prediction: prediction["user_name"].lower())
        return fixture, predictions

    async def replace_prediction(
        self,
        fixture_id: int,
        user_id: str,
        prediction_lines: str,
        admin_user_id: str,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        """Replace a stored prediction through an explicit admin action."""
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

        existing_prediction = await self.db.get_prediction(fixture_id, user_id)
        if existing_prediction is None:
            raise ValueError("Prediction not found for that user")

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
            raise ValueError("Prediction disappeared after update")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id)
        return fixture, refreshed_prediction, recalculation

    async def toggle_late_penalty_waiver(
        self,
        fixture_id: int,
        user_id: str,
    ) -> tuple[dict, dict, FixtureScoreResult | None]:
        """Toggle the waiver flag for a stored late prediction."""
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

        prediction = await self.db.get_prediction(fixture_id, user_id)
        if prediction is None:
            raise ValueError("Prediction not found for that user")
        if not prediction["is_late"]:
            raise ValueError("That prediction was submitted on time")

        waived = await self.db.toggle_late_penalty_waiver_with_recalc(fixture_id, user_id)
        if waived is None:
            raise ValueError("Late waiver update failed")

        refreshed_prediction = await self.db.get_prediction(fixture_id, user_id)
        if refreshed_prediction is None:
            raise ValueError("Prediction disappeared after waiver update")

        recalculation = None
        if await self.db.fixture_has_scores(fixture_id):
            recalculation = await self._build_score_result(fixture_id)
        return fixture, refreshed_prediction, recalculation

    async def correct_results(
        self,
        fixture_id: int,
        results_lines: str,
    ) -> tuple[dict, list[str], FixtureScoreResult | None]:
        """Replace stored results and recalculate scored fixtures."""
        fixture = await self.db.get_fixture_by_id(fixture_id)
        if fixture is None:
            raise ValueError("Fixture not found")

        results, errors = parse_line_predictions(results_lines, fixture["games"])
        if errors:
            raise ValueError("\n".join(errors))

        recalculated = await self.db.save_results_with_recalc(fixture_id, results)
        recalculation = await self._build_score_result(fixture_id) if recalculated else None
        return fixture, results, recalculation
