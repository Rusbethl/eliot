# Outcome-Training Evaluation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only CLI harness that empirically measures whether v0.10.0's outcome-driven right-edge training (`--use-outcomes`) improves model quality vs synthetic-only training, by training two arms and scoring both on a held-out temporal slice of realized outcomes.

**Architecture:** Add a new module `src/wave_alpha/right_edge/outcome_eval.py` that orchestrates: (a) temporal split of resolved outcomes by `exit_date`, (b) training arm A via the existing `walk_forward`, (c) training arm B via `train_blended_final` on the training-portion outcomes only, (d) scoring both on the same hold-out, (e) paired bootstrap Brier delta + per-degree ECE. Surface as `wave-alpha train outcome-eval` writing a markdown report. Reuses existing primitives — no new training paths.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `numpy`, `typer`, frozen dataclasses, ruff (E F I N UP B SIM RUF, line-length 100, E501 ignored). Domain conventions: prices `decimal.Decimal`, dates `datetime.date`, frozen Pydantic v2 for protocol models — but `OutcomeEvalConfig`/`ArmMetrics`/`OutcomeEvalResult` follow the existing `WalkForwardResult`/`OutcomeRow` style: `@dataclass(frozen=True)`.

**Spec:** `docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md`

---

## Pre-Task: Branch + baseline

**Files:**
- None (git operations only)

- [ ] **Step 1: Verify PR #4 has merged**

Run: `git fetch origin && git log --oneline origin/main -10`
Expected: see commit `perf(elliott): window-major enumerate_counts with per-window eval cache` (or its squash/merge equivalent) on `origin/main`. If absent, STOP and surface: "PR #4 has not merged yet — cannot start this work."

- [ ] **Step 2: Create branch from updated main**

Run:
```bash
git checkout main
git pull
git checkout -b feat/outcome-training-eval-harness
```
Expected: clean checkout, branch created.

- [ ] **Step 3: Baseline test suite**

Run: `uv run pytest -q`
Expected: all tests pass. If any fail, STOP and report — baseline must be green.

- [ ] **Step 4: Commit nothing yet, just verify baseline**

Run: `git status`
Expected: `nothing to commit, working tree clean`.

---

## Task 1: Extend `OutcomeRow` with `exit_date`

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_dataset.py` (lines ~45-62 for `OutcomeRow`, ~270-282 for `build_outcome_dataset` construction site)
- Modify: `tests/right_edge/test_outcome_dataset.py` (any direct `OutcomeRow(...)` constructions)
- Test: `tests/right_edge/test_outcome_dataset.py` (new test for exit_date population)

- [ ] **Step 1: Write the failing test**

Append to `tests/right_edge/test_outcome_dataset.py`:

```python
def test_build_outcome_dataset_populates_exit_date(
    tmp_path, fake_bar_provider, fixture_history_db_with_resolved_outcome
):
    """build_outcome_dataset must surface snapshot.outcome.exit_date on each
    constructed OutcomeRow. The temporal split logic in outcome_eval needs it."""
    from wave_alpha.right_edge.outcome_dataset import WeightSchedule, build_outcome_dataset

    db_path = fixture_history_db_with_resolved_outcome
    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=fake_bar_provider,
        degree=2,
        schedule=WeightSchedule(),
        n_synth_per_degree={2: 50},
    )
    assert ds.rows, "fixture must produce at least one row"
    for r in ds.rows:
        assert r.exit_date is not None, f"exit_date missing on row {r}"
        from datetime import date as _date
        assert isinstance(r.exit_date, _date)
```

This test depends on existing fixtures in `tests/right_edge/test_outcome_dataset.py`. If the existing test file lacks `fake_bar_provider` / `fixture_history_db_with_resolved_outcome` under those names, reuse whatever fixtures the file's existing `test_build_outcome_dataset_filters_to_resolved_rows` test uses — keep the same fixture style.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/right_edge/test_outcome_dataset.py::test_build_outcome_dataset_populates_exit_date -v`
Expected: FAIL with `AttributeError: 'OutcomeRow' object has no attribute 'exit_date'`.

- [ ] **Step 3: Add `exit_date` field to `OutcomeRow`**

In `src/wave_alpha/right_edge/outcome_dataset.py`, modify the `OutcomeRow` dataclass to add the field after `weight`:

```python
@dataclass(frozen=True)
class OutcomeRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    label: float  # 0.0 or 1.0
    weight: float  # populated by assign_weights
    exit_date: date  # populated by build_outcome_dataset from snapshot.outcome.exit_date
```

- [ ] **Step 4: Populate `exit_date` in `build_outcome_dataset`**

In the same file, find the `OutcomeRow(...)` construction at approximately line 273 inside `build_outcome_dataset`. It currently looks like:

```python
rows.append(
    OutcomeRow(
        ticker=ticker,
        as_of=as_of,
        degree=degree,
        features=outcome,
        label=label,
        weight=0.0,
    )
)
```

Replace with:

