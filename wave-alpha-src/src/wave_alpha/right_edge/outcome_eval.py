"""Outcome-training evaluation harness.

Empirically measures whether outcome-driven training (--use-outcomes) improves
right-edge model quality vs synthetic-only training. Splits resolved outcomes
temporally by exit_date, trains two arms on the training portion, scores both
arms on the hold-out portion, reports per-degree Brier/ECE/log-loss and a
paired bootstrap Brier delta CI.

See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import numpy as np

from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset,
    OutcomeRow,
    WeightSchedule,
    assign_weights,
    build_outcome_dataset,
)
from wave_alpha.right_edge.training import (
    Fold,
    TrainingRow,
    apply_platt,
    predict_logistic,
    train_blended_final,
    walk_forward,
)

VerdictLiteral = Literal["improves", "no_effect", "degrades"]


@dataclass(frozen=True)
class OutcomeEvalConfig:
    """Inputs to a single outcome-eval run."""

    end: date
    universe: list[str]
    quarters: int = 16
    fold_count: int = 4
    holdout_fraction: float = 0.20
    seed: int = 42
    with_fib: bool = False
    schedule: WeightSchedule = field(default_factory=WeightSchedule)


@dataclass(frozen=True)
class ArmMetrics:
    """Per-arm scoring summary on the hold-out outcomes."""

    brier_overall: float
    brier_per_degree: dict[int, float]
    ece_per_degree: dict[int, float]
    log_loss_overall: float
    n_holdout_per_degree: dict[int, int]
    low_n_degrees: list[int]  # degrees with < 20 holdout rows


@dataclass(frozen=True)
class OutcomeEvalResult:
    """Top-level result emitted by `run_outcome_eval`."""

    config: OutcomeEvalConfig
    n_synth_rows: int
    n_train_outcomes_per_degree: dict[int, int]
    n_holdout_outcomes_per_degree: dict[int, int]
    arm_a: ArmMetrics  # synthetic-only
    arm_b: ArmMetrics  # synthetic + outcomes
    brier_delta_ci: tuple[float, float, float]  # (lo, median, hi) on B - A
    verdict: VerdictLiteral
    generated_at: datetime


def _split_outcomes_temporal(
    rows: list[OutcomeRow],
    *,
    fraction: float,
) -> tuple[list[OutcomeRow], list[OutcomeRow]]:
    """Split outcomes into (train, holdout) by exit_date.

    The hold-out is the most-recent `fraction` of rows. Ties at the split
    boundary are pushed to the training side so we never train on a date
    later than the earliest hold-out date.
    """
    if not rows:
        raise ValueError("_split_outcomes_temporal: rows is empty")
    n = len(rows)
    n_holdout_target = int(n * fraction)
    if n_holdout_target < 1:
        raise ValueError(
            f"_split_outcomes_temporal: fraction={fraction} x N={n} < 1 -> "
            "zero holdout rows; pick a larger fraction or gather more outcomes"
        )

    sorted_rows = sorted(rows, key=lambda r: (r.exit_date, r.ticker, r.as_of))
    # Tentative boundary: include the most-recent n_holdout_target rows.
    boundary_idx = n - n_holdout_target
    boundary_date = sorted_rows[boundary_idx].exit_date
    # If the boundary cuts through a group of same-date rows (i.e. the row
    # just before the boundary shares the same exit_date), push ALL rows with
    # that date to the training side so we never train on a date later than
    # the earliest hold-out date.
    if boundary_idx > 0 and sorted_rows[boundary_idx - 1].exit_date == boundary_date:
        while boundary_idx < n and sorted_rows[boundary_idx].exit_date == boundary_date:
            boundary_idx += 1

    train = sorted_rows[:boundary_idx]
    holdout = sorted_rows[boundary_idx:]
    if not holdout:
        raise ValueError(
            "_split_outcomes_temporal: tie-pushing consumed the entire holdout; "
            "all rows share the boundary exit_date — cannot evaluate"
        )
    return train, holdout


def _score_arm_from_predictions(
    preds: list[tuple[float, int, int]],
    *,
    low_n_threshold: int = 20,
) -> ArmMetrics:
    """Compute per-arm metrics from pre-scored (calibrated_proba, label, degree)
    triples. Pure function — independent of the arm's logistic/Platt models.

    Reuses backtest.brier.brier_score and backtest.brier.calibration_error
    so metrics are identical to the existing promotion gate's primitives.
    """
    from wave_alpha.backtest.brier import brier_score, calibration_error

    if not preds:
        raise ValueError("_score_arm_from_predictions: empty preds")

    # Overall Brier on (proba, label) pairs.
    pl_pairs = [(p, lab) for p, lab, _ in preds]
    brier_overall = brier_score(pl_pairs)

    # Overall log-loss with clipping to avoid log(0).
    eps = 1e-15
    log_loss_overall = -sum(
        lab * math.log(max(p, eps)) + (1 - lab) * math.log(max(1 - p, eps)) for p, lab, _ in preds
    ) / len(preds)

    # Per-degree breakdowns.
    by_degree: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, lab, d in preds:
        by_degree[d].append((p, lab))
    brier_per_degree = {d: brier_score(pairs) for d, pairs in by_degree.items()}
    ece_per_degree = {d: calibration_error(pairs, n_bins=10) for d, pairs in by_degree.items()}
    n_holdout_per_degree = {d: len(pairs) for d, pairs in by_degree.items()}
    low_n_degrees = sorted(d for d, n in n_holdout_per_degree.items() if n < low_n_threshold)

    return ArmMetrics(
        brier_overall=brier_overall,
        brier_per_degree=brier_per_degree,
        ece_per_degree=ece_per_degree,
        log_loss_overall=log_loss_overall,
        n_holdout_per_degree=n_holdout_per_degree,
        low_n_degrees=low_n_degrees,
    )


def _brier_delta_ci(
    preds_a: list[tuple[float, int, int]],
    preds_b: list[tuple[float, int, int]],
    *,
    n_resamples: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Paired bootstrap 95% CI on (Brier_B - Brier_A).

    preds_a[i] and preds_b[i] MUST be the same hold-out row scored by each
    arm. The bootstrap resamples row indices with replacement; both arms see
    the same resampled indices on each iteration.

    Returns (lo, median, hi) at quantiles (0.025, 0.5, 0.975).
    """
    if len(preds_a) != len(preds_b):
        raise ValueError(
            f"_brier_delta_ci: paired length mismatch — len(a)={len(preds_a)} len(b)={len(preds_b)}"
        )
    n = len(preds_a)
    if n == 0:
        raise ValueError("_brier_delta_ci: empty preds")

    # Per-row squared error for each arm.
    sq_a = np.array([(p - lab) ** 2 for p, lab, _ in preds_a], dtype=np.float64)
    sq_b = np.array([(p - lab) ** 2 for p, lab, _ in preds_b], dtype=np.float64)

    rng = np.random.default_rng(seed)
    deltas = np.empty(n_resamples, dtype=np.float64)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        deltas[i] = float(sq_b[idx].mean() - sq_a[idx].mean())

    lo = float(np.quantile(deltas, 0.025))
    mid = float(np.quantile(deltas, 0.5))
    hi = float(np.quantile(deltas, 0.975))
    return lo, mid, hi


