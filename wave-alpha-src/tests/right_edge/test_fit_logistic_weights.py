from datetime import date

import numpy as np
import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.training import (
    TrainingRow,
    fit_logistic,
    fit_platt,
    predict_logistic,
)


def _row(label: int, atr: float = 1.0) -> TrainingRow:
    return TrainingRow(
        ticker="T",
        as_of=date(2025, 1, 1),
        degree=1,
        features=RightEdgeFeatures(
            retracement_progress=0.5,
            bars_since_candidate=5,
            volume_decline_pct=0.0,
            higher_degree_zone_match=0.0,
            atr_regime=atr,
        ),
        outcome=label,
    )


def test_fit_logistic_unweighted_unchanged_by_unit_weights() -> None:
    # Passing all 1.0 weights must produce identical params to passing no weights.
    rows = [_row(0, 0.5), _row(1, 1.5)] * 50
    p_unweighted = fit_logistic(rows, seed=42)
    p_weighted = fit_logistic(rows, seed=42, sample_weights=np.ones(len(rows), dtype=np.float64))
    assert p_unweighted.weights == pytest.approx(p_weighted.weights, abs=1e-9)
    assert p_unweighted.intercept == pytest.approx(p_weighted.intercept, abs=1e-9)


def test_fit_logistic_high_weight_row_pulls_decision_boundary() -> None:
    # 1 positive at atr=1.5 with weight=1000, 100 negatives at atr=0.5.
    # Heavy positive must shift the boundary so atr=1.5 is predicted as positive.
    rows = [_row(0, 0.5)] * 100 + [_row(1, 1.5)]
    weights = np.array([1.0] * 100 + [1000.0])
    p = fit_logistic(rows, seed=42, sample_weights=weights)
    pos_pred = predict_logistic(
        p,
        RightEdgeFeatures(
            retracement_progress=0.5,
            bars_since_candidate=5,
            volume_decline_pct=0.0,
            higher_degree_zone_match=0.0,
            atr_regime=1.5,
        ),
    )
    assert pos_pred > 0.5


def test_fit_platt_unweighted_unchanged_by_unit_weights() -> None:
    preds = [(0.1, 0), (0.9, 1), (0.4, 0), (0.6, 1)] * 30
    p_a = fit_platt(preds)
    p_b = fit_platt(preds, sample_weights=np.ones(len(preds), dtype=np.float64))
    assert p_a.a == pytest.approx(p_b.a, abs=1e-9)
    assert p_a.b == pytest.approx(p_b.b, abs=1e-9)


def test_fit_logistic_rejects_wrong_shape_weights() -> None:
    rows = [_row(0), _row(1)] * 5
    with pytest.raises(ValueError, match="sample_weights"):
        fit_logistic(rows, seed=42, sample_weights=np.ones(3))


def test_fit_platt_rejects_wrong_shape_weights() -> None:
    preds = [(0.1, 0), (0.9, 1)] * 5
    with pytest.raises(ValueError, match="sample_weights"):
        fit_platt(preds, sample_weights=np.ones(3))
