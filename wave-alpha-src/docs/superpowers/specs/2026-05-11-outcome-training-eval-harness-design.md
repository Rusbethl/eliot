# Outcome-Training Evaluation Harness — Design

**Status:** Draft, 2026-05-11
**Author:** Alex Chen (with Claude)
**Branch:** TBD (depends on PR #4 merge state — see §10)

## 1. Problem

v0.10.0 shipped outcome-driven right-edge training: `wave-alpha train right-edge --use-outcomes` blends realized outcomes from `history.sqlite` into the final logistic + Platt refit, with a per-degree sample-weight ramp (`n_target_per_degree=100`) that scales influence from ~0 to synthetic-parity as labelled rows accumulate.

We have **no empirical answer** to "does this help?" The existing promotion gate (`right_edge/promotion.py:evaluate_promotion`) compares the *logistic* against the *heuristic* on synthetic OOS data. It does **not** compare {synthetic-only logistic} against {synthetic+outcome logistic} on a held-out set of *real outcomes*. So the project is shipping a feature whose marginal value is unmeasured.

This spec defines a read-only evaluation harness that answers the question.

## 2. Goals

- **G1.** Produce a paired comparison of two right-edge models, A and B, on a held-out set of realized outcomes:
  - **A = synthetic-only** — `walk_forward` final-refit logistic + Platt, no outcome blending.
  - **B = synthetic+outcomes** — `train_blended_final` over the same synthetic rows + the *training portion* of outcomes.
- **G2.** Report per-degree Brier, ECE, log-loss, and a paired bootstrap CI on Δ Brier.
- **G3.** Surface as a CLI command that writes a markdown report. No artifact writes; no model promotion gating.
- **G4.** Be reproducible: deterministic given (universe, end date, seed, history.sqlite contents).

## 3. Non-goals

- **NG1.** New training modes or model types. Reuses `walk_forward`, `train_blended_final`, `brier_score`, `calibration_error`.
- **NG2.** Auto-gating model promotion on outcome metrics. Humans interpret the report; promotion stays where it is.
- **NG3.** K-fold or rolling-window evaluation. Single temporal split in v1 (see §4).
- **NG4.** Mutations to `history.sqlite` schema. The harness is strictly read-only against it.
- **NG5.** A new evaluation metric framework. We reuse the existing primitives in `right_edge/training.py`, `right_edge/promotion.py`, and `backtest/brier.py`.

## 4. Approach: temporal hold-out

Outcomes are time-series. Random k-fold risks training-set leakage (the same ticker's neighbouring trades carry overlapping pivot structure). The deployment use case is "retrain monthly, evaluate on next month's outcomes." A temporal split mirrors this and is the project's existing idiom (walk-forward folds in `walk_forward`).

**Split rule:** rank all resolved outcomes (status ∈ {`target`, `stop`, `time_stop`}) by `outcome.exit_date` ascending. The hold-out is the most-recent fraction *f* (default `0.20`); the training portion is the remainder. The harness reports `n_train_outcomes` and `n_holdout_outcomes` per degree so the reader sees the statistical power on each side.

Boundary handling: rows with identical `exit_date` at the split boundary go to the **training** side (closes the gap so we never train on a date later than the earliest hold-out date). If the implementation finds zero hold-out rows at the chosen fraction, raise a clear error rather than silently producing a meaningless report.

**Weight assignment order.** `assign_weights` runs *after* the split, against the training-portion rows only. This is load-bearing: the per-degree weight ramp `n_target_per_degree` must reflect the row count that would exist at a real deployment training moment, not the full pre-split count. Holdout rows do not need weights (they are scored, not fit).

## 5. Pipeline

```
                          ┌──────────────────────────┐
                          │ build synthetic rows     │
                          │ (existing walk_forward    │
                          │  fold expansion)         │
                          └────────────┬─────────────┘
                                       │ synth_rows_all
                                       ▼
   history.sqlite ──► build_outcome_dataset ──► OutcomeDataset
                                       │
                                       ▼
                          ┌──────────────────────────┐
                          │ split by exit_date       │
                          │  → train_outcomes (1-f)  │
                          │  → holdout_outcomes (f)  │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────┴─────────────┐
                          ▼                          ▼
                    Arm A (synth-only)        Arm B (blended)
                    walk_forward(...)         train_blended_final(
                       .final_logistic,           synth_rows_all,
                       .platt_per_degree          train_outcomes,
                                                   weights via WeightSchedule)
                          │                          │
                          └──────────┬───────────────┘
                                     ▼
                       score on holdout_outcomes
                       per-arm and per-degree:
                       • Brier (overall + per-degree)
                       • ECE (per-degree, 10 bins)
                       • log-loss (sanity)
                       paired delta:
                       • Δ Brier = B − A (with bootstrap CI)
                                     │
                                     ▼
                       format_markdown_report → reports/outcome_eval_<end>.md
```

### 5.1 Scoring rule

For each hold-out `OutcomeRow`:
1. Predict raw logistic probability via `predict_logistic(arm, row.features)`.
2. Apply that arm's per-degree Platt scaler via `apply_platt(platt[row.degree], raw)`.
3. Compare to `row.label` (binary, from `label_for_status`).

`time_stop` rows in the hold-out are labelled exactly the same way they would be at training (`schedule.time_stop_label`, default `0.0`). The schedule is reused for hold-out labelling so train/eval semantics match.

### 5.2 Statistics

- **Brier delta CI:** paired bootstrap (1000 resamples, matches `promotion._paired_bootstrap_brier`). The pair is (Brier_A, Brier_B) on the same resampled hold-out row set.
- **Per-degree ECE:** `calibration_error` with 10 equal-width bins. If a degree's hold-out count < 20, report it with a `low_n` warning flag rather than refusing.
- **Verdict text** (advisory, not a gate): "outcome training improves Brier (Δ < 0, CI excludes 0)", "no significant effect (CI straddles 0)", or "outcome training degrades Brier (Δ > 0, CI excludes 0)".

## 6. Public surface

### 6.1 New module: `src/wave_alpha/right_edge/outcome_eval.py`

```python
@dataclass(frozen=True)
class OutcomeEvalConfig:
    end: date
    universe: list[str]
    quarters: int = 16        # mirrors train_right_edge default
    fold_count: int = 4
    holdout_fraction: float = 0.20
    seed: int = 42
    with_fib: bool = False
    schedule: WeightSchedule = WeightSchedule()  # see outcome_dataset

@dataclass(frozen=True)
class ArmMetrics:
    brier_overall: float
    brier_per_degree: dict[int, float]
    ece_per_degree: dict[int, float]
    log_loss_overall: float
    n_holdout_per_degree: dict[int, int]

@dataclass(frozen=True)
class OutcomeEvalResult:
    config: OutcomeEvalConfig
    n_synth_rows: int
    n_train_outcomes_per_degree: dict[int, int]
    n_holdout_outcomes_per_degree: dict[int, int]
    arm_a: ArmMetrics                       # synthetic-only
    arm_b: ArmMetrics                       # synthetic + outcomes
    brier_delta_ci: tuple[float, float, float]  # (lo, median, hi) on B − A
    verdict: Literal["improves", "no_effect", "degrades"]
    generated_at: datetime

def run_outcome_eval(
    cfg: OutcomeEvalConfig,
    *,
    rows_by_quarter: dict[date, list[TrainingRow]],  # built by CLI via _load_rows_by_quarter
    folds: list[Fold],                                # ditto
    bar_provider: OHLCVProvider,                      # for outcome feature replay
    history_db_path: Path,                            # defaults to ~/.wave_alpha/history.sqlite
) -> OutcomeEvalResult: ...

def format_markdown_report(result: OutcomeEvalResult) -> str: ...
```

### 6.2 New CLI command: `wave-alpha train outcome-eval`

Lives in `cli/app.py` under the existing `train_app` sub-app. Read-only — does **not** accept `--write` or modify any artifact.

```
wave-alpha train outcome-eval \
    --end 2024-12-31 \
    --universe data/universe_curated.txt \
    [--quarters 16] \
    [--folds 4] \
    [--holdout-fraction 0.20] \
    [--seed 42] \
    [--with-fib-features] \
    [--outcome-weight-target 100] \
    [--include-time-stops/--no-time-stops] \
    [--out reports/outcome_eval_<end>.md]
```

Default `--out` derived from `--end`. Identical config-loading behaviour to `train right-edge` (reads `right_edge.outcome_training` defaults from `config.yaml`, CLI overrides win).

## 7. File structure

| File | Purpose | Status |
|---|---|---|
| `src/wave_alpha/right_edge/outcome_eval.py` | `OutcomeEvalConfig`, `ArmMetrics`, `OutcomeEvalResult`, `run_outcome_eval`, `format_markdown_report` | CREATE |
| `src/wave_alpha/cli/app.py` | Add `train outcome-eval` command | MODIFY |
| `tests/right_edge/test_outcome_eval.py` | Unit tests: split logic, metric math, edge cases | CREATE |
| `tests/test_cli_train.py` | One integration test for `train outcome-eval` end-to-end | MODIFY |
| `docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md` | This document | CREATE |

| `src/wave_alpha/right_edge/outcome_dataset.py` | Add `exit_date: date` field to `OutcomeRow`; populate in `build_outcome_dataset` from `snapshot.outcome.exit_date`. Required by the temporal split logic. | MODIFY |
| `tests/right_edge/test_outcome_dataset.py` | Update any direct `OutcomeRow(...)` construction to pass `exit_date` | MODIFY |

No changes to:
- `right_edge/training.py` (reused as-is)
- `right_edge/promotion.py` (independent — promotion stays on synthetic OOS)
- `history/store.py`, `history/models.py`

## 8. Testing

### 8.1 Unit (`tests/right_edge/test_outcome_eval.py`)

| Test | Locks |
|---|---|
| `test_temporal_split_by_exit_date` | Hold-out fraction 0.20 over 10 outcomes → 2 most-recent rows in hold-out |
| `test_temporal_split_ties_go_to_train` | Identical exit_date at boundary → all go train side |
| `test_temporal_split_zero_holdout_raises` | `holdout_fraction × N < 1` raises ValueError |
| `test_arm_metrics_brier_matches_brier_score` | Computed Brier matches `backtest.brier.brier_score` exactly |
| `test_arm_metrics_per_degree_breakdown` | Per-degree dict keys = degrees present in hold-out |
| `test_ece_low_n_flag_when_degree_holdout_under_20` | `ArmMetrics` reports `low_n` for sparse degrees |
| `test_verdict_improves_when_delta_negative_and_ci_excludes_zero` | Synthetic-degraded vs blended-perfect → "improves" |
| `test_verdict_no_effect_when_ci_straddles_zero` | Synthetic ≈ blended → "no_effect" |
| `test_run_outcome_eval_deterministic_seed` | Same (cfg, seed) → byte-identical `OutcomeEvalResult` |
| `test_format_markdown_report_includes_required_sections` | Report has: config, row counts, arm A, arm B, delta, verdict |

### 8.2 Integration (`tests/test_cli_train.py`)

One test: `test_train_outcome_eval_runs_end_to_end` — runs `train outcome-eval` against a small fixture universe with a pre-populated fixture `history.sqlite`, asserts the report file is written and contains the verdict line.

### 8.3 Test fixtures

A fixture `history.sqlite` with ≥10 resolved outcomes across two degrees and at least 5 distinct `exit_date`s, **rebuilt per test session** via a `pytest` session-scoped fixture that exercises `record_snapshot` + `resolve_outcomes` against synthetic snapshot payloads. No committed binary — the fixture writes to a `tmp_path_factory` location.

## 9. Determinism + reproducibility

- `seed=42` default propagates into `fit_logistic` and `fit_platt` for both arms.
- The temporal split is sort-stable (`exit_date` then `(ticker, as_of)`) — same DB contents → same split.
- `OutcomeEvalResult.generated_at` is the only non-deterministic field. The markdown report timestamps with it but never compares against it.

## 10. Rollout

This work depends on PR #4 (`feat/enumerator-eval-cache`) merging because:
1. The eval-cache branch is the current local HEAD; this spec lives in `docs/` so it must commit somewhere clean.
2. After PR #4 merges, branch from updated `main` → `feat/outcome-training-eval-harness` → implement → PR.

If PR #4 is unmerged when implementation starts, the implementer should pause and surface the conflict rather than rebasing the eval-cache history under this work.

## 11. Future work (NOT in scope)

- Multi-window rolling evaluation (eval at quarter boundaries to see trend over time).
- Per-ticker breakdown for diagnostic drill-down.
- Auto-gating the production training pipeline on the verdict.
- A reverse arm: B' = synthetic + outcomes with outcome weight forced to 1.0 (no ramp). Quantifies the value of the ramp itself.

These are good follow-ups once the v1 harness shows whether outcome training is helping at all.

## 12. References

- CLAUDE.md §"Right-edge confirmation models" — outcome-driven retraining contract.
- `src/wave_alpha/right_edge/outcome_dataset.py` — dataset construction reused unchanged.
- `src/wave_alpha/right_edge/training.py:573-642` — `train_blended_final` reused unchanged.
- `src/wave_alpha/right_edge/promotion.py:66-103` — paired bootstrap Brier (pattern reused for delta CI).
- `src/wave_alpha/backtest/brier.py` — `brier_score`, `calibration_error`.
- `src/wave_alpha/cli/app.py:955-1129` — `train_right_edge` CLI surface (pattern mirrored for `train outcome-eval`).
