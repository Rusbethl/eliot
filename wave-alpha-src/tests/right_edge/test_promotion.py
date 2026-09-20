"""Tests for the bootstrap-CI promotion gate (PROMOTE / HOLD / REJECT).

The gate uses *paired* bootstrap: the same resample indices are applied to both
the logistic and heuristic prediction lists.  This reduces variance and makes
the comparison more powerful.  Both lists must be length-equal and row-aligned
(same outcome and degree at each position).
"""

from __future__ import annotations

import numpy as np
import pytest

from wave_alpha.right_edge.promotion import GateResult, _paired_bootstrap_brier, evaluate_promotion


# ---------------------------------------------------------------------------
# Helper: build noisy-prediction dataset for HOLD/determinism tests.
# Uses a fixed seed so the fixtures are stable.
# ---------------------------------------------------------------------------
def _noisy_preds(
    *,
    n: int = 200,
    log_center: float = 0.55,
    heu_center: float = 0.53,
    spread: float = 0.15,
    degree: int = 1,
    seed: int = 42,
) -> tuple[list[tuple[float, int, int]], list[tuple[float, int, int]]]:
    """Return (log_preds, heu_preds) with n random predictions and 50/50 outcomes."""
    rng = np.random.default_rng(seed)
    outs = [1] * (n // 2) + [0] * (n // 2)
    log_ps = np.clip(rng.normal(log_center, spread, n), 0.01, 0.99).tolist()
    heu_ps = np.clip(rng.normal(heu_center, spread, n), 0.01, 0.99).tolist()
    log_p = [(float(p), o, degree) for p, o in zip(log_ps, outs, strict=True)]
    heu_p = [(float(p), o, degree) for p, o in zip(heu_ps, outs, strict=True)]
    return log_p, heu_p


def test_promote_when_logistic_clearly_better() -> None:
    """Logistic is calibrated at the true base rate; heuristic predicts 0.5 (miscalibrated).

    Base rate = 60%, logistic predicts 0.6 always (ECE ≈ 0), heuristic predicts 0.5 (ECE = 0.1).
    With 600 samples the bootstrap CIs don't overlap → PROMOTE.
    """
    log_p: list[tuple[float, int, int]] = [(0.6, 1, 1)] * 60 * 10 + [(0.6, 0, 1)] * 40 * 10
    heu_p: list[tuple[float, int, int]] = [(0.5, 1, 1)] * 60 * 10 + [(0.5, 0, 1)] * 40 * 10
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "PROMOTE"


def test_reject_when_logistic_worse() -> None:
    """Heuristic is calibrated at the true base rate; logistic predicts 0.5 (miscalibrated).

    Symmetrically opposite to the PROMOTE case → REJECT.
    """
    log_p: list[tuple[float, int, int]] = [(0.5, 1, 1)] * 60 * 10 + [(0.5, 0, 1)] * 40 * 10
    heu_p: list[tuple[float, int, int]] = [(0.6, 1, 1)] * 60 * 10 + [(0.6, 0, 1)] * 40 * 10
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "REJECT"


def test_hold_when_close() -> None:
    """Both models produce nearly identical predictions → bootstrap CIs overlap
    and calibration improvement is below the 20% threshold → HOLD.

    Use the exact same predictions for both — identical proba means zero
    calibration improvement → _calibration_strictly_better returns False.
    """
    log_p, _ = _noisy_preds(log_center=0.55, heu_center=0.55, seed=42)
    # Make heuristic predictions identical to logistic → same ECE, same Brier.
    heu_p = [(p, o, d) for p, o, d in log_p]
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "HOLD"


def test_reject_when_calibration_worse_even_if_brier_better() -> None:
    """Logistic has clearly better overall Brier (non-overlapping CIs) but its
    per-degree calibration is strictly worse on degree 2.

    The gate must REJECT because the calibration constraint trumps Brier superiority.
    """
    # Degree 1: logistic well-calibrated at 60% base rate; heuristic predicts 0.5
    # → drives overall Brier improvement for logistic
    log_d1: list[tuple[float, int, int]] = [(0.6, 1, 1)] * 60 * 10 + [(0.6, 0, 1)] * 40 * 10
    heu_d1: list[tuple[float, int, int]] = [(0.5, 1, 1)] * 60 * 10 + [(0.5, 0, 1)] * 40 * 10

    # Degree 2: logistic badly miscalibrated (predicts 0.9 for 50/50 outcomes),
    # heuristic is exactly calibrated (predicts 0.5 for 50/50 outcomes).
    # calibration_error(logistic_d2) >> calibration_error(heuristic_d2)
    log_d2: list[tuple[float, int, int]] = [(0.9, 1, 2)] * 50 + [(0.9, 0, 2)] * 50
    heu_d2: list[tuple[float, int, int]] = [(0.5, 1, 2)] * 50 + [(0.5, 0, 2)] * 50

    log_p = log_d1 + log_d2
    heu_p = heu_d1 + heu_d2

    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)

    # Sanity-check: calibration on degree 2 is indeed worse for logistic
    assert r.per_degree_calibration[2]["logistic"] > r.per_degree_calibration[2]["heuristic"]
    # The gate must REJECT despite the logistic's Brier advantage
    assert r.verdict == "REJECT"


