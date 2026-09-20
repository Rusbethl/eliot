# src/wave_alpha/right_edge/promotion.py
"""Bootstrap-CI promotion gate for PROMOTE / PROMOTE_CALIBRATION / HOLD / REJECT verdicts.

Compares a logistic model against the heuristic baseline using:
1. Paired bootstrap 95% CI on the overall Brier score (1000 resamples).
   Both models are resampled with the same indices — this is a paired bootstrap,
   which reduces variance and makes the comparison more powerful.
2. Per-degree expected calibration error.

Verdict rules (evaluated in this order):
- REJECT: logistic Brier lower-CI > heuristic Brier upper-CI (logistic strictly
  worse on overall Brier), OR any per-degree calibration error has logistic > heuristic.
- PROMOTE: logistic Brier upper-CI < heuristic Brier lower-CI (logistic strictly
  better on overall Brier) AND every per-degree calibration error has logistic ≤ heuristic.
- PROMOTE_CALIBRATION: Brier CIs overlap (no regression by REJECT check), and every
  degree's calibration is strictly better by ≥ _CALIBRATION_PROMOTE_RATIO relative.
- HOLD: anything else (overlapping CIs or calibration improvement below threshold).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from wave_alpha.backtest.brier import brier_score, calibration_error

Verdict = Literal["PROMOTE", "PROMOTE_CALIBRATION", "HOLD", "REJECT"]

# Threshold: every degree's logistic ECE must be at least 20% relatively
# better than heuristic's. Tunable; chosen as the smallest gap that's
# clearly above bootstrap noise on per-degree calibration and gives meaningful
# user-facing improvement (i.e., a 70% prediction is closer to truly 70%).
_CALIBRATION_PROMOTE_RATIO: float = 0.20  # relative improvement required


def _calibration_strictly_better(per_degree: dict[int, dict[str, float]]) -> bool:
    """Return True iff every degree's logistic ECE is ≥ _CALIBRATION_PROMOTE_RATIO
    better (relatively) than the heuristic's ECE.

    If the heuristic ECE is 0 for any degree the improvement ratio is undefined
    and we return False (can't improve on perfection).
    """
    for _d, m in per_degree.items():
        log_ece = m["logistic"]
        heu_ece = m["heuristic"]
        if heu_ece == 0:
            return False  # can't beat zero
        improvement = (heu_ece - log_ece) / heu_ece
        if improvement < _CALIBRATION_PROMOTE_RATIO:
            return False
    return True


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    logistic_brier_ci: tuple[float, float, float]  # (lo, mid, hi)
    heuristic_brier_ci: tuple[float, float, float]  # (lo, mid, hi)
    per_degree_calibration: dict[
        int, dict[str, float]
    ]  # {degree: {"logistic": .., "heuristic": ..}}


def _paired_bootstrap_brier(
    log_pairs: list[tuple[float, int]],
    heu_pairs: list[tuple[float, int]],
    *,
    n_resamples: int = 1000,
    seed: int = 0,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Paired bootstrap: same resample indices applied to both prediction lists.

    Returns (logistic_ci, heuristic_ci) as (lo, mid, hi) at 95%.
    Using paired resampling reduces variance in the comparison and makes the
    verdict more powerful and accurate than independent bootstraps.
    """
    assert len(log_pairs) == len(heu_pairs), "paired bootstrap requires same n"
    if not log_pairs:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    rng = np.random.default_rng(seed)
    log_arr = np.array(log_pairs, dtype=np.float64)
    heu_arr = np.array(heu_pairs, dtype=np.float64)
    n = len(log_arr)

    log_samples: list[float] = []
    heu_samples: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        log_samples.append(brier_score([(float(row[0]), int(row[1])) for row in log_arr[idx]]))
        heu_samples.append(brier_score([(float(row[0]), int(row[1])) for row in heu_arr[idx]]))

    log_sorted = np.sort(np.array(log_samples))
    heu_sorted = np.sort(np.array(heu_samples))
    # 2.5th percentile → index 25 (0-indexed); 97.5th → index 974 (int(0.975*1000)-1)
    lo_i = int(0.025 * n_resamples)
    hi_i = int(0.975 * n_resamples) - 1
    return (
        (float(log_sorted[lo_i]), float(np.median(log_sorted)), float(log_sorted[hi_i])),
        (float(heu_sorted[lo_i]), float(np.median(heu_sorted)), float(heu_sorted[hi_i])),
    )


def evaluate_promotion(
    *,
    logistic_preds: list[tuple[float, int, int]],  # (proba, outcome, degree)
    heuristic_preds: list[tuple[float, int, int]],  # same shape, same prediction set
    seed: int = 0,
) -> GateResult:
    """Evaluate whether to PROMOTE, HOLD, or REJECT the logistic model.

    Uses paired bootstrap: the same resample indices are applied to both
    prediction lists, reducing variance and making the verdict more powerful.
    Both lists must be aligned row-by-row (same prediction set, same row order).
    """
    assert len(logistic_preds) == len(heuristic_preds), (
        f"logistic_preds and heuristic_preds must have the same length; "
        f"got {len(logistic_preds)} vs {len(heuristic_preds)}"
    )
    # Verify row-by-row alignment: (outcome, degree) must match for every row.
    for i, (lp, hp) in enumerate(zip(logistic_preds, heuristic_preds, strict=True)):
        _l_proba, l_outcome, l_degree = lp
        _h_proba, h_outcome, h_degree = hp
        assert l_outcome == h_outcome and l_degree == h_degree, (
            f"Row {i}: misaligned prediction sets — "
            f"logistic=(outcome={l_outcome}, degree={l_degree}), "
            f"heuristic=(outcome={h_outcome}, degree={h_degree})"
        )

    log_pairs = [(p, o) for p, o, _ in logistic_preds]
    heu_pairs = [(p, o) for p, o, _ in heuristic_preds]

    log_ci, heu_ci = _paired_bootstrap_brier(log_pairs, heu_pairs, seed=seed)

    # Compute per-degree calibration errors
    degrees = {dg for _, _, dg in logistic_preds}
    per_degree: dict[int, dict[str, float]] = {}
    for d in sorted(degrees):
        log_d = [(p, o) for p, o, dg in logistic_preds if dg == d]
        heu_d = [(p, o) for p, o, dg in heuristic_preds if dg == d]
        per_degree[d] = {
            "logistic": calibration_error(log_d) if log_d else 0.0,
            "heuristic": calibration_error(heu_d) if heu_d else 0.0,
        }

    log_lo, _, log_hi = log_ci
    heu_lo, _, heu_hi = heu_ci

    # REJECT first: logistic Brier is strictly worse (its lower-CI > heuristic upper-CI).
    if log_lo > heu_hi:
        return GateResult("REJECT", log_ci, heu_ci, per_degree)

    # REJECT if any per-degree calibration error is worse for logistic.
    for _d, metrics in per_degree.items():
        if metrics["logistic"] > metrics["heuristic"]:
            return GateResult("REJECT", log_ci, heu_ci, per_degree)

    # Strongest PROMOTE: Brier strictly better (logistic upper-CI < heuristic lower-CI)
    # AND every per-degree calibration error is ≤ heuristic.
    if log_hi < heu_lo:
        return GateResult("PROMOTE", log_ci, heu_ci, per_degree)

    # Calibration-only PROMOTE: Brier overlaps (no regression — REJECT check passed above),
    # and every degree's calibration is strictly better by ≥ _CALIBRATION_PROMOTE_RATIO.
    if _calibration_strictly_better(per_degree):
        return GateResult("PROMOTE_CALIBRATION", log_ci, heu_ci, per_degree)

    # Everything else is HOLD.
    return GateResult("HOLD", log_ci, heu_ci, per_degree)
