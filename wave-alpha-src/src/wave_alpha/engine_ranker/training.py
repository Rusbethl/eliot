"""Engine-ranker outcome dataset builder and walk-forward logistic training.

Reads resolved snapshots from history.sqlite, replays run_analysis at each
(ticker, as_of) against cached bars, builds EngineRankerFeatures for the
top-1 ranked candidate, and fits a shared-coef + per-degree-intercept logistic
via NumPy batch gradient descent.

No sklearn dependency. Mirrors right_edge/training.fit_logistic in batch-GD
pattern, seed handling, and L2 regularisation.

Public surface:
    OutcomeDatasetRow, OutcomeDataset  — data models
    build_outcome_dataset              — DB read + pipeline replay + labelling
    walk_forward_split                 — chronological train/holdout split
    _assert_no_train_leakage           — invariant check (exported for tests)
    CandidateArtifact                  — fit result
    train_logistic                     — NumPy batch-GD fit
    serialize_artifact                 — produce JSON-ready dict
    candidate_to_loadable              — in-process artifact → LogisticEngineRanker
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wave_alpha.engine_ranker.logistic import LogisticEngineRanker

import numpy as np
from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.engine_ranker.features import EngineRankerFeatures

# ---------------------------------------------------------------------------
# Module-level constant: the four features used by the engine ranker logistic.
# Per-degree intercepts are encoded as one-hot columns appended to the matrix.
# ---------------------------------------------------------------------------

_FEATURE_ORDER: tuple[str, ...] = ("template_fit", "fib", "recency", "coherence")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutcomeDatasetRow:
    ticker: str
    as_of: date
    features: EngineRankerFeatures | None
    label: int  # 0 or 1
    degree: int
    weight: float = 1.0


@dataclass(frozen=True)
class OutcomeDataset:
    rows: list[OutcomeDatasetRow]
    skipped_no_features: int
    skipped_unresolved: int
    n_target: int
    n_non_target: int


# ---------------------------------------------------------------------------
# History.sqlite read
# ---------------------------------------------------------------------------


def _select_resolved_snapshots(*, db_path: Path) -> Iterator[tuple[str, date, str]]:
    """Yield (ticker, as_of, outcome_status) for resolved rows."""
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, outcome_status, payload_json
            FROM snapshots
            WHERE outcome_status IN ('target', 'stop', 'time_stop')
            ORDER BY ticker ASC, as_of ASC
            """
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    for ticker, as_of_str, outcome_status, _payload_json in rows:
        yield ticker, date.fromisoformat(as_of_str), outcome_status


# ---------------------------------------------------------------------------
# Outcome dataset construction
# ---------------------------------------------------------------------------


def build_outcome_dataset(
    *,
    db_path: Path,
    provider: OHLCVProvider,
    include_time_stops: bool = True,
) -> OutcomeDataset:
    """Read every snapshot from history.sqlite with a resolved outcome, replay
    run_analysis(...llm_mode=DISABLED) at each (ticker, as_of) against cached
    bars, build EngineRankerFeatures.from_count for the top-1 ranked candidate,
    label it (1 if status=='target' else 0).

    Args:
        db_path: Path to history.sqlite.
        provider: OHLCVProvider instance (typically CachedProvider(YahooProvider())).
        include_time_stops: When False, snapshots with outcome_status='time_stop'
            are dropped. When True, they are labelled as 0.
    """
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.llm.modes import LLMMode
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    rows: list[OutcomeDatasetRow] = []
    skipped_no_features = 0
    skipped_unresolved = 0

    for ticker, as_of, outcome_status in _select_resolved_snapshots(db_path=db_path):
        # Determine binary label.
        if outcome_status == "target":
            label = 1
        elif outcome_status == "stop":
            label = 0
        elif outcome_status == "time_stop":
            if not include_time_stops:
                skipped_unresolved += 1
                continue
            label = 0
        else:
            skipped_unresolved += 1
            continue

        # Replay the pipeline at this (ticker, as_of) with LLM disabled.
        req = AnalyzeRequest(ticker=ticker, as_of=as_of)
        try:
            # Use the heuristic ranker explicitly so feature extraction is
            # stable across training rounds — otherwise a deployed logistic
            # would silently feed into the next training round's features.
            from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker

            result = run_analysis(
                req,
                provider=provider,
                llm_mode=LLMMode.DISABLED,
                engine_ranker=HeuristicEngineRanker(),
            )
        except Exception as exc:
            logger.warning(
                "engine_ranker.build_outcome_dataset: skip {} @ {}: {}",
                ticker,
                as_of,
                exc,
            )
            skipped_no_features += 1
            continue

        if not result.ranked_daily:
            skipped_no_features += 1
            continue

        # Build EngineRankerFeatures from the top-1 ranked candidate.
        top1 = result.ranked_daily[0]
        try:
            coherence: CoherenceResult | None = result.coherence
            features = EngineRankerFeatures.from_count(top1.count, coherence)
        except (ValueError, AttributeError):
            skipped_no_features += 1
            continue

        rows.append(
            OutcomeDatasetRow(
                ticker=ticker,
                as_of=as_of,
                features=features,
                label=label,
                degree=features.degree,
            )
        )

    n_target = sum(1 for r in rows if r.label == 1)
    n_non_target = sum(1 for r in rows if r.label == 0)
    return OutcomeDataset(
        rows=rows,
        skipped_no_features=skipped_no_features,
        skipped_unresolved=skipped_unresolved,
        n_target=n_target,
        n_non_target=n_non_target,
    )