```python
assert snap.outcome is not None and snap.outcome.exit_date is not None, (
    "resolved snapshots must have outcome.exit_date; "
    f"got ticker={ticker} as_of={as_of} status={snap.outcome.status if snap.outcome else None}"
)
rows.append(
    OutcomeRow(
        ticker=ticker,
        as_of=as_of,
        degree=degree,
        features=outcome,
        label=label,
        weight=0.0,
        exit_date=snap.outcome.exit_date,
    )
)
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest tests/right_edge/test_outcome_dataset.py::test_build_outcome_dataset_populates_exit_date -v`
Expected: PASS.

- [ ] **Step 6: Run the full `tests/right_edge/test_outcome_dataset.py` file to catch fallout**

Run: `uv run pytest tests/right_edge/test_outcome_dataset.py -v`
Expected: all pass. If any of `test_outcome_dataset.py`'s existing tests fail (likely because they construct `OutcomeRow(...)` directly without `exit_date`), fix them by passing `exit_date=date(2024, 1, 1)` (or any valid date — value is irrelevant to those tests).

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -q`
Expected: all pass. Fix any cross-file fallout the same way (add `exit_date` to direct constructions).

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_dataset.py tests/right_edge/test_outcome_dataset.py
git commit -m "feat(right-edge): add exit_date field to OutcomeRow

Required by the outcome-training eval harness for temporal split logic.
Populated in build_outcome_dataset from snapshot.outcome.exit_date.
See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Data models for `outcome_eval`

**Files:**
- Create: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing test**

Create `tests/right_edge/test_outcome_eval.py`:

```python
"""Tests for the outcome-training evaluation harness.

See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.
"""

from datetime import date, datetime

import pytest


def test_outcome_eval_config_defaults():
    """OutcomeEvalConfig must have sensible defaults matching the spec."""
    from wave_alpha.right_edge.outcome_eval import OutcomeEvalConfig

    cfg = OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL", "MSFT"])
    assert cfg.quarters == 16
    assert cfg.fold_count == 4
    assert cfg.holdout_fraction == 0.20
    assert cfg.seed == 42
    assert cfg.with_fib is False
    # schedule must default to a WeightSchedule with n_target_per_degree=100
    assert cfg.schedule.n_target_per_degree == 100
    assert cfg.schedule.include_time_stops is True


def test_arm_metrics_construction():
    """ArmMetrics is a frozen dataclass carrying per-degree breakdowns."""
    from wave_alpha.right_edge.outcome_eval import ArmMetrics

    m = ArmMetrics(
        brier_overall=0.18,
        brier_per_degree={2: 0.15, 3: 0.21},
        ece_per_degree={2: 0.04, 3: 0.07},
        log_loss_overall=0.42,
        n_holdout_per_degree={2: 30, 3: 12},
        low_n_degrees=[3],
    )
    assert m.brier_overall == 0.18
    assert m.low_n_degrees == [3]
    # frozen: cannot mutate
    with pytest.raises(Exception):
        m.brier_overall = 0.5  # type: ignore[misc]


def test_outcome_eval_result_construction():
    """OutcomeEvalResult bundles config + counts + both arms + delta + verdict."""
    from wave_alpha.right_edge.outcome_eval import (
        ArmMetrics,
        OutcomeEvalConfig,
        OutcomeEvalResult,
    )

    arm = ArmMetrics(
        brier_overall=0.2,
        brier_per_degree={2: 0.2},
        ece_per_degree={2: 0.05},
        log_loss_overall=0.5,
        n_holdout_per_degree={2: 20},
        low_n_degrees=[],
    )
    res = OutcomeEvalResult(
        config=OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL"]),
        n_synth_rows=500,
        n_train_outcomes_per_degree={2: 80},
        n_holdout_outcomes_per_degree={2: 20},
        arm_a=arm,
        arm_b=arm,
        brier_delta_ci=(-0.01, 0.0, 0.01),
        verdict="no_effect",
        generated_at=datetime(2024, 12, 31, 12, 0, 0),
    )
    assert res.verdict == "no_effect"
    assert res.brier_delta_ci == (-0.01, 0.0, 0.01)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wave_alpha.right_edge.outcome_eval'`.

- [ ] **Step 3: Create `outcome_eval.py` with data models**

Create `src/wave_alpha/right_edge/outcome_eval.py`:

