# src/wave_alpha/backtest/brier.py
from __future__ import annotations

from collections.abc import Sequence


def brier_score(pairs: Sequence[tuple[float, int]]) -> float:
    """Mean squared error between predicted probability and binary outcome.

    `pairs` is a sequence of (predicted_probability, actual_outcome) where
    actual is 0 or 1. Lower is better; range [0, 1].
    """
    if not pairs:
        raise ValueError("brier_score on empty sequence")
    return sum((p - a) ** 2 for p, a in pairs) / len(pairs)


def reliability_bins(
    pairs: Sequence[tuple[float, int]], *, n_bins: int = 10
) -> list[tuple[float, int, float, float]]:
    """Group pairs into n_bins equal-width buckets over [0, 1].

    Returns a list of (bin_lower, count, mean_predicted, mean_actual) sorted by
    bin_lower; empty bins are omitted. The last bin is closed on the right
    (predictions of exactly 1.0 land in the last bin).
    """
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    width = 1.0 / n_bins
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, a in pairs:
        idx = min(int(p / width), n_bins - 1)
        buckets.setdefault(idx, []).append((p, a))
    out: list[tuple[float, int, float, float]] = []
    for idx in sorted(buckets):
        rows = buckets[idx]
        n = len(rows)
        mean_p = sum(p for p, _ in rows) / n
        mean_a = sum(a for _, a in rows) / n
        out.append((idx * width, n, mean_p, mean_a))
    return out


def calibration_error(pairs: Sequence[tuple[float, int]], *, n_bins: int = 10) -> float:
    """Expected calibration error: weighted mean |bin_mean_predicted - bin_mean_actual|.

    Weight is bin sample size. Range [0, 1]; 0 = perfect calibration.
    """
    if not pairs:
        raise ValueError("calibration_error on empty sequence")
    bins = reliability_bins(pairs, n_bins=n_bins)
    total = sum(n for _, n, _, _ in bins)
    return sum(n * abs(mean_p - mean_a) for _, n, mean_p, mean_a in bins) / total