def test_gate_result_fields_populated() -> None:
    """GateResult carries all expected fields regardless of verdict."""
    log_p, heu_p = _noisy_preds(log_center=0.55, heu_center=0.53, seed=42)
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)

    assert isinstance(r, GateResult)
    assert len(r.logistic_brier_ci) == 3
    assert len(r.heuristic_brier_ci) == 3
    lo, mid, hi = r.logistic_brier_ci
    assert lo <= mid <= hi
    lo, mid, hi = r.heuristic_brier_ci
    assert lo <= mid <= hi
    assert 1 in r.per_degree_calibration
    assert "logistic" in r.per_degree_calibration[1]
    assert "heuristic" in r.per_degree_calibration[1]


def test_determinism() -> None:
    """Same inputs + same seed must produce identical results bit-for-bit."""
    log_p, heu_p = _noisy_preds(log_center=0.55, heu_center=0.53, seed=42)
    r1 = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p, seed=7)
    r2 = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p, seed=7)
    assert r1 == r2

    # Different seeds use different resample indices → at least one CI must differ
    r3 = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p, seed=99)
    assert (
        r3.logistic_brier_ci != r1.logistic_brier_ci
        or r3.heuristic_brier_ci != r1.heuristic_brier_ci
    ), "Expected at least one CI to differ when seed changes"


def test_paired_bootstrap_same_indices() -> None:
    """Paired bootstrap must use the same resample indices for both models.

    Consequence: if logistic == heuristic predictions, both CIs must be identical.
    """
    # Build identical prediction lists
    pairs: list[tuple[float, int]] = [(0.6, 1)] * 50 + [(0.4, 0)] * 50
    log_ci, heu_ci = _paired_bootstrap_brier(pairs, pairs, seed=0)
    # Same data, same indices → identical CIs
    assert log_ci == heu_ci


def test_evaluate_promotion_rejects_misaligned_preds() -> None:
    """evaluate_promotion must fail loudly when outcome/degree rows are misaligned."""
    log_p: list[tuple[float, int, int]] = [(0.6, 1, 1), (0.4, 0, 1)]
    # Heuristic has swapped outcomes
    heu_p: list[tuple[float, int, int]] = [(0.5, 0, 1), (0.5, 1, 1)]
    with pytest.raises(AssertionError, match="misaligned"):
        evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)


def test_evaluate_promotion_rejects_different_lengths() -> None:
    """evaluate_promotion must fail if lists are different lengths."""
    log_p: list[tuple[float, int, int]] = [(0.6, 1, 1), (0.4, 0, 1)]
    heu_p: list[tuple[float, int, int]] = [(0.5, 1, 1)]
    with pytest.raises(AssertionError, match="same length"):
        evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)


