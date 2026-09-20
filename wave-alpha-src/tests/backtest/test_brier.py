# tests/backtest/test_brier.py
import pytest

from wave_alpha.backtest.brier import brier_score, calibration_error, reliability_bins


def test_brier_score_perfect_predictions_is_zero() -> None:
    assert brier_score([(1.0, 1), (0.0, 0), (1.0, 1)]) == 0.0


def test_brier_score_worst_predictions_is_one() -> None:
    assert brier_score([(1.0, 0), (0.0, 1)]) == 1.0


def test_brier_score_uncertain_predictions() -> None:
    # Always predict 0.5; outcomes 50/50
    pairs = [(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]
    assert brier_score(pairs) == 0.25


def test_brier_score_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        brier_score([])


def test_reliability_bins_groups_by_predicted_probability() -> None:
    # 10-bin: [0.0, 0.1), [0.1, 0.2), ..., [0.9, 1.0]
    pairs = [
        (0.05, 0),
        (0.05, 1),  # bin 0: 1/2 = 0.5 actual, 0.05 predicted
        (0.55, 1),
        (0.55, 1),
        (0.55, 0),  # bin 5: 2/3 = 0.667 actual, 0.55 predicted
        (0.95, 1),  # bin 9: 1/1 = 1.0 actual, 0.95 predicted
    ]
    bins = reliability_bins(pairs, n_bins=10)
    # Each entry is (bin_lower, n, mean_predicted, mean_actual)
    assert (0.0, 2, 0.05, 0.5) in bins
    assert (0.5, 3, pytest.approx(0.55), pytest.approx(2 / 3)) in bins
    assert (0.9, 1, 0.95, 1.0) in bins


def test_calibration_error_perfect_calibration_is_zero() -> None:
    # Each bin's mean_actual matches mean_predicted exactly
    pairs = [(0.0, 0), (0.5, 0), (0.5, 1), (1.0, 1)]
    assert calibration_error(pairs, n_bins=10) == pytest.approx(0.0)


def test_calibration_error_weighted_by_bin_size() -> None:
    # 100 predictions of 0.9 with actual 0.5 → big mass x 0.4 gap = 0.4
    # 1 prediction of 0.1 with actual 0.5 → small mass x 0.4 gap = ~0
    pairs = [(0.9, i % 2) for i in range(100)] + [(0.1, 1)]
    err = calibration_error(pairs, n_bins=10)
    assert 0.35 < err < 0.41