```python
"""Outcome-training evaluation harness.

Empirically measures whether outcome-driven training (--use-outcomes) improves
right-edge model quality vs synthetic-only training. Splits resolved outcomes
temporally by exit_date, trains two arms on the training portion, scores both
arms on the hold-out portion, reports per-degree Brier/ECE/log-loss and a
paired bootstrap Brier delta CI.

See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from wave_alpha.right_edge.outcome_dataset import WeightSchedule

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): outcome-eval data models

Adds OutcomeEvalConfig, ArmMetrics, OutcomeEvalResult dataclasses for the
outcome-training evaluation harness.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Temporal split

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def _row(*, ticker: str = "AAPL", as_of_day: int = 1, degree: int = 2, exit_day: int):
    """Build a minimal OutcomeRow for split tests. features can be a stub —
    the split function never touches them."""
    from datetime import date

    from wave_alpha.right_edge.outcome_dataset import OutcomeRow

    return OutcomeRow(
        ticker=ticker,
        as_of=date(2024, 1, as_of_day),
        degree=degree,
        features=object(),  # type: ignore[arg-type]  # stub — split never touches features
        label=1.0,
        weight=0.0,
        exit_date=date(2024, 1, exit_day),
    )


def test_split_outcomes_by_exit_date_holds_out_most_recent_fraction():
    """holdout = most-recent fraction by exit_date."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    rows = [_row(exit_day=d) for d in range(1, 11)]  # 10 rows, exit days 1..10
    train, holdout = _split_outcomes_temporal(rows, fraction=0.20)
    # 20% of 10 = 2; most-recent 2 (exit days 9, 10) in holdout
    assert len(train) == 8
    assert len(holdout) == 2
    holdout_exit_days = sorted(r.exit_date.day for r in holdout)
    assert holdout_exit_days == [9, 10]


def test_split_outcomes_ties_at_boundary_go_to_train():
    """When the boundary row has the same exit_date as the next training row,
    all tied rows go to the training side."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    # 10 rows; days [1,2,3,4,5,5,5,6,7,8]. Most-recent 20% = 2 rows.
    # By naive count it'd be 7,8 → holdout. But the boundary at day 7 has no
    # tie, so this case keeps train=8, holdout=2. To exercise ties:
    # 10 rows; days [1,2,3,4,5,5,5,5,5,8]. 20% = 2 → naive holdout = [5,8].
    # All day-5 rows must be on the train side; only day 8 in holdout.
    from datetime import date as _date

    from wave_alpha.right_edge.outcome_dataset import OutcomeRow

    def mk(d):
        return OutcomeRow(
            ticker="AAPL",
            as_of=_date(2024, 1, 1),
            degree=2,
            features=object(),  # type: ignore[arg-type]
            label=1.0,
            weight=0.0,
            exit_date=_date(2024, 1, d),
        )

    rows = [mk(d) for d in [1, 2, 3, 4, 5, 5, 5, 5, 5, 8]]
    train, holdout = _split_outcomes_temporal(rows, fraction=0.20)
    assert {r.exit_date.day for r in holdout} == {8}
    assert all(r.exit_date.day <= 5 for r in train)
    assert len(train) + len(holdout) == 10


def test_split_outcomes_zero_holdout_raises():
    """fraction × N < 1 must raise — meaningless report otherwise."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    rows = [_row(exit_day=1), _row(exit_day=2)]  # N=2, fraction=0.20 → floor(0.4)=0
    with pytest.raises(ValueError, match="holdout"):
        _split_outcomes_temporal(rows, fraction=0.20)


def test_split_outcomes_empty_raises():
    """Empty input is a hard error."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    with pytest.raises(ValueError):
        _split_outcomes_temporal([], fraction=0.20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k split`
Expected: 4 FAIL with `ImportError` or `AttributeError`.

- [ ] **Step 3: Implement `_split_outcomes_temporal`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
from wave_alpha.right_edge.outcome_dataset import OutcomeRow


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
            f"_split_outcomes_temporal: fraction={fraction} × N={n} < 1 → "
            "zero holdout rows; pick a larger fraction or gather more outcomes"
        )

    sorted_rows = sorted(rows, key=lambda r: (r.exit_date, r.ticker, r.as_of))
    # Tentative boundary: include the most-recent n_holdout_target rows.
    boundary_idx = n - n_holdout_target
    boundary_date = sorted_rows[boundary_idx].exit_date
    # Push any rows at boundary_date forward into the training side.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k split`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): _split_outcomes_temporal with tie-pushing

Implements the temporal split described in spec §4: rank outcomes by
exit_date, hold-out most-recent fraction, boundary ties stay on the
training side. Empty input and zero-holdout cases raise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Per-arm scoring (Brier, ECE, log-loss)

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def test_score_arm_from_predictions_brier_matches_brier_score():
    """ArmMetrics.brier_overall must equal backtest.brier.brier_score on the
    same (proba, label) pairs."""
    from wave_alpha.backtest.brier import brier_score
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    # 10 holdout predictions: 5 confident-correct, 5 confident-wrong → Brier ≈ 0.5
    preds = [
        (0.9, 1, 2), (0.9, 1, 2), (0.1, 0, 2), (0.1, 0, 2), (0.9, 1, 2),
        (0.1, 0, 2), (0.9, 0, 2), (0.1, 1, 2), (0.9, 0, 2), (0.1, 1, 2),
    ]  # (calibrated_proba, label, degree)
    metrics = _score_arm_from_predictions(preds, low_n_threshold=5)
    expected = brier_score([(p, lab) for p, lab, _ in preds])
    assert abs(metrics.brier_overall - expected) < 1e-9