def test_paired_bootstrap_empty_returns_zero_ci() -> None:
    """Empty prediction lists return (0,0,0) for both CIs."""
    log_ci, heu_ci = _paired_bootstrap_brier([], [], seed=0)
    assert log_ci == (0.0, 0.0, 0.0)
    assert heu_ci == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Phase 2 — PROMOTE_CALIBRATION verdict tests
# ---------------------------------------------------------------------------


def _preds_overlapping_brier_better_cal(
    *,
    degrees: list[int] | None = None,
    n_per_degree: int = 100,
) -> tuple[list[tuple[float, int, int]], list[tuple[float, int, int]]]:
    """Build aligned prediction sets where:

    - Brier CIs overlap (both models score similarly on overall Brier).
    - Logistic calibration is dramatically better than heuristic on every degree
      (>20% relative improvement on every degree).

    Strategy: Both logistic and heuristic predict the same probability (same Brier),
    but the logistic's proba is better calibrated to the actual base rate.
    We construct this so the REJECT check doesn't fire (no degree is worse for logistic)
    and the Brier is overlapping (so PROMOTE doesn't fire either).

    We achieve overlapping Brier by making both models predict close values for
    outcomes (same Brier score), but calibration ECE differs because:
    - Logistic: predicts 0.65 for 60/40 outcomes (ECE = |0.65 - 0.6| = 0.05)
    - Heuristic: predicts 0.9 for 60/40 outcomes (ECE = |0.9 - 0.6| = 0.3)
    BUT to make Brier overlap we need same predicted proba (same Brier).
    Instead, we use: both predict approximately the same for Brier purposes
    but calibration error differs.

    Actually the simplest approach: small n so CIs are wide and overlapping,
    but calibration improvement is large (>20%) on every degree.
    """
    if degrees is None:
        degrees = [0, 1, 2]

    log_preds: list[tuple[float, int, int]] = []
    heu_preds: list[tuple[float, int, int]] = []

    for d in degrees:
        # 60/40 true base rate.
        # Logistic: predicts 0.6 (well-calibrated, ECE ≈ 0).
        # Heuristic: predicts 0.9 (miscalibrated, ECE ≈ 0.3).
        # With small n, Brier CIs overlap since both have similar Brier scores.
        # (Brier(logistic) = (0.4)^2 * 0.6 + (0.6)^2 * 0.4 = 0.096 + 0.144 = 0.240)
        # (Brier(heuristic) = (0.1)^2 * 0.6 + (0.9)^2 * 0.4 = 0.006 + 0.324 = 0.330)
        # Wait — these Brier scores are NOT similar. Logistic wins clearly.
        # For overlapping Brier: both should predict similarly.
        # Use: logistic predicts 0.62, heuristic predicts 0.9, with small n → wide CIs.
        # With 20 samples per degree total (10 per degree), bootstrap CI is very wide.
        n_pos = n_per_degree // 2  # 50% base rate for maximum Brier similarity
        n_neg = n_per_degree - n_pos
        # Heuristic: badly miscalibrated (predicts 0.85, but base rate is 0.5)
        # ECE ≈ |0.85 - 0.5| = 0.35
        # Logistic: better calibrated (predicts 0.6, base rate 0.5)
        # ECE ≈ |0.6 - 0.5| = 0.10
        # Improvement: (0.35 - 0.10) / 0.35 ≈ 71% > 20% threshold
        # Brier(logistic) = (0.4)^2*0.5 + (0.6)^2*0.5 = 0.08 + 0.18 = 0.26
        # Brier(heuristic) = (0.15)^2*0.5 + (0.85)^2*0.5 = 0.01125 + 0.36125 = 0.37
        # These are different — logistic better on Brier too. Need same-Brier.
        # To truly get overlapping Brier: both predict 0.5 but with different ECE
        # → both have same Brier (optimal for 50/50 data), but ECE differs.
        # logistic: 0.5 (ECE = 0)
        # heuristic: 0.8 (ECE = 0.3)
        # BUT: Brier(0.5) = (0.5)^2 = 0.25 for every sample regardless of outcome.
        # Brier(0.8, 1) = (0.2)^2 = 0.04; Brier(0.8, 0) = (0.8)^2 = 0.64
        # So heuristic Brier = 0.5*0.04 + 0.5*0.64 = 0.34, still worse than logistic.
        # The only way to get Brier to overlap is use small n so CIs are wide.
        log_preds += [(0.5, 1, d)] * n_pos + [(0.5, 0, d)] * n_neg
        heu_preds += [(0.8, 1, d)] * n_pos + [(0.8, 0, d)] * n_neg

    return log_preds, heu_preds