# ---------------------------------------------------------------------------
# Walk-forward split
# ---------------------------------------------------------------------------


def walk_forward_split(
    rows: list[OutcomeDatasetRow],
    *,
    split: float,
) -> tuple[list[OutcomeDatasetRow], list[OutcomeDatasetRow]]:
    """Time-split by distinct as_of date. Returns (train, holdout).

    Determines the cutoff as the date at index floor(split * n_distinct_dates) - 1
    (0-indexed into the sorted list of distinct dates). Train is inclusive
    on the cutoff date; holdout is everything strictly after cutoff.

    Example: 4 distinct dates, split=0.6 → floor(0.6*4)=2 → cutoff = dates[1]
    (the second date). Train has dates[0] and dates[1]; holdout has dates[2:]

    Boundary behaviour:
        - ``split=1.0``: cutoff is the last distinct date → all rows go to train,
          holdout is empty.
        - ``split=0.0``: cutoff is the *first* distinct date (``max(0, …)`` clamps
          the index to 0) → the smallest date is in train, all later dates are in
          holdout.
        - Empty ``rows``: returns ``([], [])`` immediately.
    """
    distinct_dates = sorted({r.as_of for r in rows})
    if not distinct_dates:
        return [], []
    n = len(distinct_dates)
    cutoff_idx = max(0, int(split * n) - 1)
    cutoff = distinct_dates[cutoff_idx]
    train = [r for r in rows if r.as_of <= cutoff]
    holdout = [r for r in rows if r.as_of > cutoff]
    _assert_no_train_leakage(train, cutoff=cutoff)
    return train, holdout


def _assert_no_train_leakage(
    train: list[OutcomeDatasetRow],
    *,
    cutoff: date,
) -> None:
    """Raise AssertionError with 'lookahead' in the message if any row in
    `train` has as_of > cutoff."""
    for r in train:
        if r.as_of > cutoff:
            raise AssertionError(f"lookahead: train row has as_of={r.as_of} > cutoff={cutoff}")


# ---------------------------------------------------------------------------
# NumPy batch gradient descent helpers
# ---------------------------------------------------------------------------


def _build_matrix(rows: list[OutcomeDatasetRow], degrees: list[int]) -> np.ndarray:
    """Build design matrix of shape (N, 4 + D).

    Columns: [template_fit, fib, recency, coherence, deg_d1_onehot, ...]
    where D = len(degrees) and the degree one-hots are in the order of `degrees`.
    """
    degree_to_idx = {d: i for i, d in enumerate(degrees)}
    n_deg = len(degrees)
    n_feat = len(_FEATURE_ORDER)
    n_cols = n_feat + n_deg
    x_mat = np.zeros((len(rows), n_cols), dtype=np.float64)
    for i, row in enumerate(rows):
        f = row.features
        for j, name in enumerate(_FEATURE_ORDER):
            x_mat[i, j] = float(getattr(f, name))
        deg_col = n_feat + degree_to_idx.get(row.degree, 0)
        x_mat[i, deg_col] = 1.0
    return x_mat


def _fit_logistic_numpy(
    x_mat: np.ndarray,
    y: np.ndarray,
    sample_weights: np.ndarray,
    lr: float,
    n_iters: int,
    weight_decay: float,
    seed: int,
) -> np.ndarray:
    """Batch gradient descent on binary cross-entropy with L2 regularisation.

    Mirrors right_edge/training.fit_logistic in loop structure, weight handling,
    and deterministic init via np.random.default_rng(seed).

    No separate bias/intercept: per-degree intercepts are the one-hot columns
    at the tail of x_mat, so there is no global intercept (fit_intercept=False
    semantics).

    Returns the full coefficient vector of shape (n_cols,).
    """
    rng = np.random.default_rng(seed)
    w = rng.normal(0, 0.01, size=x_mat.shape[1])
    sw_sum = float(sample_weights.sum())

    for _ in range(n_iters):
        z = x_mat @ w
        p = 1.0 / (1.0 + np.exp(-z))
        err = (p - y) * sample_weights
        grad_w = x_mat.T @ err / sw_sum + weight_decay * w
        w -= lr * grad_w

    return w


# ---------------------------------------------------------------------------
# CandidateArtifact and train_logistic
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateArtifact:
    version: str
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept_per_degree: dict[int, float]
    metadata: dict[str, Any]


