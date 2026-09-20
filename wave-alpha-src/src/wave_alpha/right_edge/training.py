"""Right-edge logistic training pipeline.

Builds TrainingRows from point-in-time bar snapshots, fits a hand-coded
batch-gradient-descent logistic, fits per-degree Platt scalers, and runs an
expanding walk-forward loop to produce calibrated OOS predictions.

No runtime deps beyond NumPy (already a transitive dep).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import date as _date_type
from pathlib import Path

import numpy as np

from wave_alpha.right_edge.features import RightEdgeFeatures

# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    outcome: int  # 1 = confirmed, 0 = invalidated


@dataclass(frozen=True)
class Fold:
    train_until: date  # exclusive
    test_start: date  # inclusive
    test_end: date  # exclusive


# ---------------------------------------------------------------------------
# Fold generation
# ---------------------------------------------------------------------------


def quarter_folds(start: date, end: date, *, min_train_quarters: int = 4) -> list[Fold]:
    """Generate expanding-window quarter folds from `start` to `end`.

    Skips the first `min_train_quarters` quarters (used as warmup-only training
    data). Each fold's test window is one calendar quarter.
    """
    boundaries: list[date] = [start]
    cur = start
    while cur < end:
        nxt = _next_quarter_start(cur)
        if nxt >= end:
            boundaries.append(end)
            break
        boundaries.append(nxt)
        cur = nxt

    folds: list[Fold] = []
    for i in range(min_train_quarters, len(boundaries) - 1):
        folds.append(
            Fold(
                train_until=boundaries[i],
                test_start=boundaries[i],
                test_end=boundaries[i + 1],
            )
        )
    return folds


def _next_quarter_start(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    if q == 4:
        return date(d.year + 1, 1, 1)
    return date(d.year, q * 3 + 1, 1)


def quarter_start(d: date) -> date:
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


# ---------------------------------------------------------------------------
# Logistic + Platt fitting (NumPy, hand-coded batch gradient descent)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogisticParams:
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float


@dataclass(frozen=True)
class PlattParams:
    a: float  # slope
    b: float  # intercept


LEGACY_FEATURE_ORDER: tuple[str, ...] = (
    "retracement_progress",
    "bars_since_candidate",
    "volume_decline_pct",
    "higher_degree_zone_match",
    "atr_regime",
)

FIB_FEATURE_ORDER: tuple[str, ...] = (
    "fib_w2_w1_retrace",
    "fib_w3_w1_extension",
    "fib_w4_w3_retrace",
    "fib_w5_w1_ratio",
    "fib_w5_thrust_extension",
    "fib_b_a_retrace",
    "fib_c_a_ratio",
    "fib_w2_w1_retrace_present",
    "fib_w3_w1_extension_present",
    "fib_w4_w3_retrace_present",
    "fib_w5_w1_ratio_present",
    "fib_w5_thrust_extension_present",
    "fib_b_a_retrace_present",
    "fib_c_a_ratio_present",
)


def feature_order(*, with_fib: bool) -> tuple[str, ...]:
    """Return the trainer/serve feature_order tuple.

    With ``with_fib=False`` returns the legacy 5-field order; with
    ``with_fib=True`` appends 14 fib feature names. The returned tuple
    matches the ``feature_order`` written into the trained artifact and the
    same tuple consumed by ``LogisticConfirmationModel._vector``.
    """
    return LEGACY_FEATURE_ORDER + FIB_FEATURE_ORDER if with_fib else LEGACY_FEATURE_ORDER


# Internal alias for backward compatibility with tests/callers that imported
# the old name. Prefer ``LEGACY_FEATURE_ORDER`` in new code.
_FEATURE_ORDER = LEGACY_FEATURE_ORDER

_BARS_SCALE_FIELD = "bars_since_candidate"


def _row_to_vector(row: TrainingRow, *, with_fib: bool = False) -> np.ndarray:
    """Convert a ``TrainingRow`` to a 1D numpy vector.

    Mirrors ``LogisticConfirmationModel._vector`` exactly — same field order,
    same ``bars_since_candidate / 20.0`` rescaling. Train/serve skew here
    silently corrupts every prediction; if you change one, change both, and
    the cross-test ``test_row_to_vector_*_matches_logistic_vector`` proves
    they agree.
    """
    f = row.features
    names = feature_order(with_fib=with_fib)
    out: list[float] = []
    for name in names:
        value = getattr(f, name)
        if name == _BARS_SCALE_FIELD:
            value = float(value) / 20.0
        out.append(float(value))
    return np.array(out, dtype=np.float64)


def fit_logistic(
    rows: list[TrainingRow],
    *,
    lr: float = 0.05,
    n_iters: int = 2000,
    seed: int = 42,
    with_fib: bool = False,
    sample_weights: np.ndarray | None = None,
) -> LogisticParams:
    """Batch gradient descent on binary cross-entropy. Deterministic given seed.

    If `sample_weights` is provided, the gradient is the weighted mean —
    rows with higher weights pull the boundary toward their label. Defaults
    to uniform weights (behavior identical to the no-weights path).
    """
    if not rows:
        raise ValueError("cannot fit on empty rows")
    rng = np.random.default_rng(seed)
    # N806: X is an established ML convention for the design matrix; kept as-is.
    x_mat = np.stack([_row_to_vector(r, with_fib=with_fib) for r in rows])
    y = np.array([r.outcome for r in rows], dtype=np.float64)
    if sample_weights is None:
        sw = np.ones(len(rows), dtype=np.float64)
    else:
        sw = np.asarray(sample_weights, dtype=np.float64)
        if sw.shape != (len(rows),):
            raise ValueError(f"sample_weights shape {sw.shape} != ({len(rows)},)")
    sw_sum = float(sw.sum())
    w = rng.normal(0, 0.01, size=x_mat.shape[1])
    b = 0.0
    for _ in range(n_iters):
        z = x_mat @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        err = (p - y) * sw
        grad_w = x_mat.T @ err / sw_sum
        grad_b = float(err.sum() / sw_sum)
        w -= lr * grad_w
        b -= lr * grad_b
    return LogisticParams(
        feature_order=feature_order(with_fib=with_fib),
        weights=tuple(float(v) for v in w),
        intercept=float(b),
    )


def fit_platt(
    predictions: list[tuple[float, int]],
    *,
    sample_weights: np.ndarray | None = None,
) -> PlattParams:
    """Platt scaling: 1-feature logistic on (raw_proba, label) pairs.

    Honors `sample_weights` symmetrically with `fit_logistic`.
    """
    if not predictions:
        return PlattParams(a=1.0, b=0.0)  # identity when no data
    x_col = np.array([[p] for p, _ in predictions], dtype=np.float64)
    y = np.array([o for _, o in predictions], dtype=np.float64)
    if sample_weights is None:
        sw = np.ones(len(predictions), dtype=np.float64)
    else:
        sw = np.asarray(sample_weights, dtype=np.float64)
        if sw.shape != (len(predictions),):
            raise ValueError(f"sample_weights shape {sw.shape} != ({len(predictions)},)")
    sw_sum = float(sw.sum())
    a = 0.0
    b = 0.0
    for _ in range(2000):
        z = x_col[:, 0] * a + b
        ph = 1.0 / (1.0 + np.exp(-z))
        err = (ph - y) * sw
        grad_a = float((err * x_col[:, 0]).sum() / sw_sum)
        grad_b = float(err.sum() / sw_sum)
        a -= 0.05 * grad_a
        b -= 0.05 * grad_b
    return PlattParams(a=float(a), b=float(b))


def predict_logistic(params: LogisticParams, features: RightEdgeFeatures) -> float:
    """Apply a LogisticParams to a RightEdgeFeatures and return a raw probability.

    Uses the params' own feature_order so a 19-field artifact reads 19 fields
    and a 5-field artifact reads 5 — same dynamic dispatch as
    LogisticConfirmationModel._vector at serve time.
    """
    values: list[float] = []
    for name in params.feature_order:
        value = getattr(features, name)
        if name == _BARS_SCALE_FIELD:
            value = float(value) / 20.0
        values.append(float(value))
    v = np.array(values, dtype=np.float64)
    z = float(np.dot(np.array(params.weights, dtype=np.float64), v) + params.intercept)
    return 1.0 / (1.0 + float(np.exp(-z)))


def apply_platt(params: PlattParams, raw_proba: float) -> float:
    """Apply Platt calibration to a raw logistic probability."""
    z = params.a * raw_proba + params.b
    return 1.0 / (1.0 + float(np.exp(-z)))


# ---------------------------------------------------------------------------
# Walk-forward orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibratedPrediction:
    ticker: str
    as_of: date
    degree: int
    raw_proba: float
    calibrated_proba: float
    outcome: int
    features: RightEdgeFeatures  # kept so CLI gate can score heuristic baseline


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[Fold]
    final_logistic: LogisticParams
    platt_per_degree: dict[int, PlattParams]
    oos_predictions: list[CalibratedPrediction]
    n_train_rows: int
    n_oos_rows: int


def walk_forward(
    rows_by_quarter: dict[date, list[TrainingRow]],
    *,
    folds: list[Fold],
    seed: int = 42,
    with_fib: bool = False,
) -> WalkForwardResult:
    """Expanding-window walk-forward training with per-degree Platt calibration.

    `rows_by_quarter[q]` maps a quarter-start date to the TrainingRows whose
    `as_of` falls within that quarter.

    Platt calibration is trained on prior-fold data only (no leakage):
    - For each fold, Platt scalers are fit on (raw_proba, outcome) pairs from
      ALL PRIOR folds.
    - The current fold's test rows are calibrated with those prior-fold scalers.
    - Only AFTER emitting the fold's OOS predictions are the fold's own pairs
      added to the accumulator, making them available for FUTURE folds.
    - The first fold always uses identity Platt (calibrated_proba == raw_proba).
    - The FINAL Platt (stored in platt_per_degree) is refit on ALL accumulated
      data — this is used by the production model and is NOT being self-evaluated.
    """
    all_oos: list[CalibratedPrediction] = []
    # Accumulated (raw_proba, outcome) pairs per degree, growing across folds.
    # Updated AFTER each fold so that Platt is always fit on PRIOR-fold data only
    # (no in-sample leakage from the current fold).
    accumulated_platt_inputs: dict[int, list[tuple[float, int]]] = {}

    for fold in folds:
        # Training rows: every quarter starting strictly before fold.train_until.
        train_rows: list[TrainingRow] = [
            r for q, rs in rows_by_quarter.items() if q < fold.train_until for r in rs
        ]
        if not train_rows:
            continue
        params = fit_logistic(train_rows, seed=seed, with_fib=with_fib)

        # Test rows: quarters falling within [test_start, test_end).
        test_rows: list[TrainingRow] = [
            r
            for q, rs in rows_by_quarter.items()
            if fold.test_start <= q < fold.test_end
            for r in rs
        ]

        # Fit Platt scalers using ONLY data accumulated through PRIOR folds.
        # First fold gets identity Platt (no prior calibration data) → raw == calibrated.
        platt = {d: fit_platt(pairs) for d, pairs in accumulated_platt_inputs.items() if pairs}

        # Compute raw probas for this fold's test rows; apply Platt; defer accumulator update.
        test_raw: list[tuple[TrainingRow, float]] = []
        for r in test_rows:
            raw = predict_logistic(params, r.features)
            test_raw.append((r, raw))

        # Emit calibrated predictions using the PRIOR-fold Platt scalers.
        for r, raw in test_raw:
            scaler = platt.get(r.degree)
            cal = apply_platt(scaler, raw) if scaler else raw  # identity for first fold
            all_oos.append(
                CalibratedPrediction(
                    ticker=r.ticker,
                    as_of=r.as_of,
                    degree=r.degree,
                    raw_proba=raw,
                    calibrated_proba=cal,
                    outcome=r.outcome,
                    features=r.features,
                )
            )

        # NOW add this fold's pairs to the accumulator for the NEXT fold's Platt fit.
        for r, raw in test_raw:
            accumulated_platt_inputs.setdefault(r.degree, []).append((raw, r.outcome))

    # Final logistic refit on ALL rows for live inference.
    all_rows = [r for rs in rows_by_quarter.values() for r in rs]
    if not all_rows:
        raise ValueError("walk_forward called with no rows across any quarter")
    final_logistic = fit_logistic(all_rows, seed=seed, with_fib=with_fib)
    final_platt = {d: fit_platt(pairs) for d, pairs in accumulated_platt_inputs.items() if pairs}

    return WalkForwardResult(
        folds=folds,
        final_logistic=final_logistic,
        platt_per_degree=final_platt,
        oos_predictions=all_oos,
        n_train_rows=len(all_rows),
        n_oos_rows=len(all_oos),
    )


# ---------------------------------------------------------------------------
# TrainingRow assembly from real OHLCV provider
# ---------------------------------------------------------------------------


def assemble_training_rows(
    *,
    provider: object,  # OHLCVProvider — avoid heavy import at module level
    universe: list[str],
    as_of_pairs: list[tuple[date, date]],  # (initial, final) date pairs
    interval: object = None,  # Interval — resolved to DAILY when None
    lookahead_bars: int = 20,
) -> list[TrainingRow]:
    """Walk universe x (initial, final) pairs; emit TrainingRow per tentative pivot.

    Each tentative pivot in the initial snapshot whose outcome can be determined
    (confirmed or invalidated) against the final snapshot becomes one TrainingRow.
    Mirrors pivot_layer.py's evaluation loop but emits TrainingRow instead of
    PivotEvaluation — no logic is duplicated, both share FeatureExtractor.
    """
    from wave_alpha.backtest.labels import pivot_confirmation_label
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.domain import Interval as _Interval
    from wave_alpha.elliott.enumerator import enumerate_counts
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.models import PivotState
    from wave_alpha.pivots.multi_degree import (
        detect_multi_degree,
        detect_multi_degree_with_snapshots,
    )
    from wave_alpha.right_edge.extractor import FeatureExtractor

    if interval is None:
        interval = _Interval.DAILY

    extractor = FeatureExtractor()
    rows: list[TrainingRow] = []

    for ticker in universe:
        if not as_of_pairs:
            continue
        max_final = max(f for _, f in as_of_pairs)
        bars = provider.fetch(  # type: ignore[union-attr]
            ticker,
            interval,
            end=max_final + timedelta(days=lookahead_bars * 2),
        )
        if not bars:
            continue

        for initial, final in as_of_pairs:
            initial_view = PointInTimeView(bars, initial).visible()
            final_view = PointInTimeView(bars, final).visible()
            if not initial_view or not final_view:
                continue

            initial_snaps = detect_multi_degree_with_snapshots(
                initial_view,
                interval=interval,
                atr_multiples=_DEFAULT_ATR_MULTIPLES,
            )
            final_pivots = detect_multi_degree(
                final_view,
                interval=interval,
                atr_multiples=_DEFAULT_ATR_MULTIPLES,
            )

            for degree, snap in initial_snaps.items():
                if snap.atr_at_last_bar is None or snap.required_move <= 0:
                    continue
                tentative = [p for p in snap.pivots if p.state == PivotState.TENTATIVE]
                later = final_pivots.get(degree, [])
                # Top-1 wave count for this (initial-snapshot, degree) feeds the
                # 14 fib feature slots. Same call assess_from_bars makes at serve
                # time, so the per-row vector matches what the model will see in
                # production for the latest tentative pivot in this snap.
                counts = enumerate_counts(snap.pivots, degree=degree, top_k=1)
                top_count = counts[0] if counts else None
                for p in tentative:
                    outcome_label = pivot_confirmation_label(p, later_pivots=later)
                    if outcome_label is None:
                        continue
                    features = extractor.extract(
                        candidate=p,
                        point_in_time_bars=initial_view,
                        atr_at_candidate=snap.atr_at_last_bar,
                        required_move=snap.required_move,
                        higher_degree_zone_match=0.0,
                        top_count=top_count,
                    )
                    rows.append(
                        TrainingRow(
                            ticker=ticker,
                            as_of=initial,
                            degree=degree,
                            features=features,
                            outcome=1 if outcome_label == "confirmed" else 0,
                        )
                    )

    return rows


# ---------------------------------------------------------------------------
# Calibrated-heuristic walk-forward
# ---------------------------------------------------------------------------


def fit_calibrated_heuristic(
    rows_by_quarter: dict[date, list[TrainingRow]],
    *,
    folds: list[Fold],
) -> tuple[dict[int, PlattParams], list[CalibratedPrediction]]:
    """Walk-forward Platt scalers fit ON TOP OF the heuristic — no logistic.

    Same fold structure and Platt-fitting logic as ``walk_forward``; uses the
    heuristic as the raw scorer instead of a fitted logistic.  Platt is fit on
    PRIOR-fold accumulator only (no leakage into the current fold).

    Returns:
        (final_platt_per_degree, oos_predictions) where ``oos_predictions``
        carry the calibrated heuristic probas for the gate comparison.
        ``final_platt_per_degree`` is refit on ALL accumulated data — use this
        in the production artifact.
    """
    from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel

    heuristic = HeuristicConfirmationModel()
    # Accumulated (raw_proba, outcome) pairs per degree, growing across folds.
    # Updated AFTER each fold so Platt is always fit on PRIOR-fold data only.
    accumulated: dict[int, list[tuple[float, int]]] = {}
    all_oos: list[CalibratedPrediction] = []

    for fold in folds:
        # Test rows: quarters falling within [test_start, test_end).
        test_rows: list[TrainingRow] = [
            r
            for q, rs in rows_by_quarter.items()
            if fold.test_start <= q < fold.test_end
            for r in rs
        ]
        if not test_rows:
            continue

        # Fit Platt scalers using ONLY data accumulated through PRIOR folds.
        # First fold gets identity Platt (no prior calibration data).
        platt = {d: fit_platt(pairs) for d, pairs in accumulated.items() if pairs}

        # Collect raw probas (defer accumulator update to after OOS emission).
        test_raw: list[tuple[TrainingRow, float]] = []
        for r in test_rows:
            raw = heuristic.predict_proba(r.features)
            test_raw.append((r, raw))

        # Emit calibrated OOS predictions using the PRIOR-fold Platt scalers.
        for r, raw in test_raw:
            scaler = platt.get(r.degree)
            cal = apply_platt(scaler, raw) if scaler else raw
            all_oos.append(
                CalibratedPrediction(
                    ticker=r.ticker,
                    as_of=r.as_of,
                    degree=r.degree,
                    raw_proba=raw,
                    calibrated_proba=cal,
                    outcome=r.outcome,
                    features=r.features,
                )
            )

        # NOW add this fold's pairs to the accumulator for NEXT fold's Platt fit.
        for r, raw in test_raw:
            accumulated.setdefault(r.degree, []).append((raw, r.outcome))

    final_platt = {d: fit_platt(pairs) for d, pairs in accumulated.items() if pairs}
    return final_platt, all_oos


# ---------------------------------------------------------------------------
# Blended final refit (synthetic + outcomes)
# ---------------------------------------------------------------------------


def train_blended_final(
    synthetic_rows: list[TrainingRow],
    outcome_dataset,  # OutcomeDataset — typed loosely to avoid import cycle
    *,
    seed: int = 42,
    with_fib: bool = False,
) -> tuple[LogisticParams, dict[int, PlattParams]]:
    """Refit the production logistic + per-degree Platt on synthetic + outcomes.

    This is the FINAL-refit replacement for the synthetic-only artifact
    produced by walk_forward. Walk-forward stays synthetic-only as an OOS
    estimator; outcomes enter only here.

    Args:
        synthetic_rows: all synthetic rows (across all quarters) — typically
                        the flattening of `rows_by_quarter.values()` used by
                        walk_forward's final refit.
        outcome_dataset: OutcomeDataset with per-row weights already assigned
                         by build_outcome_dataset.

    Returns:
        (LogisticParams, dict[degree, PlattParams]) — drop-in replacement for
        (wf.final_logistic, wf.platt_per_degree).
    """
    if not synthetic_rows and not outcome_dataset.rows:
        raise ValueError("train_blended_final called with no rows")

    # Build the combined row list + sample_weights vector. Synthetic rows
    # carry weight 1.0; outcome rows carry their assigned per-degree weight.
    combined_rows: list[TrainingRow] = list(synthetic_rows)
    weights: list[float] = [1.0] * len(synthetic_rows)
    for orow in outcome_dataset.rows:
        combined_rows.append(
            TrainingRow(
                ticker=orow.ticker,
                as_of=orow.as_of,
                degree=orow.degree,
                features=orow.features,
                outcome=round(orow.label),  # binary label
            )
        )
        weights.append(orow.weight)

    sw = np.asarray(weights, dtype=np.float64)
    logistic = fit_logistic(
        combined_rows,
        seed=seed,
        with_fib=with_fib,
        sample_weights=sw,
    )

    # Per-degree Platt: fit on (raw_proba, outcome) pairs from the combined
    # rows, also weighted. Mirrors the structure of walk_forward's final platt
    # refit, with weights threaded through.
    by_degree: dict[int, list[tuple[float, int]]] = {}
    weights_by_degree: dict[int, list[float]] = {}
    for r, w in zip(combined_rows, sw, strict=True):
        raw = predict_logistic(logistic, r.features)
        by_degree.setdefault(r.degree, []).append((raw, r.outcome))
        weights_by_degree.setdefault(r.degree, []).append(float(w))

    platt = {
        d: fit_platt(
            pairs,
            sample_weights=np.asarray(weights_by_degree[d], dtype=np.float64),
        )
        for d, pairs in by_degree.items()
        if pairs
    }
    return logistic, platt


# ---------------------------------------------------------------------------
# Artifact serialization
# ---------------------------------------------------------------------------


def serialize_artifact(
    *,
    wf: WalkForwardResult,
    gate: object,  # GateResult — duck-typed to avoid circular import
    seed: int,
    trained_at: _date_type | None = None,
    git_sha: str | None = None,
    outcome_training: dict | None = None,
) -> dict:
    """Serialize a WalkForwardResult + GateResult into a JSON-ready dict.

    When ``outcome_training`` is provided, the dict is embedded under the
    ``outcome_training`` key — the optional, additive provenance block for
    outcome-blended training runs.

    Pass ``trained_at`` and ``git_sha`` explicitly in tests to get deterministic
    output — the same data will then produce bit-for-bit identical artifacts.
    Leave both as ``None`` in production; they default to today's date and the
    current git HEAD SHA.
    """
    artifact: dict = {
        "version": "logistic_v1",
        "trained_at": (trained_at or _date_type.today()).isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "feature_order": list(wf.final_logistic.feature_order),
        "weights": list(wf.final_logistic.weights),
        "intercept": wf.final_logistic.intercept,
        "platt_per_degree": {str(d): {"a": p.a, "b": p.b} for d, p in wf.platt_per_degree.items()},
        "training": {
            "n_train_rows": wf.n_train_rows,
            "n_oos_rows": wf.n_oos_rows,
            "n_folds": len(wf.folds),
            "rng_seed": seed,
        },
        "gate": {
            "verdict": gate.verdict,  # type: ignore[attr-defined]
            "logistic_brier_ci": list(gate.logistic_brier_ci),  # type: ignore[attr-defined]
            "heuristic_brier_ci": list(gate.heuristic_brier_ci),  # type: ignore[attr-defined]
            "per_degree_calibration": gate.per_degree_calibration,  # type: ignore[attr-defined]
        },
    }
    if outcome_training is not None:
        artifact["outcome_training"] = outcome_training
    return artifact


def serialize_calibrated_heuristic_artifact(
    *,
    platt: dict[int, PlattParams],
    gate: object,  # GateResult — duck-typed to avoid circular import
    trained_at: _date_type | None = None,
    git_sha: str | None = None,
) -> dict:
    """Serialize a calibrated-heuristic training result into a JSON-ready dict.

    The ``gate.logistic_brier_ci`` field is reused for the calibrated-heuristic's
    Brier CI (calibrated heuristic vs raw heuristic).  The field name is a
    pragmatic reuse of the GateResult schema — acceptable for now, documented here.

    Pass ``trained_at`` and ``git_sha`` explicitly in tests for deterministic
    output.  Leave as ``None`` in production to auto-populate.
    """
    from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel

    return {
        "version": "calibrated_heuristic_v1",
        "heuristic_version": HeuristicConfirmationModel.version,
        "trained_at": (trained_at or _date_type.today()).isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "platt_per_degree": {str(d): {"a": p.a, "b": p.b} for d, p in platt.items()},
        "gate": {
            "verdict": gate.verdict,  # type: ignore[attr-defined]
            # Note: field named "logistic_brier_ci" by GateResult convention;
            # here it holds the calibrated-heuristic Brier CI vs raw heuristic.
            "logistic_brier_ci": list(gate.logistic_brier_ci),  # type: ignore[attr-defined]
            "heuristic_brier_ci": list(gate.heuristic_brier_ci),  # type: ignore[attr-defined]
            "per_degree_calibration": gate.per_degree_calibration,  # type: ignore[attr-defined]
        },
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def write_artifact(artifact: dict, path: Path) -> None:
    """Write the artifact dict as indented JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
