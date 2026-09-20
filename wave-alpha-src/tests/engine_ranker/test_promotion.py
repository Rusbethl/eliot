"""Tests for engine_ranker.promotion.evaluate_promotion."""

from __future__ import annotations

from datetime import date

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.engine_ranker.promotion import (
    PromotionVerdict,
    evaluate_promotion,
)
from wave_alpha.engine_ranker.training import OutcomeDatasetRow


def _row(
    label: int, fit: float, fib: float, rec: float, coh: float, degree: int = 2
) -> OutcomeDatasetRow:
    return OutcomeDatasetRow(
        ticker="X",
        as_of=date(2024, 1, 1),
        features=EngineRankerFeatures(
            template_fit=fit,
            fib=fib,
            recency=rec,
            coherence=coh,
            degree=degree,
        ),
        label=label,
        degree=degree,
    )


def _ranker_that_always(prob: float) -> LogisticEngineRanker:
    """Synthetic logistic: weights=0, intercept tuned to a target probability."""
    import math
    import statistics

    # sigmoid(b) = prob → b = log(prob/(1-prob))
    b = math.log(prob / (1.0 - prob))
    intercepts = {2: b}
    return LogisticEngineRanker(
        version="vfake",
        feature_order=("template_fit", "fib", "recency", "coherence"),
        weights=(0.0, 0.0, 0.0, 0.0),
        intercept_per_degree=intercepts,
        _median_intercept=statistics.median(intercepts.values()),
    )


def test_promote_when_logistic_strictly_better() -> None:
    """All rows labeled 1; logistic predicts 0.9, heuristic predicts 0.1.
    Brier for logistic ≈ 0.01, heuristic ≈ 0.81. Logistic should PROMOTE."""
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(60)]
    candidate = _ranker_that_always(0.9)

    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.1

    report = evaluate_promotion(
        candidate=candidate,
        baseline=_AlwaysHeuristic(),
        holdout_rows=rows,
        min_resolved_per_degree=10,
        n_bootstrap=200,
        random_state=42,
    )
    assert report.verdict == PromotionVerdict.PROMOTE


def test_hold_when_logistic_strictly_worse() -> None:
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(60)]
    candidate = _ranker_that_always(0.1)

    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.9

    report = evaluate_promotion(
        candidate=candidate,
        baseline=_AlwaysHeuristic(),
        holdout_rows=rows,
        min_resolved_per_degree=10,
        n_bootstrap=200,
        random_state=42,
    )
    assert report.verdict == PromotionVerdict.HOLD


def test_insufficient_data_when_below_per_degree_threshold() -> None:
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(5)]
    candidate = _ranker_that_always(0.5)

    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.5

    report = evaluate_promotion(
        candidate=candidate,
        baseline=_AlwaysHeuristic(),
        holdout_rows=rows,
        min_resolved_per_degree=30,
        n_bootstrap=200,
        random_state=42,
    )
    assert report.verdict == PromotionVerdict.INSUFFICIENT_DATA