def train_logistic(
    dataset: OutcomeDataset,
    *,
    walk_forward_split_fraction: float = 0.7,
    random_state: int = 42,
    class_balance_low: float = 0.35,
    class_balance_high: float = 0.65,
    lr: float = 0.05,
    n_iters: int = 2000,
    weight_decay: float = 1e-4,
) -> CandidateArtifact:
    """Fit shared-coef + per-degree-intercept logistic on the train split via
    NumPy batch GD. Per-degree intercepts encoded as one-hot columns: the
    design matrix is X = [template_fit, fib, recency, coherence,
    deg_d1_onehot, deg_d2_onehot, ...]. After fit, weights[:4] are the
    feature coefficients; weights[4:] map to observed degrees in sorted order.

    Mirrors right_edge/training.fit_logistic in hyperparams, init, and L2
    structure; that function is the canonical reference for the NumPy
    batch-GD pattern used in this project.
    """
    # Filter out rows with None features before splitting.
    usable = [r for r in dataset.rows if r.features is not None]
    if not usable:
        raise ValueError("train_logistic: no usable rows (all features are None)")

    # Walk-forward train/holdout split.
    train_rows, holdout_rows = walk_forward_split(usable, split=walk_forward_split_fraction)

    if not train_rows:
        raise ValueError("train_logistic: train split is empty after walk_forward_split")

    walk_forward_cutoff = max(r.as_of for r in train_rows)

    # Determine observed degrees in sorted order for stable one-hot indexing.
    observed_degrees = sorted({r.degree for r in train_rows})

    # Build design matrix and label vector.
    x_mat = _build_matrix(train_rows, observed_degrees)
    y = np.array([float(r.label) for r in train_rows], dtype=np.float64)

    # Class-balance sample weights.
    target_rate = float(y.mean())
    class_balance_applied: bool
    if target_rate < class_balance_low or target_rate > class_balance_high:
        # Clamp to avoid 1/0 in pathological all-positive or all-negative splits.
        tr = float(np.clip(target_rate, 1e-6, 1.0 - 1e-6))
        sw = np.where(y == 1.0, 1.0 / tr, 1.0 / (1.0 - tr))
        class_balance_applied = True
    else:
        sw = np.ones(len(train_rows), dtype=np.float64)
        class_balance_applied = False

    # Fit via NumPy batch GD.
    coefs = _fit_logistic_numpy(
        x_mat, y, sw, lr=lr, n_iters=n_iters, weight_decay=weight_decay, seed=random_state
    )

    # Split coefs: first 4 are feature weights; remaining are per-degree intercepts.
    n_feat = len(_FEATURE_ORDER)
    weights = tuple(float(c) for c in coefs[:n_feat])
    intercept_per_degree = {d: float(coefs[n_feat + i]) for i, d in enumerate(observed_degrees)}

    # Compute simple OOS stats from holdout (informational metadata only).
    n_holdout = len(holdout_rows)
    holdout_targets = sum(1 for r in holdout_rows if r.label == 1)

    metadata: dict[str, Any] = {
        "n_train": len(train_rows),
        "n_holdout": n_holdout,
        "n_holdout_targets": holdout_targets,
        "observed_degrees": observed_degrees,
        "target_rate_train": target_rate,
        "class_balance_applied": class_balance_applied,
        "walk_forward_cutoff": walk_forward_cutoff.isoformat(),
        "lr": lr,
        "n_iters": n_iters,
        "weight_decay": weight_decay,
        "random_state": random_state,
    }

    return CandidateArtifact(
        version="engine_ranker_logistic_v1",
        feature_order=_FEATURE_ORDER,
        weights=weights,
        intercept_per_degree=intercept_per_degree,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def serialize_artifact(
    artifact: CandidateArtifact,
    *,
    gate_verdict: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the dict that becomes the on-disk JSON artifact.

    The gate.verdict key is what LogisticEngineRanker.from_json checks before
    loading — only "PROMOTE" is accepted.
    """
    extra = extra or {}
    payload: dict[str, Any] = {
        "version": artifact.version,
        "trained_at": date.today().isoformat(),
        "git_sha": _git_sha(),
        "feature_order": list(artifact.feature_order),
        "weights": list(artifact.weights),
        "intercept_per_degree": {str(d): v for d, v in artifact.intercept_per_degree.items()},
        "gate": {
            "verdict": gate_verdict,
        },
        "metadata": artifact.metadata,
    }
    overlap = set(extra) & set(payload)
    if overlap:
        raise ValueError(f"extra keys would overwrite core artifact keys: {sorted(overlap)}")
    payload.update(extra)
    return payload


def candidate_to_loadable(artifact: CandidateArtifact) -> LogisticEngineRanker:
    """In-process: turn a CandidateArtifact into a usable LogisticEngineRanker
    without round-tripping through JSON. Used in promotion evaluation."""
    import statistics

    from wave_alpha.engine_ranker.logistic import LogisticEngineRanker

    intercepts = artifact.intercept_per_degree
    median_intercept = statistics.median(intercepts.values()) if intercepts else 0.0
    return LogisticEngineRanker(
        version=artifact.version,
        feature_order=artifact.feature_order,
        weights=artifact.weights,
        intercept_per_degree=dict(intercepts),
        _median_intercept=median_intercept,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"