def test_score_arm_from_predictions_per_degree_breakdown():
    """brier_per_degree, ece_per_degree, n_holdout_per_degree all keyed by
    degrees present in input."""
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    preds = [
        (0.8, 1, 2), (0.2, 0, 2), (0.6, 1, 2),
        (0.9, 1, 3), (0.1, 0, 3),
    ]
    m = _score_arm_from_predictions(preds, low_n_threshold=20)
    assert set(m.brier_per_degree) == {2, 3}
    assert set(m.ece_per_degree) == {2, 3}
    assert m.n_holdout_per_degree == {2: 3, 3: 2}


def test_score_arm_low_n_flag():
    """Degrees with row count < threshold appear in low_n_degrees."""
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    preds = [(0.5, 1, 2), (0.5, 0, 2), (0.5, 1, 3)]  # d=2 has 2, d=3 has 1
    m = _score_arm_from_predictions(preds, low_n_threshold=20)
    assert set(m.low_n_degrees) == {2, 3}

    m_strict = _score_arm_from_predictions(preds, low_n_threshold=2)
    assert m_strict.low_n_degrees == [3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k score_arm`
Expected: FAIL with `ImportError` for `_score_arm_from_predictions`.

- [ ] **Step 3: Implement `_score_arm_from_predictions`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
import math
from collections import defaultdict


def _score_arm_from_predictions(
    preds: list[tuple[float, int, int]],
    *,
    low_n_threshold: int = 20,
) -> ArmMetrics:
    """Compute per-arm metrics from pre-scored (calibrated_proba, label, degree)
    triples. Pure function — independent of the arm's logistic/Platt models.

    Reuses `backtest.brier.brier_score` and `backtest.brier.calibration_error`
    so metrics are identical to the existing promotion gate's primitives.
    """
    from wave_alpha.backtest.brier import brier_score, calibration_error

    if not preds:
        raise ValueError("_score_arm_from_predictions: empty preds")

    # Overall Brier on (proba, label) pairs.
    pl_pairs = [(p, lab) for p, lab, _ in preds]
    brier_overall = brier_score(pl_pairs)

    # Overall log-loss with clipping (matches calibration_error's conventions).
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
```

If `brier_score` or `calibration_error` have different signatures than `(pairs, n_bins=...)`, adapt the calls — the project's existing `promotion.py` uses these same primitives at `right_edge/promotion.py:66-103` and `:144`. Mirror those call sites.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k score_arm`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): _score_arm_from_predictions (Brier + ECE + log-loss)

Per-arm scoring on (calibrated_proba, label, degree) triples. Pure
function — reuses backtest.brier primitives for parity with the
existing promotion gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Paired bootstrap Brier delta CI

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def test_brier_delta_ci_deterministic_seed():
    """Same (preds_a, preds_b, seed) → byte-identical (lo, median, hi)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    preds_a = [(0.9, 1, 2), (0.1, 0, 2), (0.6, 1, 2), (0.4, 0, 2)] * 5
    preds_b = [(0.8, 1, 2), (0.2, 0, 2), (0.7, 1, 2), (0.3, 0, 2)] * 5
    ci1 = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    ci2 = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    assert ci1 == ci2
    assert len(ci1) == 3
    lo, mid, hi = ci1
    assert lo <= mid <= hi


def test_brier_delta_ci_b_better_when_b_more_confident_correct():
    """If B has lower per-row Brier than A on every row, the median delta
    must be negative (B − A < 0 → B improves)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    preds_a = [(0.6, 1, 2)] * 30  # per-row Brier = (1 - 0.6)^2 = 0.16
    preds_b = [(0.9, 1, 2)] * 30  # per-row Brier = (1 - 0.9)^2 = 0.01
    lo, mid, hi = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    assert mid < 0
    assert hi < 0  # CI excludes zero — B strictly improves


def test_brier_delta_ci_pair_length_mismatch_raises():
    """preds_a and preds_b must have equal length (paired)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    with pytest.raises(ValueError, match="length"):
        _brier_delta_ci([(0.5, 1, 2)], [(0.5, 1, 2), (0.5, 0, 2)], n_resamples=10, seed=42)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k brier_delta_ci`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `_brier_delta_ci`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
import numpy as np


def _brier_delta_ci(
    preds_a: list[tuple[float, int, int]],
    preds_b: list[tuple[float, int, int]],
    *,
    n_resamples: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Paired bootstrap 95% CI on (Brier_B − Brier_A).

    `preds_a[i]` and `preds_b[i]` MUST be the same hold-out row scored by each
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k brier_delta_ci`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): paired bootstrap CI on Brier delta (B - A)

Mirrors the resampling structure of promotion._paired_bootstrap_brier
but operates on a fixed paired hold-out (both arms scored on the same
rows) rather than two independent samples.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Verdict logic

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def test_verdict_improves_when_ci_strictly_negative():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((-0.05, -0.03, -0.01)) == "improves"


def test_verdict_degrades_when_ci_strictly_positive():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((0.01, 0.03, 0.05)) == "degrades"


def test_verdict_no_effect_when_ci_straddles_zero():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((-0.02, 0.0, 0.02)) == "no_effect"
    assert _verdict_from_ci((-0.02, -0.01, 0.01)) == "no_effect"
    assert _verdict_from_ci((-0.01, 0.01, 0.02)) == "no_effect"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k verdict`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `_verdict_from_ci`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
def _verdict_from_ci(ci: tuple[float, float, float]) -> VerdictLiteral:
    """Map a (lo, median, hi) CI on B − A into a verdict.

    Negative delta = B (outcome-blended) has lower Brier = improvement.
    """
    lo, _mid, hi = ci
    if hi < 0:
        return "improves"
    if lo > 0:
        return "degrades"
    return "no_effect"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v -k verdict`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): verdict from Brier delta CI

improves / no_effect / degrades based on whether the 95% CI excludes
zero on the appropriate side.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `run_outcome_eval` orchestration

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def test_run_outcome_eval_deterministic_end_to_end(
    tmp_path, fake_bar_provider_with_universe, fixture_history_db_multiple_outcomes
):
    """Same (cfg, fixtures) → byte-identical OutcomeEvalResult (modulo
    generated_at).

    Uses fixtures from tests/right_edge/test_outcome_dataset.py — if those
    fixtures live in tests/right_edge/conftest.py, this test will pick them up
    automatically. If they live in the test_outcome_dataset.py file itself,
    promote them to conftest.py as part of this task.
    """
    from datetime import date

    from wave_alpha.right_edge.outcome_eval import OutcomeEvalConfig, run_outcome_eval

    cfg = OutcomeEvalConfig(
        end=date(2024, 12, 31),
        universe=["AAPL", "MSFT"],
        quarters=4,        # smaller for fixture speed
        fold_count=2,
        holdout_fraction=0.30,
        seed=42,
    )
    # rows_by_quarter + folds: built by helper that mirrors _load_rows_by_quarter
    rows_by_quarter, folds = _build_fixture_rows_and_folds(cfg, fake_bar_provider_with_universe)

    result1 = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=fake_bar_provider_with_universe,
        history_db_path=fixture_history_db_multiple_outcomes,
    )
    result2 = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=fake_bar_provider_with_universe,
        history_db_path=fixture_history_db_multiple_outcomes,
    )
    # Everything must match except generated_at.
    assert result1.verdict == result2.verdict
    assert result1.brier_delta_ci == result2.brier_delta_ci
    assert result1.arm_a == result2.arm_a
    assert result1.arm_b == result2.arm_b
    assert result1.n_synth_rows == result2.n_synth_rows


def _build_fixture_rows_and_folds(cfg, provider):
    """Build a tiny rows_by_quarter + folds for the orchestrator test.

    If the implementer extracts _load_rows_by_quarter from cli/app.py into a
    public helper (right_edge/training.py), call that. Otherwise inline a
    minimal version that produces 2-3 quarters of synthetic rows."""
    from wave_alpha.cli.app import _load_rows_by_quarter

    _start, _end, folds, rows_by_quarter = _load_rows_by_quarter(
        universe_file=None,  # caller will need to adapt — see below
        start_date=f"{cfg.end.year - 2}-01-01",
        end_date=cfg.end.isoformat(),
        provider=provider,
    )
    return rows_by_quarter, folds
```

**Note on the fixture helper:** `_load_rows_by_quarter` in `cli/app.py` takes a `universe_file: Path` and reads it. For test isolation, the cleanest path is to either:
(a) Refactor `_load_rows_by_quarter` to accept either `universe_file: Path` or `universe: list[str]`, OR
(b) Have the test write a temporary universe file (`tmp_path / "universe.txt"`) and pass its path.

Option (b) is less invasive — pick (b) for this task. Adjust `_build_fixture_rows_and_folds` to write the universe file from `cfg.universe`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py::test_run_outcome_eval_deterministic_end_to_end -v`
Expected: FAIL with `ImportError: cannot import name 'run_outcome_eval'`.

- [ ] **Step 3: Implement `run_outcome_eval`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
from datetime import datetime
from pathlib import Path

from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset,
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
    from collections import Counter

    # --- Arm A: synthetic-only walk-forward ---
    wf = walk_forward(
        rows_by_quarter,
        folds=folds,
        seed=cfg.seed,
        with_fib=cfg.with_fib,
    )

    # --- Outcome dataset (UNWEIGHTED — we split first, then weight) ---
    # n_synth_per_degree passed for build_outcome_dataset's filter only; weights
    # we'll overwrite below.
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

    # --- assign_weights AFTER split: per-degree ramp uses training count ---
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
            out.append((cal, int(round(row.label)), row.degree))
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py::test_run_outcome_eval_deterministic_end_to_end -v`
Expected: PASS. If it fails because the fixture `_load_rows_by_quarter` requires a real `data/universe_curated.txt`, follow the "Option (b)" guidance from Step 1's note: write `tmp_path / "universe.txt"` listing `cfg.universe` and pass that path.

- [ ] **Step 5: Run the full new-file test suite**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py -v`
Expected: all tests PASS (3 from Task 2 + 4 from Task 3 + 3 from Task 4 + 3 from Task 5 + 3 from Task 6 + 1 from this task = 17).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): run_outcome_eval orchestration

Wires arm A (walk_forward synthetic-only) + arm B (train_blended_final
with training-portion outcomes only) + holdout scoring + paired
bootstrap delta CI + verdict. assign_weights runs AFTER the split so
the per-degree ramp reflects the training count.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Markdown report formatter

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_eval.py`
- Test: `tests/right_edge/test_outcome_eval.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/right_edge/test_outcome_eval.py`:

```python
def test_format_markdown_report_includes_required_sections():
    """Report must contain: config block, row counts, arm A metrics,
    arm B metrics, delta CI, verdict line."""
    from datetime import date, datetime

    from wave_alpha.right_edge.outcome_eval import (
        ArmMetrics,
        OutcomeEvalConfig,
        OutcomeEvalResult,
        format_markdown_report,
    )

    arm = ArmMetrics(
        brier_overall=0.18,
        brier_per_degree={2: 0.15, 3: 0.21},
        ece_per_degree={2: 0.04, 3: 0.07},
        log_loss_overall=0.42,
        n_holdout_per_degree={2: 30, 3: 12},
        low_n_degrees=[3],
    )
    res = OutcomeEvalResult(
        config=OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL", "MSFT"]),
        n_synth_rows=500,
        n_train_outcomes_per_degree={2: 80, 3: 15},
        n_holdout_outcomes_per_degree={2: 30, 3: 12},
        arm_a=arm,
        arm_b=arm,
        brier_delta_ci=(-0.02, -0.01, 0.005),
        verdict="no_effect",
        generated_at=datetime(2024, 12, 31, 12, 0, 0),
    )
    md = format_markdown_report(res)
    assert "# Outcome-Training Evaluation" in md
    assert "## Verdict" in md
    assert "no_effect" in md.lower()
    assert "Arm A (synthetic-only)" in md
    assert "Arm B (synthetic + outcomes)" in md
    assert "## Δ Brier (B − A)" in md or "Delta Brier" in md
    assert "2024-12-31" in md  # end date
    assert "low_n" in md.lower()  # the [3] low_n flag must surface
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py::test_format_markdown_report_includes_required_sections -v`
Expected: FAIL with `ImportError: cannot import name 'format_markdown_report'`.

- [ ] **Step 3: Implement `format_markdown_report`**

Append to `src/wave_alpha/right_edge/outcome_eval.py`:

```python
def format_markdown_report(result: OutcomeEvalResult) -> str:
    """Render an OutcomeEvalResult as a human-readable markdown report.

    Layout (spec §5 / §6):
      # Outcome-Training Evaluation
      ## Config
      ## Row counts
      ## Arm A (synthetic-only)
      ## Arm B (synthetic + outcomes)
      ## Δ Brier (B − A)
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
        low_n = (
            f"\n- low_n degrees (<20 rows): {m.low_n_degrees}" if m.low_n_degrees else ""
        )
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
        f"\n## Row counts\n\n",
        f"- Synthetic rows: {result.n_synth_rows}\n",
        f"- Training outcomes per degree: {train_counts}\n",
        f"- Holdout outcomes per degree: {holdout_counts}\n",
        "\n",
        _arm_block("Arm A (synthetic-only)", result.arm_a),
        "\n",
        _arm_block("Arm B (synthetic + outcomes)", result.arm_b),
        "\n",
        f"## Δ Brier (B − A)\n\n",
        f"- 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}] (lo, median, hi)\n",
        f"- Negative = outcome training improves Brier on the hold-out.\n",
        "\n",
        f"## Verdict\n\n",
        f"**{result.verdict}**\n\n",
        f"- `improves`: 95% CI strictly below 0 (outcome training helps).\n",
        f"- `no_effect`: CI straddles 0 (no measurable effect).\n",
        f"- `degrades`: 95% CI strictly above 0 (outcome training hurts).\n",
        "\n",
        f"---\n_Generated: {result.generated_at.isoformat()}_\n",
    ]
    return "".join(parts)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/right_edge/test_outcome_eval.py::test_format_markdown_report_includes_required_sections -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_eval.py tests/right_edge/test_outcome_eval.py
