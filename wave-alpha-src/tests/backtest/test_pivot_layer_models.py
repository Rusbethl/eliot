import pytest

from wave_alpha.backtest.pivot_layer import PivotEvaluation, PivotLayerResult


def test_pivot_evaluation_construction() -> None:
    ev = PivotEvaluation(
        ticker="AAPL",
        as_of_initial="2024-01-05",
        as_of_final="2024-01-15",
        degree=2,
        outcome="confirmed",
        predicted_proba=0.7,
    )
    assert ev.actual == 1


def test_pivot_evaluation_invalidated_actual_is_zero() -> None:
    ev = PivotEvaluation(
        ticker="AAPL",
        as_of_initial="2024-01-05",
        as_of_final="2024-01-15",
        degree=2,
        outcome="invalidated",
        predicted_proba=0.7,
    )
    assert ev.actual == 0


def test_pivot_layer_result_per_degree_confirmation_rates() -> None:
    evs = [
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 0, "confirmed", 0.6),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 0, "invalidated", 0.6),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "confirmed", 0.5),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "confirmed", 0.5),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "invalidated", 0.5),
    ]
    res = PivotLayerResult(evaluations=evs)
    rates = res.confirmation_rate_per_degree()
    assert rates[0] == 0.5
    assert rates[1] == pytest.approx(2 / 3)


def test_pivot_layer_result_brier_and_calibration() -> None:
    evs = [
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "confirmed", 0.9),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "confirmed", 0.8),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "invalidated", 0.2),
    ]
    res = PivotLayerResult(evaluations=evs)
    # Brier = ((.9-1)^2 + (.8-1)^2 + (.2-0)^2) / 3 = (.01 + .04 + .04) / 3 ≈ 0.03
    assert res.brier_score() == pytest.approx(0.03, abs=0.001)
    # Calibration shouldn't crash
    res.calibration_error()