def _verdict_from_ci(ci: tuple[float, float, float]) -> VerdictLiteral:
    """Map a (lo, median, hi) CI on B - A into a verdict.

    Negative delta = B (outcome-blended) has lower Brier = improvement.
    """
    lo, _mid, hi = ci
    if hi < 0:
        return "improves"
    if lo > 0:
        return "degrades"
    return "no_effect"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_outcome_eval(
    cfg: OutcomeEvalConfig,
    *,
    rows_by_quarter: dict[date, list[TrainingRow]],
    folds: list[Fold],
    bar_provider: object,  # OHLCVProvider
    history_db_path: Path,
) -> OutcomeEvalResult:
    """Orchestrate the outcome-training evaluation.

    Steps mirror spec §5 pipeline:
    1. Train arm A (synthetic-only) via walk_forward.
    2. Build outcome dataset from history_db_path (raw, no weights yet).
    3. Temporal split by exit_date → train_outcomes + holdout_outcomes.
    4. assign_weights on TRAIN_OUTCOMES ONLY (per-degree ramp uses training count).
    5. Train arm B via train_blended_final(synthetic + train_outcomes_weighted).
    6. Score each arm on holdout_outcomes (predict_logistic → apply_platt → metric).
    7. Paired bootstrap on Brier delta + verdict.
    """
    # --- Arm A: synthetic-only walk-forward ---
    wf = walk_forward(
        rows_by_quarter,
        folds=folds,
        seed=cfg.seed,
        with_fib=cfg.with_fib,
    )

    # --- Outcome dataset (UNWEIGHTED here — we split first, then weight). ---
    # n_synth_per_degree fed to build_outcome_dataset is fine; weights will be
    # overwritten below by re-running assign_weights on the training portion.
    synth_rows_all = [r for rs in rows_by_quarter.values() for r in rs]
    n_synth_per_degree = dict(Counter(r.degree for r in synth_rows_all))
    raw_ds = build_outcome_dataset(
        db_path=history_db_path,
        bar_provider=bar_provider,
        degree=2,
        schedule=cfg.schedule,
        n_synth_per_degree=n_synth_per_degree,
    )

    # --- Temporal split ---
    train_rows, holdout_rows = _split_outcomes_temporal(
        raw_ds.rows,
        fraction=cfg.holdout_fraction,
    )

    # --- assign_weights AFTER split: per-degree ramp uses training count. ---
    # This is load-bearing per spec §4 — the ramp must reflect the row count
    # a real deployment training moment would see, not the full pre-split count.
    train_rows_weighted = assign_weights(
        train_rows,
        n_synth_per_degree=n_synth_per_degree,
        schedule=cfg.schedule,
    )
    train_ds = OutcomeDataset(
        rows=train_rows_weighted,
        skipped_drift=raw_ds.skipped_drift,
        skipped_no_bars=raw_ds.skipped_no_bars,
        skipped_wrong_degree=raw_ds.skipped_wrong_degree,
        schedule=cfg.schedule,
    )

    # --- Arm B: synthetic + train_outcomes blended refit ---
    logistic_b, platt_b = train_blended_final(
        synth_rows_all,
        train_ds,
        seed=cfg.seed,
        with_fib=cfg.with_fib,
    )

    # --- Score both arms on the same holdout ---
    def _score_one(logistic, platt) -> list[tuple[float, int, int]]:
        out: list[tuple[float, int, int]] = []
        for row in holdout_rows:
            raw = predict_logistic(logistic, row.features)
            scaler = platt.get(row.degree)
            cal = apply_platt(scaler, raw) if scaler else raw
            out.append((cal, round(row.label), row.degree))
        return out

    preds_a = _score_one(wf.final_logistic, wf.platt_per_degree)
    preds_b = _score_one(logistic_b, platt_b)

    arm_a = _score_arm_from_predictions(preds_a)
    arm_b = _score_arm_from_predictions(preds_b)
    ci = _brier_delta_ci(preds_a, preds_b, n_resamples=1000, seed=cfg.seed)
    verdict = _verdict_from_ci(ci)

    return OutcomeEvalResult(
        config=cfg,
        n_synth_rows=len(synth_rows_all),
        n_train_outcomes_per_degree=dict(Counter(r.degree for r in train_rows)),
        n_holdout_outcomes_per_degree=dict(Counter(r.degree for r in holdout_rows)),
        arm_a=arm_a,
        arm_b=arm_b,
        brier_delta_ci=ci,
        verdict=verdict,
        generated_at=datetime.now(),
    )