git commit -m "feat(right-edge): format_markdown_report for outcome-eval result

Renders OutcomeEvalResult as a markdown report with config, row
counts, per-arm metrics, delta CI, and verdict sections.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: CLI command `wave-alpha train outcome-eval`

**Files:**
- Modify: `src/wave_alpha/cli/app.py` (add new command under `train_app`)
- Test: `tests/test_cli_train.py`

- [ ] **Step 1: Write the failing integration test**

Append to `tests/test_cli_train.py` (mirror the style of `test_train_right_edge_runs_end_to_end`):

```python
def test_train_outcome_eval_runs_end_to_end(
    tmp_path,
    monkeypatch,
    fake_bar_provider_with_universe,
    fixture_history_db_multiple_outcomes,
):
    """`wave-alpha train outcome-eval --end <date> --universe <file>` writes
    a markdown report to --out containing the verdict and per-arm metrics."""
    from typer.testing import CliRunner

    from wave_alpha.cli.app import app

    # Provider injection — mirror what test_train_right_edge does. Likely a
    # monkeypatch of cli.app._default_provider.
    monkeypatch.setattr(
        "wave_alpha.cli.app._default_provider",
        lambda: fake_bar_provider_with_universe,
    )
    # History DB path injection.
    monkeypatch.setattr(
        "wave_alpha.cli.app._DEFAULT_DATA_DIR",
        fixture_history_db_multiple_outcomes.parent,
    )

    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\nMSFT\n")
    out = tmp_path / "outcome_eval.md"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "train", "outcome-eval",
            "--end", "2024-12-31",
            "--universe", str(universe_file),
            "--quarters", "4",
            "--folds", "2",
            "--holdout-fraction", "0.30",
            "--seed", "42",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert out.exists(), "report file was not written"
    body = out.read_text()
    assert "# Outcome-Training Evaluation" in body
    assert "## Verdict" in body
    assert any(v in body.lower() for v in ("improves", "no_effect", "degrades"))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli_train.py::test_train_outcome_eval_runs_end_to_end -v`