def test_promote_calibration_when_brier_overlaps_and_calibration_clearly_better() -> None:
    """Brier CIs overlap but logistic calibration is dramatically better on every degree
    (>20% relative improvement) → verdict should be PROMOTE_CALIBRATION.

    We use small n (10 per degree) to produce wide bootstrap CIs that overlap,
    combined with a large calibration gap (logistic ECE=0, heuristic ECE=0.3).
    The calibration improvement ratio is (0.3-0)/0.3 = 100% >> 20% threshold.
    """
    degrees = [0, 1, 2]
    n = 10  # small n → wide bootstrap CIs → overlapping
    log_preds: list[tuple[float, int, int]] = []
    heu_preds: list[tuple[float, int, int]] = []
    for d in degrees:
        # 50/50 outcomes, logistic perfectly calibrated, heuristic badly miscalibrated.
        log_preds += [(0.5, 1, d)] * (n // 2) + [(0.5, 0, d)] * (n // 2)
        heu_preds += [(0.8, 1, d)] * (n // 2) + [(0.8, 0, d)] * (n // 2)

    r = evaluate_promotion(logistic_preds=log_preds, heuristic_preds=heu_preds)

    # Verify calibration is better on every degree (no REJECT trigger)
    for d in degrees:
        log_ece = r.per_degree_calibration[d]["logistic"]
        heu_ece = r.per_degree_calibration[d]["heuristic"]
        assert log_ece < heu_ece, (
            f"degree {d}: logistic ECE {log_ece} should be < heuristic {heu_ece}"
        )
        improvement = (heu_ece - log_ece) / heu_ece
        assert improvement >= 0.20, f"degree {d}: improvement {improvement:.2%} should be >= 20%"

    assert r.verdict == "PROMOTE_CALIBRATION"


def test_hold_when_calibration_better_but_below_threshold() -> None:
    """Logistic calibration is only ~10% better (below 20% threshold) → HOLD."""
    # Both logistic and heuristic predict nearly the same miscalibrated value.
    # logistic slightly closer to base rate → < 20% relative improvement.
    n = 300
    base_rate = 0.6
    n_pos = int(n * base_rate)
    n_neg = n - n_pos
    # Heuristic ECE: |0.72 - 0.6| = 0.12
    # Logistic ECE:  |0.708 - 0.6| = 0.108
    # Improvement ratio: (0.12 - 0.108) / 0.12 = 0.10 → below 20% threshold
    log_p: list[tuple[float, int, int]] = [(0.708, 1, 1)] * n_pos + [(0.708, 0, 1)] * n_neg
    heu_p: list[tuple[float, int, int]] = [(0.72, 1, 1)] * n_pos + [(0.72, 0, 1)] * n_neg
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "HOLD"


def test_promote_strong_path_still_works() -> None:
    """Strict Brier-better AND calibration ≤ → PROMOTE (not PROMOTE_CALIBRATION)."""
    log_p: list[tuple[float, int, int]] = [(0.6, 1, 1)] * 600 + [(0.6, 0, 1)] * 400
    heu_p: list[tuple[float, int, int]] = [(0.5, 1, 1)] * 600 + [(0.5, 0, 1)] * 400
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "PROMOTE"