def format_markdown_report(result: OutcomeEvalResult) -> str:
    """Render an OutcomeEvalResult as a human-readable markdown report.

    Layout (spec §5 / §6):
      # Outcome-Training Evaluation
      ## Config
      ## Row counts
      ## Arm A (synthetic-only)
      ## Arm B (synthetic + outcomes)
      ## Δ Brier (B - A)
      ## Verdict
    """
    cfg = result.config
    lo, mid, hi = result.brier_delta_ci

    def _arm_block(name: str, m: ArmMetrics) -> str:
        per_deg = ", ".join(
            f"d{d}: brier={m.brier_per_degree[d]:.4f} ece={m.ece_per_degree[d]:.4f} "
            f"n={m.n_holdout_per_degree[d]}"
            for d in sorted(m.brier_per_degree)
        )
        low_n = f"\n- low_n degrees (<20 rows): {m.low_n_degrees}" if m.low_n_degrees else ""
        return (
            f"## {name}\n\n"
            f"- Brier overall: **{m.brier_overall:.4f}**\n"
            f"- Log-loss overall: {m.log_loss_overall:.4f}\n"
            f"- Per-degree: {per_deg}{low_n}\n"
        )

    train_counts = ", ".join(
        f"d{d}: {n}" for d, n in sorted(result.n_train_outcomes_per_degree.items())
    )
    holdout_counts = ", ".join(
        f"d{d}: {n}" for d, n in sorted(result.n_holdout_outcomes_per_degree.items())
    )

    parts = [
        "# Outcome-Training Evaluation\n",
        "## Config\n",
        f"- End date: **{cfg.end.isoformat()}**\n",
        f"- Universe: {len(cfg.universe)} tickers ({', '.join(cfg.universe[:5])}"
        f"{'…' if len(cfg.universe) > 5 else ''})\n",
        f"- Quarters: {cfg.quarters} | Folds: {cfg.fold_count} | "
        f"Holdout fraction: {cfg.holdout_fraction}\n",
        f"- Seed: {cfg.seed} | with_fib: {cfg.with_fib} | "
        f"n_target_per_degree: {cfg.schedule.n_target_per_degree}\n",
        "\n## Row counts\n\n",
        f"- Synthetic rows: {result.n_synth_rows}\n",
        f"- Training outcomes per degree: {train_counts}\n",
        f"- Holdout outcomes per degree: {holdout_counts}\n",
        "\n",
        _arm_block("Arm A (synthetic-only)", result.arm_a),
        "\n",
        _arm_block("Arm B (synthetic + outcomes)", result.arm_b),
        "\n",
        "## Δ Brier (B - A)\n\n",
        f"- 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}] (lo, median, hi)\n",
        "- Negative = outcome training improves Brier on the hold-out.\n",
        "\n",
        "## Verdict\n\n",
        f"**{result.verdict}**\n\n",
        "- `improves`: 95% CI strictly below 0 (outcome training helps).\n",
        "- `no_effect`: CI straddles 0 (no measurable effect).\n",
        "- `degrades`: 95% CI strictly above 0 (outcome training hurts).\n",
        "\n",
        f"---\n_Generated: {result.generated_at.isoformat()}_\n",
    ]
    return "".join(parts)