Expected: FAIL with `No such command 'outcome-eval'`.

- [ ] **Step 3: Implement the CLI command**

In `src/wave_alpha/cli/app.py`, after the existing `train_right_edge` command (around line 1129, before `train_calibrated_heuristic`), add:

```python
@train_app.command("outcome-eval")
def train_outcome_eval(
    universe_file: Path = typer.Option(  # noqa: B008
        Path("data/universe_curated.txt"),
        "--universe",
        help="Path to ticker universe file (one symbol per line; # = comment).",
    ),
    start_date: str = typer.Option(
        "2021-01-01", "--start", help="Training start date YYYY-MM-DD."
    ),
    end_date: str = typer.Option(..., "--end", help="Training end date YYYY-MM-DD (required)."),
    quarters: int | None = typer.Option(
        None, "--quarters", help="Override quarters; default derived from start/end."
    ),
    fold_count: int | None = typer.Option(
        None, "--folds", help="Override fold count; default 4."
    ),
    holdout_fraction: float = typer.Option(
        0.20, "--holdout-fraction", help="Fraction of resolved outcomes held out by exit_date."
    ),
    seed: int = typer.Option(42, "--seed", help="RNG seed (logistic fit, bootstrap)."),
    with_fib_features: bool = typer.Option(False, "--with-fib-features"),
    outcome_weight_target: int | None = typer.Option(
        None,
        "--outcome-weight-target",
        help="Override n_target_per_degree (default reads from config.yaml).",
    ),
    include_time_stops: bool = typer.Option(
        True,
        "--include-time-stops/--no-time-stops",
        help="Whether time_stop outcomes contribute (default reads from config.yaml).",
    ),
    out_path: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Markdown report destination. Default: reports/outcome_eval_<end>.md."
    ),
) -> None:
    """Evaluate whether outcome-driven training improves right-edge model quality.

    Read-only: trains two arms (synthetic-only vs synthetic+outcomes), scores
    both on a temporally held-out slice of realized outcomes, writes a
    markdown report with per-degree Brier/ECE and a paired bootstrap Δ Brier CI.

    Does NOT write model artifacts.
    """
    from wave_alpha.right_edge.outcome_dataset import WeightSchedule
    from wave_alpha.right_edge.outcome_eval import (
        OutcomeEvalConfig,
        format_markdown_report,
        run_outcome_eval,
    )

    provider = _default_provider()
    _start, _end, folds, rows_by_quarter = _load_rows_by_quarter(
        universe_file=universe_file,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )

    # Schedule: config defaults, CLI overrides win.
    cfg_outcome = load_config().right_edge.outcome_training
    schedule = WeightSchedule(
        n_target_per_degree=outcome_weight_target
        if outcome_weight_target is not None
        else cfg_outcome.n_target_per_degree,
        include_time_stops=include_time_stops and cfg_outcome.include_time_stops,
        time_stop_label=cfg_outcome.time_stop_label,
    )

    universe = [
        line.strip()
        for line in universe_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    cfg = OutcomeEvalConfig(
        end=_end,
        universe=universe,
        quarters=quarters if quarters is not None else len(rows_by_quarter),
        fold_count=fold_count if fold_count is not None else len(folds),
        holdout_fraction=holdout_fraction,
        seed=seed,
        with_fib=with_fib_features,
        schedule=schedule,
    )

    typer.echo("Running outcome-training evaluation...")
    typer.echo(f"  synthetic rows by quarter: {sum(len(rs) for rs in rows_by_quarter.values())}")
    db_path = _DEFAULT_DATA_DIR / "history.sqlite"
    result = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=provider,
        history_db_path=db_path,
    )

    typer.echo(f"\nVerdict: {result.verdict}")
    lo, mid, hi = result.brier_delta_ci
    typer.echo(f"  Δ Brier 95% CI (B − A): [{lo:.4f}, {mid:.4f}, {hi:.4f}]")
    typer.echo(f"  Arm A overall Brier: {result.arm_a.brier_overall:.4f}")
    typer.echo(f"  Arm B overall Brier: {result.arm_b.brier_overall:.4f}")

    if out_path is None:
        out_path = Path("reports") / f"outcome_eval_{_end.isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_markdown_report(result))
    typer.echo(f"\nReport written to {out_path}")
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `uv run pytest tests/test_cli_train.py::test_train_outcome_eval_runs_end_to_end -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Smoke-test the CLI manually**

Run: `uv run wave-alpha train outcome-eval --help`
Expected: help text rendered, shows all options.

Run (only if `~/.wave_alpha/history.sqlite` has ≥10 resolved outcomes):
```bash
uv run wave-alpha train outcome-eval --end 2024-12-31 --universe data/universe_curated.txt
```
Expected: report written to `reports/outcome_eval_2024-12-31.md`. If history is empty, the command should fail with a clear error from `_split_outcomes_temporal` — that's correct behaviour, not a bug.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_train.py
git commit -m "feat(cli): wave-alpha train outcome-eval

New read-only CLI command that empirically measures whether outcome-
driven right-edge training (--use-outcomes) actually improves model
quality. Writes a markdown report to reports/outcome_eval_<end>.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Final verification

**Files:**
- All touched files

- [ ] **Step 1: Run ruff lint**

Run: `uv run ruff check .`
Expected: clean. Fix any new violations introduced by this work.

- [ ] **Step 2: Run ruff format**

Run: `uv run ruff format .`
Expected: 0 files reformatted (we should be clean). If files reformat, commit:

```bash
git add -u
git commit -m "style: ruff format" --allow-empty
```

- [ ] **Step 3: Run the full test suite once more**

Run: `uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Refresh GitNexus index**

Run: `npx gitnexus analyze`
Expected: completes without error. Note: per CLAUDE.md, run impact analysis before editing existing symbols — by this point edits are done, but the index refresh keeps future work safe.

- [ ] **Step 5: Verify report fixture writes**

If `~/.wave_alpha/history.sqlite` is populated with real outcomes, do one manual end-to-end run and inspect the report:

```bash
uv run wave-alpha train outcome-eval --end 2024-12-31 --universe data/universe_curated.txt --out /tmp/oe.md
cat /tmp/oe.md
```

Expected: report includes all required sections (config, row counts, both arms, Δ Brier CI, verdict). If no production history is available, skip this step — the integration test in Task 9 already covered the code path.

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin feat/outcome-training-eval-harness
gh pr create --title "feat: outcome-training evaluation harness" --body "$(cat <<'EOF'
## Summary
- New `wave-alpha train outcome-eval` read-only CLI command that empirically measures whether v0.10.0's `--use-outcomes` actually improves right-edge model quality.
- Splits resolved outcomes temporally by `exit_date` (holdout = most-recent 20%), trains two arms (synthetic-only vs synthetic+outcomes), scores both on the same hold-out, reports per-degree Brier/ECE/log-loss and a paired bootstrap Δ Brier 95% CI.
- Adds `exit_date` field to `OutcomeRow` (required by the temporal split).
- No changes to existing training, promotion, or history code paths.

## Test plan
- [x] 17 new unit tests in `tests/right_edge/test_outcome_eval.py` covering data models, temporal split (incl. tie-pushing + zero-holdout error), per-arm scoring, paired bootstrap, verdict, end-to-end determinism, markdown report.
- [x] 1 new integration test in `tests/test_cli_train.py` exercising the full CLI.
- [x] `OutcomeRow.exit_date` field surfaces correctly in `build_outcome_dataset` (new test in `tests/right_edge/test_outcome_dataset.py`).
- [x] Full suite passes (`uv run pytest -q`).
- [x] Ruff lint + format clean.

## Out of scope (explicit non-goals — see spec §3)
- Multi-window rolling evaluation.
- Auto-gating production training on the verdict.
- New model types / training modes.

Spec: `docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md`
Plan: `docs/superpowers/plans/2026-05-11-outcome-training-eval-harness.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL returned.

---

## Summary

| Task | Changes | Tests |
|---|---|---|
| Pre | Branch off main from PR #4 | — |
| 1 | `OutcomeRow.exit_date` field | 1 new + fallout fixes |
| 2 | Data models | 3 |
| 3 | Temporal split | 4 |
| 4 | Per-arm scoring | 3 |
| 5 | Paired bootstrap CI | 3 |
| 6 | Verdict | 3 |
| 7 | `run_outcome_eval` orchestration | 1 |
| 8 | Markdown report | 1 |
| 9 | CLI command | 1 |
| 10 | Lint + format + GitNexus + PR | — |

**Total new tests:** 19. **Total commits:** 9 (one per task plus optional style commit).

**Critical correctness lock:** §5 weight-assignment order — `assign_weights` runs **after** the temporal split, on training-portion outcomes only. The per-degree ramp must reflect the row count a real deployment training moment would see, not the full pre-split count. Violating this would silently overweight the outcome arm and skew the comparison toward arm B.
