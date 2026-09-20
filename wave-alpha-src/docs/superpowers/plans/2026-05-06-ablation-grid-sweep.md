# Ablation Grid Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tiered ablation grid harness on top of `run_trade_layer`, run a 16-cell Phase 1 screening sweep, and conditionally run a 32-cell Phase 2 validation sweep — producing per-cell artifacts plus a cross-cell summary identifying the recommended `TradeRules` defaults.

**Architecture:** New `src/wave_alpha/backtest/grid.py` module wraps `run_trade_layer` with a `GridConfig` cartesian-product iterator and a `GridResult` aggregator. New `grid_report.py` renders the cross-cell markdown summary. CLI subcommand `wave-alpha backtest grid` ties it together. All numerical correctness is delegated to the existing trade-sim layer; the grid is pure orchestration.

**Tech Stack:** Python 3.11, `dataclasses` (frozen for inputs, mutable for outputs — mirrors `BacktestConfig` / `TradeLayerResult` conventions), `pydantic` v2 for `TradeRules` (no schema changes), `pyyaml` for grid configs, `pytest`, `typer` for CLI. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-06-ablation-grid-design.md`

**Branch:** `feat/ablation-grid-spec` (already created at the spec commit `7462986`).

---

## File structure

**New:**
- `src/wave_alpha/backtest/grid.py` — `GridAxis`, `GridConfig`, `GridCell`, `GridResult`, `run_grid(...)`, `phase1_trigger(...)`. Single file, ~250 LOC budget. One responsibility: orchestrate cells over `run_trade_layer` and aggregate.
- `src/wave_alpha/backtest/grid_report.py` — `render_grid_markdown(result, *, config) -> str`. ~150 LOC budget.
- `configs/grid_phase1.yaml` — Phase 1 axis definitions (4×2×2=16 cells).
- `configs/grid_phase2.yaml` — Phase 2 axis definitions (2×2×2×4=32 cells, with placeholder `direction_modes`).
- `tests/backtest/test_grid.py` — orchestration + aggregation tests.

**Modified:**
- `src/wave_alpha/cli/app.py` — add `backtest grid` subcommand (lazy imports per existing convention).
- `pyproject.toml`, `src/wave_alpha/__init__.py`, `uv.lock` — version bump to v0.5.6 (decided at merge time per spec §9 S4 from prior specs).
- `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` (this branch's spec) — fill §6.1, §6.2 post-Phase-1; fill §6.3, §6.4 post-Phase-2.

**Not modified:**
- `src/wave_alpha/backtest/runner.py` (`run_trade_layer` is the unchanged orchestration point).
- `src/wave_alpha/backtest/trade_layer.py` — engine numerics unchanged.
- `src/wave_alpha/trades/derive.py`, `src/wave_alpha/pipeline.py` — no engine changes.

---

## Task 1: `GridAxis` + `GridConfig` + cartesian-product iteration

**Files:**
- Create: `src/wave_alpha/backtest/grid.py`
- Create: `tests/backtest/test_grid.py`

- [ ] **Step 1: Write failing test for `GridConfig.cells()` shape**

Create `tests/backtest/test_grid.py`:

```python
from __future__ import annotations

from wave_alpha.backtest.grid import GridAxis, GridConfig


def test_grid_config_cartesian_product_shape() -> None:
    """GridConfig.cells() yields the cartesian product of axis levels.
    A 4x2x2 axes config produces 16 cells."""
    config = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55, 0.65]),
            GridAxis(name="direction_modes", levels=[["long"], ["long", "short"]]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={"stop_source": "count_invalidation", "target_source": "fib_extension"},
    )
    cells = list(config.cells())
    assert len(cells) == 16


def test_grid_config_2x2x2x4_phase2_shape() -> None:
    """Phase 2 grid: 2x2x2x4 = 32 cells."""
    config = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="stop_source", levels=["count_invalidation", "atr_multiple"]),
            GridAxis(name="target_source", levels=["fib_extension", "fixed_R"]),
            GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55, 0.65]),
        ],
        fixed={"direction_modes": ["long"], "target_R": 2.0},
    )
    cells = list(config.cells())
    assert len(cells) == 32
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_grid.py -xvs
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.backtest.grid'`.

- [ ] **Step 3: Create the grid module with axes + cartesian product**

Create `src/wave_alpha/backtest/grid.py`:

```python
"""Ablation grid harness: cartesian-product TradeRules sweeps over
run_trade_layer.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterator


@dataclass(frozen=True)
class GridAxis:
    """One sweep dimension. `levels` are the values to sweep; each level
    must be a TradeRules-compatible value for the field named `name`."""

    name: str
    levels: list[Any]


@dataclass(frozen=True)
class GridConfig:
    """Declarative grid definition. `axes` are swept; `fixed` are held
    constant across all cells. The cartesian product of axis levels
    produces the grid's cells."""

    axes: list[GridAxis]
    fixed: dict[str, Any] = field(default_factory=dict)

    def cells(self) -> Iterator[GridCell]:
        """Yield GridCell instances in row-major axis order."""
        level_lists = [axis.levels for axis in self.axes]
        for combo in product(*level_lists):
            axis_values = {
                axis.name: level for axis, level in zip(self.axes, combo, strict=True)
            }
            yield GridCell(axis_values=axis_values, fixed=dict(self.fixed))


@dataclass(frozen=True)
class GridCell:
    """One sweep cell: combined axis values + fixed values. `cell_id`
    is deterministic from axis values for reproducible artifact paths."""

    axis_values: dict[str, Any]
    fixed: dict[str, Any]

    @property
    def cell_id(self) -> str:
        """Deterministic ID from axis values, joined by underscores.

        Example: entry-immediate_dir-long_minconf-0.45
        Lists are joined with hyphens; floats keep their string repr.
        """
        parts: list[str] = []
        for name, value in self.axis_values.items():
            short_name = _short_axis_name(name)
            short_value = _short_axis_value(value)
            parts.append(f"{short_name}-{short_value}")
        return "_".join(parts)

    def trade_rules_kwargs(self) -> dict[str, Any]:
        """Merge axis_values + fixed into a TradeRules() kwargs dict."""
        return {**self.fixed, **self.axis_values}


_AXIS_NAME_SHORT = {
    "entry_trigger": "entry",
    "stop_source": "stop",
    "target_source": "target",
    "direction_modes": "dir",
    "min_confidence": "minconf",
}


def _short_axis_name(name: str) -> str:
    return _AXIS_NAME_SHORT.get(name, name)


def _short_axis_value(value: Any) -> str:
    if isinstance(value, list):
        if value == ["long"]:
            return "long"
        if value == ["long", "short"]:
            return "both"
        return "-".join(str(v) for v in value)
    if isinstance(value, str):
        return value.replace("_", "-")
    return str(value)
```

- [ ] **Step 4: Run the failing tests to verify they pass**

```bash
uv run pytest tests/backtest/test_grid.py -xvs
```

Expected: 2 passed.

- [ ] **Step 5: Add tests for cell_id determinism**

Append to `tests/backtest/test_grid.py`:

```python
def test_grid_cell_ids_are_deterministic() -> None:
    """Two GridConfig instances with identical axes produce identical cells."""
    cfg1 = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="min_confidence", levels=[0.45, 0.55]),
        ],
        fixed={},
    )
    cfg2 = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="min_confidence", levels=[0.45, 0.55]),
        ],
        fixed={},
    )
    ids1 = [c.cell_id for c in cfg1.cells()]
    ids2 = [c.cell_id for c in cfg2.cells()]
    assert ids1 == ids2
    # Specific format check
    assert ids1 == [
        "entry-immediate_minconf-0.45",
        "entry-immediate_minconf-0.55",
        "entry-confirmation_minconf-0.45",
        "entry-confirmation_minconf-0.55",
    ]


def test_grid_cell_id_handles_direction_modes() -> None:
    """direction_modes=['long'] → 'long', ['long','short'] → 'both'."""
    cfg = GridConfig(
        axes=[
            GridAxis(
                name="direction_modes",
                levels=[["long"], ["long", "short"]],
            ),
        ],
        fixed={},
    )
    ids = [c.cell_id for c in cfg.cells()]
    assert ids == ["dir-long", "dir-both"]


def test_grid_cell_trade_rules_kwargs_merges_axis_and_fixed() -> None:
    """trade_rules_kwargs() returns axis_values overlaid on fixed values."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.45])],
        fixed={"stop_source": "count_invalidation", "slippage_bps": 5},
    )
    cell = next(cfg.cells())
    assert cell.trade_rules_kwargs() == {
        "stop_source": "count_invalidation",
        "slippage_bps": 5,
        "min_confidence": 0.45,
    }
```

- [ ] **Step 6: Run all grid tests**

```bash
uv run pytest tests/backtest/test_grid.py -xvs
```

Expected: 5 passed.

- [ ] **Step 7: Lint**

```bash
uv run ruff check src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
git commit -m "$(cat <<'EOF'
feat(backtest): GridConfig + cartesian-product cell iteration

Defines GridAxis (one sweep dimension), GridConfig (axes + fixed
values), and GridCell (one cell = axis_values overlaid on fixed).
GridConfig.cells() yields the cartesian product in row-major axis
order. Cell IDs are deterministic from axis values for reproducible
artifact paths.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `GridResult` + aggregation methods

**Files:**
- Modify: `src/wave_alpha/backtest/grid.py`
- Modify: `tests/backtest/test_grid.py`

- [ ] **Step 1: Write failing test for `GridResult.ranked_by_expectancy`**

Append to `tests/backtest/test_grid.py`:

```python
from decimal import Decimal

from wave_alpha.backtest.grid import GridResult, GridResultRow
from wave_alpha.backtest.trade_layer import TradeLayerResult


def _result_with(closed: int, expectancy: float, pf: float = 1.0) -> TradeLayerResult:
    """Synthetic TradeLayerResult with controllable headline metrics.
    realized_R per closed trade = expectancy (so avg_R = expectancy)."""
    from datetime import date as _date

    from wave_alpha.backtest.trade_layer import TradeRecord

    trades = [
        TradeRecord(
            ticker="TEST",
            signal_as_of=_date(2024, 1, 1),
            fill_date=_date(2024, 1, 2),
            fill_price=Decimal("100"),
            exit_date=_date(2024, 1, 10),
            exit_price=Decimal("110"),
            exit_reason="target" if expectancy >= 0 else "stop",
            direction="long",
            pattern="impulse",
            wave_label="5",
            coherence_score=0.8,
            coherence_bucket="full",
            bars_held=8,
            risk_per_share=Decimal("1"),
            realized_pnl_per_share=Decimal(str(expectancy)),
            realized_R=expectancy,
            confidence=0.7,
            tf_count=2,
            degree=2,
            expected_bars_to_target=25,
        )
        for _ in range(closed)
    ]
    return TradeLayerResult(trades=trades)


def test_grid_result_ranked_by_expectancy_descending() -> None:
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=+0.20)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=+0.05)),
        ]
    )
    ranked = result.ranked_by_expectancy()
    assert [r.result.expectancy for r in ranked] == pytest.approx([0.20, 0.05, -0.10])
```

`pytest` import: append `import pytest` at the top of `tests/backtest/test_grid.py` if not already present.

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_grid.py::test_grid_result_ranked_by_expectancy_descending -xvs
```

Expected: `ImportError: cannot import name 'GridResult' from 'wave_alpha.backtest.grid'`.

- [ ] **Step 3: Add `GridResult` + `GridResultRow` + `ranked_by_expectancy` + `winner`**

Append to `src/wave_alpha/backtest/grid.py`:

```python
from wave_alpha.backtest.trade_layer import TradeLayerResult


SAMPLE_SIZE_FLOOR = 200


@dataclass(frozen=True)
class GridResultRow:
    """One cell's outcome: the cell's identity + the trade-layer result it produced."""

    cell: GridCell
    result: TradeLayerResult


@dataclass
class GridResult:
    """Cross-cell aggregation. `rows` are in cell-iteration order from
    GridConfig.cells(); aggregation methods rank, slice, and summarize."""

    rows: list[GridResultRow] = field(default_factory=list)

    def ranked_by_expectancy(self, *, sample_floor: int = SAMPLE_SIZE_FLOOR) -> list[GridResultRow]:
        """Rows with closed_trades >= sample_floor, sorted by expectancy descending."""
        eligible = [r for r in self.rows if r.result.closed_trades >= sample_floor]
        return sorted(eligible, key=lambda r: r.result.expectancy, reverse=True)

    def winner(self, *, sample_floor: int = SAMPLE_SIZE_FLOOR) -> GridResultRow | None:
        """Highest-expectancy row meeting the sample-size floor; None if no row qualifies."""
        ranked = self.ranked_by_expectancy(sample_floor=sample_floor)
        return ranked[0] if ranked else None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/backtest/test_grid.py::test_grid_result_ranked_by_expectancy_descending -xvs
```

Expected: PASS.

- [ ] **Step 5: Add tests for `winner` with sample-size floor**

Append to `tests/backtest/test_grid.py`:

```python
def test_grid_result_winner_respects_sample_size_floor() -> None:
    """Highest-expectancy cell must have closed_trades >= 200 to win."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            # Highest expectancy but tiny sample → ineligible
            GridResultRow(cell=cells[0], result=_result_with(closed=50, expectancy=+1.0)),
            # Lower expectancy but enough trades → wins
            GridResultRow(cell=cells[1], result=_result_with(closed=1000, expectancy=+0.10)),
        ]
    )
    winner = result.winner()
    assert winner is not None
    assert winner.cell.axis_values["min_confidence"] == 0.45
    assert winner.result.closed_trades == 1000


def test_grid_result_winner_returns_none_when_all_below_floor() -> None:
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=10, expectancy=+0.5)),
            GridResultRow(cell=cells[1], result=_result_with(closed=20, expectancy=+0.3)),
        ]
    )
    assert result.winner() is None
```

- [ ] **Step 6: Add `axis_sensitivity` method**

Append to `src/wave_alpha/backtest/grid.py`:

```python
    def axis_sensitivity(self) -> dict[str, dict[str, dict[str, float]]]:
        """For each axis, marginal mean of headline metrics at each level.

        Returns: {axis_name: {level_str: {metric_name: mean_value}}}
        Metrics: expectancy, profit_factor, closed_trades.
        Levels are stringified via _short_axis_value for stable keys.
        """
        if not self.rows:
            return {}
        # Collect axis names from the first row
        first_axes = list(self.rows[0].cell.axis_values.keys())
        out: dict[str, dict[str, dict[str, float]]] = {}
        for axis_name in first_axes:
            level_groups: dict[str, list[GridResultRow]] = {}
            for row in self.rows:
                level = row.cell.axis_values[axis_name]
                key = _short_axis_value(level)
                level_groups.setdefault(key, []).append(row)
            out[axis_name] = {
                level_key: {
                    "expectancy": _mean(r.result.expectancy for r in group),
                    "profit_factor": _mean(
                        _pf_or_zero(r.result.profit_factor) for r in group
                    ),
                    "closed_trades": _mean(r.result.closed_trades for r in group),
                }
                for level_key, group in level_groups.items()
            }
        return out


def _mean(values: Iterator[float] | list[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _pf_or_zero(pf: float) -> float:
    """profit_factor returns inf when there are no losses; treat inf as 0
    for averaging (it's a degenerate cell, not a real signal)."""
    return 0.0 if pf == float("inf") else pf
```

Add `from typing import Any, Iterator` at the top if not already; `Iterator` is already imported in Task 1.

- [ ] **Step 7: Add test for `axis_sensitivity`**

Append to `tests/backtest/test_grid.py`:

```python
def test_grid_result_axis_sensitivity_marginal_means() -> None:
    """For a 2x2 grid, each axis level has 2 cells; the marginal mean
    is the average expectancy of those 2 cells."""
    cfg = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.55]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={},
    )
    cells = list(cfg.cells())
    # 4 cells; pick expectancies: (0.35, immediate)=+0.20, (0.35, confirmation)=-0.10,
    # (0.55, immediate)=+0.40, (0.55, confirmation)=+0.00
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=+0.20)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=+0.40)),
            GridResultRow(cell=cells[3], result=_result_with(closed=300, expectancy=+0.00)),
        ]
    )
    sens = result.axis_sensitivity()
    # min_confidence=0.35 averages 0.20 + -0.10 = 0.05
    assert sens["min_confidence"]["0.35"]["expectancy"] == pytest.approx(0.05)
    # min_confidence=0.55 averages 0.40 + 0.00 = 0.20
    assert sens["min_confidence"]["0.55"]["expectancy"] == pytest.approx(0.20)
    # entry_trigger=immediate averages 0.20 + 0.40 = 0.30
    assert sens["entry_trigger"]["immediate"]["expectancy"] == pytest.approx(0.30)
    # entry_trigger=confirmation averages -0.10 + 0.00 = -0.05
    assert sens["entry_trigger"]["confirmation"]["expectancy"] == pytest.approx(-0.05)
```

- [ ] **Step 8: Run all tests**

```bash
uv run pytest tests/backtest/test_grid.py -xvs
```

Expected: 8 passed (5 from T1 + 3 new).

- [ ] **Step 9: Lint**

```bash
uv run ruff check src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
git commit -m "$(cat <<'EOF'
feat(backtest): GridResult aggregation — ranked, winner, sensitivity

GridResult holds one row per cell with its TradeLayerResult.
ranked_by_expectancy filters by closed_trades >= 200 (sample-size
floor matching the sanity-run E6 invariant) and sorts descending.
winner returns the top ranked row or None. axis_sensitivity computes
marginal mean expectancy / profit_factor / closed_trades per axis
level — exposes which knob moves the metric most.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `phase1_trigger` GO/NO-GO function

**Files:**
- Modify: `src/wave_alpha/backtest/grid.py`
- Modify: `tests/backtest/test_grid.py`

- [ ] **Step 1: Write failing test for the GO case**

Append to `tests/backtest/test_grid.py`:

```python
from typing import Literal

from wave_alpha.backtest.grid import phase1_trigger


def test_phase1_trigger_go_when_expectancy_threshold_met() -> None:
    """One cell with expectancy >= +0.05R AND closed_trades >= 200 → GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=500, expectancy=+0.07)),
        ]
    )
    verdict, reason = phase1_trigger(result)
    assert verdict == "GO"
    assert "+0.07" in reason or "0.07" in reason
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_grid.py::test_phase1_trigger_go_when_expectancy_threshold_met -xvs
```

Expected: `ImportError: cannot import name 'phase1_trigger'`.

- [ ] **Step 3: Implement `phase1_trigger`**

Append to `src/wave_alpha/backtest/grid.py`:

```python
PHASE1_EXPECTANCY_THRESHOLD = 0.05
PHASE1_PF_THRESHOLD = 1.05


def phase1_trigger(
    result: GridResult,
    *,
    expectancy_threshold: float = PHASE1_EXPECTANCY_THRESHOLD,
    pf_threshold: float = PHASE1_PF_THRESHOLD,
    sample_floor: int = SAMPLE_SIZE_FLOOR,
) -> tuple[Literal["GO", "NO-GO"], str]:
    """GO iff at least one cell has (expectancy >= expectancy_threshold OR
    profit_factor >= pf_threshold) AND closed_trades >= sample_floor.

    Returns (verdict, human-readable reason for the spec §6.2 fill).
    """
    qualifying: list[GridResultRow] = []
    for row in result.rows:
        if row.result.closed_trades < sample_floor:
            continue
        if (
            row.result.expectancy >= expectancy_threshold
            or _pf_or_zero(row.result.profit_factor) >= pf_threshold
        ):
            qualifying.append(row)
    if not qualifying:
        # Identify the best-effort summary for the NO-GO reason
        best_eligible = result.winner()
        if best_eligible is None:
            return (
                "NO-GO",
                "No cell met the sample-size floor "
                f"(closed_trades >= {sample_floor}).",
            )
        return (
            "NO-GO",
            f"Best eligible cell: expectancy={best_eligible.result.expectancy:+.3f}R, "
            f"PF={_pf_or_zero(best_eligible.result.profit_factor):.2f}, "
            f"closed_trades={best_eligible.result.closed_trades}. "
            f"Threshold: expectancy >= +{expectancy_threshold:.2f}R "
            f"OR PF >= {pf_threshold:.2f}.",
        )
    top = max(qualifying, key=lambda r: r.result.expectancy)
    return (
        "GO",
        f"Best qualifying cell: expectancy={top.result.expectancy:+.3f}R, "
        f"PF={_pf_or_zero(top.result.profit_factor):.2f}, "
        f"closed_trades={top.result.closed_trades}. "
        f"Cell axes: {top.cell.axis_values}.",
    )
```

The `Literal` import: add `from typing import Any, Iterator, Literal` at the top of `grid.py` if `Literal` is not already imported.

- [ ] **Step 4: Add NO-GO test cases**

Append to `tests/backtest/test_grid.py`:

```python
def test_phase1_trigger_no_go_when_all_negative() -> None:
    """All cells negative expectancy and PF < 1.05 → NO-GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=-0.05)),
        ]
    )
    verdict, reason = phase1_trigger(result)
    assert verdict == "NO-GO"


def test_phase1_trigger_no_go_when_positive_but_undersample() -> None:
    """Cell has expectancy=+0.10 but only 100 closed_trades → NO-GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35])],
        fixed={},
    )
    cell = next(cfg.cells())
    result = GridResult(
        rows=[GridResultRow(cell=cell, result=_result_with(closed=100, expectancy=+0.10))]
    )
    verdict, _ = phase1_trigger(result)
    assert verdict == "NO-GO"


def test_phase1_trigger_go_via_pf_threshold() -> None:
    """Cell has expectancy=+0.02 (below 0.05 threshold) but PF=1.20 → GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35])],
        fixed={},
    )
    cell = next(cfg.cells())
    # Construct a result where avg_R = +0.02 but PF > 1.05
    # Build by hand: 100 wins of +0.5, 200 losses of -0.24 (gain=50, loss=48,
    # PF=50/48=1.04, avg_R = (50 - 48) / 300 = 0.0067)
    # That doesn't hit threshold. Pick: 200 wins of +1.0, 200 losses of -0.96
    # gain=200, loss=192, PF=200/192=1.0417, avg_R=(200-192)/400=0.02 → still no
    # Pick: 200 wins of +1.5, 200 losses of -1.46
    # gain=300, loss=292, PF=300/292=1.027, avg_R=(300-292)/400=0.02 → no
    # Need PF >= 1.05. Pick: 200 wins of +1.0, 100 losses of -1.9
    # gain=200, loss=190, PF=200/190=1.0526, avg_R=(200-190)/300=0.0333 → marginal
    # Below 0.05 threshold but PF >= 1.05. Triggers via PF branch.
    from datetime import date as _date

    from wave_alpha.backtest.trade_layer import TradeLayerResult, TradeRecord

    def _trade(realized_R: float) -> TradeRecord:  # noqa: N803
        return TradeRecord(
            ticker="TEST",
            signal_as_of=_date(2024, 1, 1),
            fill_date=_date(2024, 1, 2),
            fill_price=Decimal("100"),
            exit_date=_date(2024, 1, 10),
            exit_price=Decimal("110"),
            exit_reason="target" if realized_R > 0 else "stop",
            direction="long",
            pattern="impulse",
            wave_label="5",
            coherence_score=0.8,
            coherence_bucket="full",
            bars_held=8,
            risk_per_share=Decimal("1"),
            realized_pnl_per_share=Decimal(str(realized_R)),
            realized_R=realized_R,
            confidence=0.7,
            tf_count=2,
            degree=2,
            expected_bars_to_target=25,
        )

    trades = [_trade(+1.0) for _ in range(200)] + [_trade(-1.9) for _ in range(100)]
    tlr = TradeLayerResult(trades=trades)
    assert tlr.profit_factor >= 1.05
    assert tlr.expectancy < 0.05  # below the expectancy threshold
    result = GridResult(rows=[GridResultRow(cell=cell, result=tlr)])
    verdict, _ = phase1_trigger(result)
    assert verdict == "GO"
```

- [ ] **Step 5: Run all tests**

```bash
uv run pytest tests/backtest/test_grid.py -xvs
```

Expected: 11 passed (8 prior + 3 new).

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
git commit -m "$(cat <<'EOF'
feat(backtest): phase1_trigger GO/NO-GO function

GO iff at least one cell has (expectancy >= +0.05R OR PF >= 1.05) AND
closed_trades >= 200. Returns (verdict, human-readable reason) for
spec §6.2 fill. NO-GO branches: all-undersample (no cell met floor)
or all-below-threshold (best eligible failed both criteria).

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `run_grid` orchestration

**Files:**
- Modify: `src/wave_alpha/backtest/grid.py`
- Modify: `tests/backtest/test_grid.py`

- [ ] **Step 1: Write failing test that stubs `run_trade_layer`**

Append to `tests/backtest/test_grid.py`:

```python
def test_run_grid_calls_run_trade_layer_per_cell(monkeypatch) -> None:
    """run_grid invokes run_trade_layer once per cell with the cell's TradeRules."""
    from datetime import date as _date

    from wave_alpha.backtest.runner import BacktestConfig
    from wave_alpha.backtest import grid as grid_mod

    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.45, 0.55])],
        fixed={"slippage_bps": 5},
    )
    base_cfg = BacktestConfig(
        universe=["AAPL"],
        start=_date(2024, 1, 1),
        end=_date(2024, 6, 30),
        step=5,
    )

    captured_rules: list = []

    def fake_run_trade_layer(cfg, *, provider, rules):
        captured_rules.append(rules)
        return _result_with(closed=300, expectancy=+0.10)

    monkeypatch.setattr(grid_mod, "run_trade_layer", fake_run_trade_layer)

    class _NullProvider:
        def fetch(self, ticker, interval, start=None, end=None):
            return []

    result = run_grid(cfg, base_cfg=base_cfg, provider=_NullProvider())

    assert len(result.rows) == 2
    assert len(captured_rules) == 2
    assert captured_rules[0].min_confidence == 0.45
    assert captured_rules[0].slippage_bps == 5
    assert captured_rules[1].min_confidence == 0.55
```

The `run_grid` import: at the top of `tests/backtest/test_grid.py`, change `from wave_alpha.backtest.grid import GridAxis, GridConfig` to also import `run_grid`. (Reasonable to update incrementally.)

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_grid.py::test_run_grid_calls_run_trade_layer_per_cell -xvs
```

Expected: `ImportError: cannot import name 'run_grid'`.

- [ ] **Step 3: Implement `run_grid`**

Append to `src/wave_alpha/backtest/grid.py`:

```python
from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.trades.models import TradeRules


def run_grid(
    grid_config: GridConfig,
    *,
    base_cfg: BacktestConfig,
    provider: OHLCVProvider,
) -> GridResult:
    """Iterate over cells in row-major axis order; per cell, build a
    TradeRules from the cell's axis_values + fixed values and call
    run_trade_layer. Aggregate into a GridResult.

    The grid is a thin orchestration on top of run_trade_layer; per-cell
    numerical correctness is the trade-sim layer's contract.
    """
    rows: list[GridResultRow] = []
    for cell in grid_config.cells():
        rules = TradeRules.model_validate(cell.trade_rules_kwargs())
        result = run_trade_layer(base_cfg, provider=provider, rules=rules)
        rows.append(GridResultRow(cell=cell, result=result))
    return GridResult(rows=rows)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/backtest/test_grid.py::test_run_grid_calls_run_trade_layer_per_cell -xvs
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -x
```

Expected: all green (existing 475 + new grid tests).

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/backtest/grid.py tests/backtest/test_grid.py
git commit -m "$(cat <<'EOF'
feat(backtest): run_grid orchestration over run_trade_layer

Iterates GridConfig.cells() in row-major order, builds TradeRules
from each cell's axis_values + fixed values, calls run_trade_layer,
collects results into a GridResult. Pure orchestration; numerical
correctness is the trade-sim layer's contract.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `render_grid_markdown` cross-cell report

**Files:**
- Create: `src/wave_alpha/backtest/grid_report.py`
- Modify: `tests/backtest/test_grid.py`

- [ ] **Step 1: Write failing test for the report shape**

Append to `tests/backtest/test_grid.py`:

```python
def test_render_grid_markdown_contains_required_sections() -> None:
    """Renderer produces sections: ranked cells, axis sensitivity,
    winner/verdict block, header with cell count + axes."""
    from wave_alpha.backtest.grid_report import render_grid_markdown

    cfg = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.45]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={"stop_source": "count_invalidation"},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=+0.10)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=-0.05)),
            GridResultRow(cell=cells[3], result=_result_with(closed=300, expectancy=+0.20)),
        ]
    )
    md = render_grid_markdown(result, config=cfg, phase_name="Phase 1")
    assert "# Grid Sweep Report" in md
    assert "Phase 1" in md
    assert "## Per-cell summary" in md
    assert "## Axis sensitivity" in md
    assert "## Verdict" in md
    # Cell IDs appear in summary
    assert cells[3].cell_id in md
    # Axis names appear in sensitivity
    assert "min_confidence" in md
    assert "entry_trigger" in md
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_grid.py::test_render_grid_markdown_contains_required_sections -xvs
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.backtest.grid_report'`.

- [ ] **Step 3: Implement `render_grid_markdown`**

Create `src/wave_alpha/backtest/grid_report.py`:

```python
"""Markdown renderer for cross-cell GridResult summaries.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.8.
"""
from __future__ import annotations

from datetime import UTC, datetime

from wave_alpha.backtest.grid import (
    GridConfig,
    GridResult,
    SAMPLE_SIZE_FLOOR,
    phase1_trigger,
)


def render_grid_markdown(
    result: GridResult,
    *,
    config: GridConfig,
    phase_name: str,
) -> str:
    """Render the grid result as a markdown report with sections:
    header, per-cell summary, axis sensitivity, verdict.
    """
    lines: list[str] = []
    lines.append("# Grid Sweep Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**{phase_name}** — {len(result.rows)} cells")
    axes_str = " × ".join(
        f"{a.name}({len(a.levels)})" for a in config.axes
    )
    lines.append(f"**Axes:** {axes_str}")
    if config.fixed:
        fixed_str = ", ".join(f"{k}={v}" for k, v in config.fixed.items())
        lines.append(f"**Held fixed:** {fixed_str}")
    lines.append("")

    # Per-cell summary
    lines.append("## Per-cell summary")
    lines.append("")
    lines.append("| cell_id | n | hit | avg R | expectancy | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in result.rows:
        n = row.result.closed_trades
        hit = row.result.hit_rate
        avg_r = row.result.avg_R
        exp = row.result.expectancy
        pf = row.result.profit_factor
        pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
        lines.append(
            f"| {row.cell.cell_id} | {n} | {hit:.2f} | {avg_r:+.2f} | "
            f"{exp:+.2f} | {pf_s} |"
        )
    lines.append("")

    # Axis sensitivity
    lines.append("## Axis sensitivity")
    lines.append("")
    lines.append("| axis | level | mean expectancy | mean PF | mean closed_trades |")
    lines.append("|---|---|---:|---:|---:|")
    sens = result.axis_sensitivity()
    for axis_name, levels in sens.items():
        for level_key, metrics in levels.items():
            lines.append(
                f"| {axis_name} | {level_key} | "
                f"{metrics['expectancy']:+.3f} | "
                f"{metrics['profit_factor']:.2f} | "
                f"{int(metrics['closed_trades'])} |"
            )
    lines.append("")

    # Verdict (only meaningful for Phase 1; harmless for Phase 2)
    lines.append("## Verdict")
    lines.append("")
    winner = result.winner()
    if winner is None:
        lines.append(
            f"**No winner.** No cell met the sample-size floor "
            f"(closed_trades >= {SAMPLE_SIZE_FLOOR})."
        )
    else:
        lines.append(
            f"**Best cell:** `{winner.cell.cell_id}` — "
            f"expectancy={winner.result.expectancy:+.3f}R, "
            f"PF={winner.result.profit_factor:.2f}, "
            f"n={winner.result.closed_trades}"
        )
        lines.append("")
        lines.append("**Axis values:**")
        for k, v in winner.cell.axis_values.items():
            lines.append(f"- `{k}` = `{v}`")
    lines.append("")

    if phase_name.lower().startswith("phase 1"):
        verdict, reason = phase1_trigger(result)
        lines.append(f"**Phase 1 trigger:** **{verdict}**")
        lines.append("")
        lines.append(f"_{reason}_")
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/backtest/test_grid.py::test_render_grid_markdown_contains_required_sections -xvs
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/backtest/grid_report.py tests/backtest/test_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/backtest/grid_report.py tests/backtest/test_grid.py
git commit -m "$(cat <<'EOF'
feat(backtest): grid_report markdown renderer

render_grid_markdown produces a cross-cell summary with sections:
header (phase name + axis dimensions + fixed values), per-cell
ranking table, axis sensitivity table (marginal means per level),
winner block, and (Phase 1 only) GO/NO-GO trigger verdict.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: YAML configs for Phase 1 + Phase 2

**Files:**
- Create: `configs/grid_phase1.yaml`
- Create: `configs/grid_phase2.yaml`

- [ ] **Step 1: Write Phase 1 config**

Create `configs/grid_phase1.yaml`:

```yaml
# Ablation grid Phase 1 — 16-cell screening sweep.
# Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.2
axes:
  - name: min_confidence
    levels: [0.35, 0.45, 0.55, 0.65]
  - name: direction_modes
    levels:
      - [long]
      - [long, short]
  - name: entry_trigger
    levels: [immediate, confirmation]

fixed:
  stop_source: count_invalidation
  target_source: fib_extension
  slippage_bps: 5
  time_stop_mode: degree_based
```

- [ ] **Step 2: Write Phase 2 config**

Create `configs/grid_phase2.yaml`:

```yaml
# Ablation grid Phase 2 — 32-cell validation sweep.
# Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.4
#
# direction_modes is pinned at the Phase 1 winner. Edit this file
# after Phase 1 to set direction_modes to either [long] or [long, short]
# based on the §6.2 verdict before invoking `wave-alpha backtest grid`.
axes:
  - name: entry_trigger
    levels: [immediate, confirmation]
  - name: stop_source
    levels: [count_invalidation, atr_multiple]
  - name: target_source
    levels: [fib_extension, fixed_R]
  - name: min_confidence
    levels: [0.35, 0.45, 0.55, 0.65]

fixed:
  # FILL FROM PHASE 1 WINNER: replace with [long] or [long, short]
  direction_modes: [long, short]
  target_R: 2.0
  slippage_bps: 5
  time_stop_mode: degree_based
```

- [ ] **Step 3: Verify Phase 1 config round-trips through GridConfig + TradeRules**

```bash
uv run python -c "
import yaml
from wave_alpha.backtest.grid import GridAxis, GridConfig
from wave_alpha.trades.models import TradeRules

raw = yaml.safe_load(open('configs/grid_phase1.yaml'))
axes = [GridAxis(**a) for a in raw['axes']]
cfg = GridConfig(axes=axes, fixed=raw['fixed'])
print(f'cells: {sum(1 for _ in cfg.cells())}')
# Validate every cell builds a valid TradeRules
for cell in cfg.cells():
    TradeRules.model_validate(cell.trade_rules_kwargs())
print('all 16 cells produce valid TradeRules')
"
```

Expected:
```
cells: 16
all 16 cells produce valid TradeRules
```

- [ ] **Step 4: Verify Phase 2 config**

```bash
uv run python -c "
import yaml
from wave_alpha.backtest.grid import GridAxis, GridConfig
from wave_alpha.trades.models import TradeRules

raw = yaml.safe_load(open('configs/grid_phase2.yaml'))
axes = [GridAxis(**a) for a in raw['axes']]
cfg = GridConfig(axes=axes, fixed=raw['fixed'])
print(f'cells: {sum(1 for _ in cfg.cells())}')
for cell in cfg.cells():
    TradeRules.model_validate(cell.trade_rules_kwargs())
print('all 32 cells produce valid TradeRules')
"
```

Expected:
```
cells: 32
all 32 cells produce valid TradeRules
```

- [ ] **Step 5: Commit**

```bash
git add configs/grid_phase1.yaml configs/grid_phase2.yaml
git commit -m "$(cat <<'EOF'
feat(configs): grid_phase1 + grid_phase2 axis definitions

Phase 1: 16 cells (min_confidence × direction_modes × entry_trigger).
Phase 2: 32 cells (entry × stop × target × min_confidence), with
direction_modes placeholder ready for the Phase 1 winner.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.2, §3.4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: CLI subcommand `wave-alpha backtest grid`

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/cli/test_backtest_grid.py`

- [ ] **Step 1: Write the failing CLI test**

Create `tests/cli/test_backtest_grid.py`:

```python
"""Smoke test for `wave-alpha backtest grid` CLI subcommand.

Stubs out run_trade_layer to keep runtime trivial; the real
backtest is exercised by Task 8 (Phase 1 run).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app


def _patch_run_trade_layer(monkeypatch):
    """Replace run_trade_layer with a stub returning a synthetic result."""
    from wave_alpha.backtest import grid as grid_mod
    from wave_alpha.backtest.trade_layer import TradeLayerResult, TradeRecord

    def _trade(R: float) -> TradeRecord:  # noqa: N803
        return TradeRecord(
            ticker="TEST",
            signal_as_of=date(2024, 1, 1),
            fill_date=date(2024, 1, 2),
            fill_price=Decimal("100"),
            exit_date=date(2024, 1, 10),
            exit_price=Decimal("110"),
            exit_reason="target" if R > 0 else "stop",
            direction="long",
            pattern="impulse",
            wave_label="5",
            coherence_score=0.8,
            coherence_bucket="full",
            bars_held=8,
            risk_per_share=Decimal("1"),
            realized_pnl_per_share=Decimal(str(R)),
            realized_R=R,
            confidence=0.7,
            tf_count=2,
            degree=2,
            expected_bars_to_target=25,
        )

    def fake_run_trade_layer(cfg, *, provider, rules):
        # Synthetic positive-expectancy result so the verdict is GO
        trades = [_trade(+0.5) for _ in range(150)] + [_trade(-1.0) for _ in range(150)]
        return TradeLayerResult(trades=trades)

    monkeypatch.setattr(grid_mod, "run_trade_layer", fake_run_trade_layer)


def test_backtest_grid_writes_artifacts(monkeypatch, tmp_path: Path) -> None:
    _patch_run_trade_layer(monkeypatch)
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    grid_yaml = tmp_path / "grid.yaml"
    grid_yaml.write_text(
        "axes:\n"
        "  - name: min_confidence\n"
        "    levels: [0.45, 0.55]\n"
        "fixed:\n"
        "  stop_source: count_invalidation\n"
        "  target_source: fib_extension\n"
    )
    out_dir = tmp_path / "out"

    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "backtest",
            "grid",
            "--universe",
            str(universe_file),
            "--start",
            "2024-01-01",
            "--end",
            "2024-06-30",
            "--step",
            "5",
            "--grid",
            str(grid_yaml),
            "--out",
            str(out_dir),
            "--phase-name",
            "Phase 1",
        ],
    )
    assert res.exit_code == 0, res.output
    # Top-level summary
    grid_md = out_dir / "grid.md"
    assert grid_md.exists()
    assert "Grid Sweep Report" in grid_md.read_text()
    # Per-cell artifacts
    summary_csv = out_dir / "summary.csv"
    assert summary_csv.exists()
    cells_dir = out_dir / "cells"
    assert cells_dir.exists()
    cell_dirs = list(cells_dir.iterdir())
    assert len(cell_dirs) == 2  # 2 cells from the 1-axis 2-level config
    for cell_dir in cell_dirs:
        assert (cell_dir / "report.md").exists()
        assert (cell_dir / "result.json").exists()
        assert (cell_dir / "trades.csv").exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/cli/test_backtest_grid.py -xvs
```

Expected: error like `Usage: ... No such command 'grid'.`

- [ ] **Step 3: Add `backtest grid` subcommand to CLI**

Edit `src/wave_alpha/cli/app.py`. Locate the existing `backtest_app` sub-app (search for `backtest_app = typer.Typer`) and the existing `@backtest_app.command("trade")` decorator. Append a new command after `backtest_trade`:

```python
@backtest_app.command("grid")
def backtest_grid(
    universe: Path = typer.Option(  # noqa: B008
        ..., "--universe", help="Path to universe txt file."
    ),
    start: str = typer.Option(..., "--start", help="ISO start date."),
    end: str = typer.Option(..., "--end", help="ISO end date."),
    step: int = typer.Option(1, "--step", help="Signal-generation cadence (business days)."),
    grid: Path = typer.Option(..., "--grid", help="YAML grid config path."),  # noqa: B008
    out: Path = typer.Option(..., "--out", help="Output directory."),  # noqa: B008
    phase_name: str = typer.Option(  # noqa: B008
        "Phase", "--phase-name", help='Label for the report header (e.g. "Phase 1").'
    ),
) -> None:
    """Run an ablation grid sweep over (universe x dates x cells)."""
    import csv
    import json as _json
    from dataclasses import asdict

    import yaml

    from wave_alpha.backtest.grid import GridAxis, GridConfig, run_grid
    from wave_alpha.backtest.grid_report import render_grid_markdown
    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig
    from wave_alpha.backtest.universe import load_universe
    from wave_alpha.trades.models import TradeRules

    raw = yaml.safe_load(grid.read_text()) or {}
    axes = [GridAxis(**a) for a in raw.get("axes", [])]
    fixed = raw.get("fixed", {}) or {}
    grid_config = GridConfig(axes=axes, fixed=fixed)

    base_cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        step=step,
    )
    _warn_if_4h_window_partial(base_cfg.start)
    typer.echo(
        f"running grid sweep: {sum(1 for _ in grid_config.cells())} cells, "
        f"{len(base_cfg.universe)} tickers, step={step}"
    )

    provider = _default_provider()
    result = run_grid(grid_config, base_cfg=base_cfg, provider=provider)

    out.mkdir(parents=True, exist_ok=True)
    cells_dir = out / "cells"
    cells_dir.mkdir(exist_ok=True)

    # Top-level grid markdown
    (out / "grid.md").write_text(
        render_grid_markdown(result, config=grid_config, phase_name=phase_name)
    )

    # Cross-cell summary CSV
    summary_path = out / "summary.csv"
    with summary_path.open("w", newline="") as fh:
        if not result.rows:
            fh.write("")
        else:
            axis_names = list(result.rows[0].cell.axis_values.keys())
            fields = (
                ["cell_id"]
                + axis_names
                + [
                    "closed_trades",
                    "open_at_end",
                    "hit_rate",
                    "avg_R",
                    "expectancy",
                    "profit_factor",
                    "max_drawdown_R",
                    "coverage_4h",
                ]
            )
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for row in result.rows:
                pf = row.result.profit_factor
                w.writerow(
                    {
                        "cell_id": row.cell.cell_id,
                        **{n: str(row.cell.axis_values[n]) for n in axis_names},
                        "closed_trades": row.result.closed_trades,
                        "open_at_end": row.result.open_at_end,
                        "hit_rate": f"{row.result.hit_rate:.4f}",
                        "avg_R": f"{row.result.avg_R:.4f}",
                        "expectancy": f"{row.result.expectancy:.4f}",
                        "profit_factor": (
                            "" if pf == float("inf") else f"{pf:.4f}"
                        ),
                        "max_drawdown_R": f"{row.result.max_drawdown_R:.4f}",
                        "coverage_4h": f"{row.result.coverage_4h:.4f}",
                    }
                )

    # Per-cell artifacts
    for row in result.rows:
        cell_dir = cells_dir / row.cell.cell_id
        cell_dir.mkdir(exist_ok=True)
        rules = TradeRules.model_validate(row.cell.trade_rules_kwargs())
        (cell_dir / "report.md").write_text(
            render_trade_markdown(row.result, cfg=base_cfg, rules=rules)
        )
        # JSON sidecar mirrors the trade-layer CLI shape
        payload = {
            "cell_id": row.cell.cell_id,
            "axis_values": row.cell.axis_values,
            "fixed": row.cell.fixed,
            "rules": rules.model_dump(mode="json"),
            "headline": {
                "closed_trades": row.result.closed_trades,
                "open_at_end": row.result.open_at_end,
                "hit_rate": row.result.hit_rate,
                "avg_R": row.result.avg_R,
                "expectancy": row.result.expectancy,
                "profit_factor": (
                    None
                    if row.result.profit_factor == float("inf")
                    else row.result.profit_factor
                ),
                "max_drawdown_R": row.result.max_drawdown_R,
                "coverage_4h": row.result.coverage_4h,
            },
            "by_direction": row.result.by_direction(),
            "by_pattern": row.result.by_pattern(),
            "by_coherence_bucket": row.result.by_coherence_bucket(),
            "by_tf_count": {str(k): v for k, v in row.result.by_tf_count().items()},
            "by_year": {str(k): v for k, v in row.result.by_year().items()},
        }
        (cell_dir / "result.json").write_text(
            _json.dumps(payload, indent=2, default=_json_default)
        )
        # Per-cell trades CSV
        with (cell_dir / "trades.csv").open("w", newline="") as fh:
            if not row.result.trades:
                fh.write("")
                continue
            fields = list(asdict(row.result.trades[0]).keys())
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for t in row.result.trades:
                trow = asdict(t)
                for k, v in trow.items():
                    if isinstance(v, Decimal):
                        trow[k] = str(v)
                w.writerow(trow)

    typer.echo(f"wrote {out / 'grid.md'}")
    typer.echo(f"wrote {summary_path}")
    typer.echo(f"wrote {len(result.rows)} cells under {cells_dir}")
```

The `_json_default` helper, `Decimal` import, and `_warn_if_4h_window_partial` already exist in the file (used by `backtest_trade`). The `Path`, `date`, `typer` imports are already at the top. If `_json_default` is missing or scoped, search for it; it's defined where `backtest_trade` uses it.

- [ ] **Step 4: Run the failing test to verify it passes**

```bash
uv run pytest tests/cli/test_backtest_grid.py -xvs
```

Expected: PASS.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/cli/app.py tests/cli/test_backtest_grid.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Smoke-test `--help`**

```bash
uv run wave-alpha backtest grid --help
```

Expected: typer help text listing the subcommand's options.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_backtest_grid.py
git commit -m "$(cat <<'EOF'
feat(cli): backtest grid subcommand

Wires GridConfig + run_grid + render_grid_markdown into the existing
`wave-alpha backtest` sub-app. Loads YAML grid config via --grid;
writes top-level grid.md + summary.csv + per-cell {report.md,
result.json, trades.csv} under --out. Per-cell shape mirrors the
existing `backtest trade` artifacts so single-cell inspection is
identical to a sanity run.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Run Phase 1 + fill spec §6.1 + §6.2

**Files:**
- Read-only on the codebase.
- Modify: `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` (§6.1, §6.2)
- Side effect: writes `reports/grid-2026-05-06/phase1/...`

This is an operational task. Phase 1 is ~16 cells × ~22 min warm-cache ≈ 6 hours sequential. Cache should already be warm from the prior sanity-run work.

- [ ] **Step 1: Verify cache freshness**

```bash
uv run python -c "
import sqlite3, os
p = os.path.expanduser('~/.wave_alpha/cache.sqlite')
c = sqlite3.connect(p)
cur = c.cursor()
for row in cur.execute(\"SELECT interval, COUNT(DISTINCT ticker), COUNT(*), MAX(ts) FROM bars GROUP BY interval\"):
    print(row)
"
```

Expected: counts at three intervals (`1d`, `1wk`, `4h`) for ≥50 distinct tickers, with `MAX(ts)` near `2024-12-31`.

If cache is cold or sparse, run pre-warm first:

```bash
uv run python scripts/prewarm_4h_cache.py > reports/grid-2026-05-06/prewarm.log 2>&1
```

- [ ] **Step 2: Capture run metadata**

```bash
mkdir -p reports/grid-2026-05-06/phase1
git rev-parse HEAD > reports/grid-2026-05-06/phase1/code_sha.txt
date -u +%Y-%m-%dT%H:%M:%SZ > reports/grid-2026-05-06/phase1/run_started_at.txt
cat reports/grid-2026-05-06/phase1/code_sha.txt reports/grid-2026-05-06/phase1/run_started_at.txt
```

- [ ] **Step 3: Run Phase 1**

```bash
uv run wave-alpha backtest grid \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --grid configs/grid_phase1.yaml \
  --out reports/grid-2026-05-06/phase1/ \
  --phase-name "Phase 1" \
  > reports/grid-2026-05-06/phase1/run.log 2>&1; echo "EXIT=$?" >> reports/grid-2026-05-06/phase1/run.log
```

This is a long-running command (~6h). Run in the background or accept the wait.

- [ ] **Step 4: Verify artifacts and capture finish time**

```bash
date -u +%Y-%m-%dT%H:%M:%SZ > reports/grid-2026-05-06/phase1/run_finished_at.txt
ls -la reports/grid-2026-05-06/phase1/cells/ | head -20
test -s reports/grid-2026-05-06/phase1/grid.md
test -s reports/grid-2026-05-06/phase1/summary.csv
```

Expected: 16 cell directories, `grid.md` and `summary.csv` non-empty.

- [ ] **Step 5: Read the verdict**

```bash
grep -A 5 "## Verdict\|Phase 1 trigger" reports/grid-2026-05-06/phase1/grid.md
```

The output ends with `**Phase 1 trigger:** **GO**` or `**NO-GO**`. Note the verdict.

- [ ] **Step 6: Pull data for spec §6.1**

```bash
uv run python -c "
import csv
rows = list(csv.DictReader(open('reports/grid-2026-05-06/phase1/summary.csv')))
print('Per-cell summary (16 rows):')
for r in rows:
    print(f\"  {r['cell_id']:55s} n={r['closed_trades']:>5} expectancy={r['expectancy']:+s} PF={r['profit_factor']:>6}\")
"
```

```bash
grep -A 25 "## Axis sensitivity" reports/grid-2026-05-06/phase1/grid.md
```

Both outputs go into spec §6.1.

- [ ] **Step 7: Edit `docs/superpowers/specs/2026-05-06-ablation-grid-design.md`**

Replace the placeholders in §6.1:
- `Run date:` from `run_started_at.txt`
- `Cache state:` based on Step 1 freshness check (warm / cold / mixed)
- `Wall time:` computed from `run_started_at.txt` and `run_finished_at.txt`
- `Code SHA:` from `code_sha.txt`
- "Per-cell summary (16 rows): paste from `summary.csv`" — replace with the 16-row markdown table built from the Step 6 output
- Axis sensitivity table — paste the `## Axis sensitivity` block from `grid.md`
- "Best cell:" block — fill from the `## Verdict` block in `grid.md`

Replace the placeholders in §6.2:
- Check `[x]` on the GO line if Step 5 verdict was GO; check `[x]` on NO-GO if the verdict was NO-GO.
- If GO: leave the NO-GO addendum unfilled and proceed to Task 9.
- If NO-GO: fill the addendum with reasoning (cite specific axis-sensitivity rows from §6.1 that show no positive region) and pick one of the three §3.3 named next-steps. Skip Task 9 entirely. Proceed to Task 10 (final review + bump).

- [ ] **Step 8: Commit the §6 fill**

```bash
git add docs/superpowers/specs/2026-05-06-ablation-grid-design.md
git commit -m "$(cat <<'EOF'
docs(spec): ablation grid Phase 1 results — §6.1 + §6.2 filled

Phase 1 ran 16 cells over 2020-2024 on the curated 50-ticker
universe. Verdict: <GO|NO-GO>. <one-sentence summary of best cell
or NO-GO reasoning>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Replace `<GO|NO-GO>` and the summary sentence with the actual outcome.)

---

## Task 9: Run Phase 2 + fill spec §6.3 + §6.4 (CONDITIONAL — only if Phase 1 verdict was GO)

**Files:**
- Modify: `configs/grid_phase2.yaml` (set `direction_modes` from Phase 1 winner)
- Modify: `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` (§6.3, §6.4)
- Side effect: writes `reports/grid-2026-05-06/phase2/...`

If Task 8 verdict was NO-GO, **skip this task entirely** and proceed to Task 10.

- [ ] **Step 1: Pin `direction_modes` in `configs/grid_phase2.yaml`**

Read the §6.1 best-cell axes; identify the `direction_modes` value. Edit `configs/grid_phase2.yaml`:

```yaml
fixed:
  # FILL FROM PHASE 1 WINNER: replace with [long] or [long, short]
  direction_modes: [long]   # ← set to the Phase 1 winner's value
  target_R: 2.0
  slippage_bps: 5
  time_stop_mode: degree_based
```

- [ ] **Step 2: Sanity-check the updated config**

```bash
uv run python -c "
import yaml
from wave_alpha.backtest.grid import GridAxis, GridConfig
from wave_alpha.trades.models import TradeRules

raw = yaml.safe_load(open('configs/grid_phase2.yaml'))
axes = [GridAxis(**a) for a in raw['axes']]
cfg = GridConfig(axes=axes, fixed=raw['fixed'])
print(f'cells: {sum(1 for _ in cfg.cells())}')
print(f'pinned direction_modes: {raw[\"fixed\"][\"direction_modes\"]}')
"
```

Expected: `cells: 32`, with the pinned `direction_modes` matching the Phase 1 winner.

- [ ] **Step 3: Commit the config update**

```bash
git add configs/grid_phase2.yaml
git commit -m "chore(configs): pin Phase 2 direction_modes from Phase 1 winner"
```

- [ ] **Step 4: Capture run metadata + run Phase 2**

```bash
mkdir -p reports/grid-2026-05-06/phase2
git rev-parse HEAD > reports/grid-2026-05-06/phase2/code_sha.txt
date -u +%Y-%m-%dT%H:%M:%SZ > reports/grid-2026-05-06/phase2/run_started_at.txt

uv run wave-alpha backtest grid \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --grid configs/grid_phase2.yaml \
  --out reports/grid-2026-05-06/phase2/ \
  --phase-name "Phase 2" \
  > reports/grid-2026-05-06/phase2/run.log 2>&1; echo "EXIT=$?" >> reports/grid-2026-05-06/phase2/run.log

date -u +%Y-%m-%dT%H:%M:%SZ > reports/grid-2026-05-06/phase2/run_finished_at.txt
```

This is a long-running command (~12h sequential).

- [ ] **Step 5: Verify artifacts**

```bash
ls -la reports/grid-2026-05-06/phase2/cells/ | head -5
test -s reports/grid-2026-05-06/phase2/grid.md
test -s reports/grid-2026-05-06/phase2/summary.csv
```

Expected: 32 cell directories, top-level files non-empty.

- [ ] **Step 6: Pull data for spec §6.3**

```bash
uv run python -c "
import csv
rows = list(csv.DictReader(open('reports/grid-2026-05-06/phase2/summary.csv')))
print('Per-cell summary (32 rows):')
for r in rows:
    print(f\"  {r['cell_id']:75s} n={r['closed_trades']:>5} expectancy={r['expectancy']:+s} PF={r['profit_factor']:>6}\")
"
```

```bash
grep -A 25 "## Axis sensitivity" reports/grid-2026-05-06/phase2/grid.md
grep -A 5 "Best cell" reports/grid-2026-05-06/phase2/grid.md
```

Top 3 by expectancy:

```bash
uv run python -c "
import csv
rows = list(csv.DictReader(open('reports/grid-2026-05-06/phase2/summary.csv')))
elig = [r for r in rows if int(r['closed_trades']) >= 200]
elig.sort(key=lambda r: float(r['expectancy']), reverse=True)
print('Top 3:')
for r in elig[:3]:
    print(f\"  {r['cell_id']:75s} n={r['closed_trades']:>5} hit={r['hit_rate']} avg_R={r['avg_R']:+s} expectancy={r['expectancy']:+s} PF={r['profit_factor']:>6}\")
"
```

By-direction breakdown (only if `direction_modes` was `[long, short]`):

```bash
uv run python -c "
import csv, json
rows = list(csv.DictReader(open('reports/grid-2026-05-06/phase2/summary.csv')))
print('| cell_id | long expectancy | short expectancy | delta |')
print('|---|---:|---:|---:|')
for r in rows:
    cid = r['cell_id']
    payload = json.load(open(f'reports/grid-2026-05-06/phase2/cells/{cid}/result.json'))
    long_e = payload['by_direction'].get('long', {}).get('expectancy', 0.0)
    short_e = payload['by_direction'].get('short', {}).get('expectancy', 0.0)
    print(f'| {cid} | {long_e:+.2f} | {short_e:+.2f} | {long_e - short_e:+.2f} |')
"
```

(Skip the by-direction step if Phase 1 winner was `[long]` only — there are no shorts in Phase 2.)

- [ ] **Step 7: Edit `docs/superpowers/specs/2026-05-06-ablation-grid-design.md`**

Fill §6.3 with:
- Run metadata block (date, cache state, wall time, code SHA, pinned `direction_modes`)
- Per-cell summary table (32 rows from `summary.csv`)
- Axis sensitivity table (paste from `grid.md`)
- Top 3 cells table (from Step 6 output)
- Recommended default config block (the §6.3 winner's axis values, formatted as YAML)
- By-direction breakdown table (if applicable)

Fill §6.4 — choose one:
- `[x] Defaults updated.` — if the winner's expectancy is high enough that the user wants to ship it as new `TradeRules()` defaults. Reasoning explains the threshold.
- `[x] No update.` — if the winner is borderline (e.g., expectancy < +0.10R) or the user wants to sweep further before locking. Reasoning identifies the follow-up.

- [ ] **Step 8: Commit the §6.3 + §6.4 fill**

```bash
git add docs/superpowers/specs/2026-05-06-ablation-grid-design.md
git commit -m "$(cat <<'EOF'
docs(spec): ablation grid Phase 2 results — §6.3 + §6.4 filled

Phase 2 ran 32 cells with direction_modes pinned at Phase 1 winner.
Best cell: <cell_id>, expectancy <X.XX>R, PF <X.XX>, n=<N>.
Verdict: <Defaults updated|No update>. <one-sentence rationale>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Replace placeholders with the actual outcome.)

---

## Task 10: Final review + bump to v0.5.6

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/wave_alpha/__init__.py`
- Modify: `uv.lock`

This task runs regardless of Phase 1 verdict. Even a NO-GO Phase 1 ships the harness + the spec record + version bump (the harness is reusable, and the negative finding is itself documented work).

- [ ] **Step 1: Final test pass**

```bash
uv run pytest
```

Expected: all green (475+ tests including new grid + CLI tests).

- [ ] **Step 2: Lint**

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Bump `pyproject.toml`**

Edit `pyproject.toml`. Change `version = "0.5.5"` to:

```toml
version = "0.5.6"
```

- [ ] **Step 4: Bump `src/wave_alpha/__init__.py`**

Edit `src/wave_alpha/__init__.py`. Change `__version__ = "0.5.5"` to `__version__ = "0.5.6"`.

- [ ] **Step 5: Refresh the lock**

```bash
uv lock
```

Expected: `Updated wave-alpha v0.5.5 -> v0.5.6`.

- [ ] **Step 6: Verify the bump**

```bash
uv run wave-alpha version
```

Expected: prints `0.5.6`.

- [ ] **Step 7: Commit the bump**

```bash
git add pyproject.toml uv.lock src/wave_alpha/__init__.py
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.5.6

Adds the ablation grid harness (`wave-alpha backtest grid`). Closes
the v0.5 finishing-step commitment from main design §8 and the
trade-sim layer §13 roadmap, conditional on Phase 1 verdict (see
docs/superpowers/specs/2026-05-06-ablation-grid-design.md §6.2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Print branch summary**

```bash
git log --oneline main..HEAD
```

Expected: ~10–12 commits — spec, plan, T1–T7 features, T8 Phase 1 fill, optional T9 (Phase 2 fill + config update), T10 bump.

---

## Final Review Boundary

```bash
uv run pytest
uv run ruff check .
git log --oneline main..HEAD
```

Expected: full suite green, lint clean, branch summary ready for merge to `main` (merge-commit per project convention).

---

## Self-Review Checklist (controller)

- [ ] **Spec coverage:** every spec section has a task — §3.1 cartesian product → T1; §3.2 Phase 1 axes → T6 yaml + T8 run; §3.3 trigger → T3 + T8 §6.2 fill; §3.4 Phase 2 axes → T6 yaml + T9 run; §3.5 per-cell → T4 orchestration + T7 CLI artifacts; §3.6 cross-cell aggregation → T2; §3.7 CLI → T7; §3.8 output structure → T7 + T8/T9; §3.9 lookahead → preserved (no new fetch sites, T7 routes through `run_trade_layer`); §4 tests → T1–T7 each include their tests; §5 file structure → T1–T7 file lists; §6 results → T8 + T9; §7 fix ledger → empty unless cells expose sim bugs; §8 risks → no implementation tasks (documented); §9 deferred → no implementation tasks; §10 roadmap → T10 version bump.
- [ ] **Placeholder scan:** every step has actual code or commands. The Phase 2 task (T9) explicitly hedges "if Phase 1 verdict is NO-GO, skip this entire task" — that's a documented branch, not a placeholder. The §6 spec edits in T8/T9 use template fields like `<X.XX>R` only inside the commit message text, where the engineer is expected to substitute actual numbers from the run.
- [ ] **Type consistency:** `GridAxis(name, levels)` matches across T1 + T2 + T3 + T6. `GridResult.rows: list[GridResultRow]` matches across T2–T5 and T7. `phase1_trigger(...) -> tuple[Literal["GO","NO-GO"], str]` is the single signature used in T3 and T5. `BacktestConfig` and `OHLCVProvider` are existing types imported in T4. `TradeRules.model_validate(cell.trade_rules_kwargs())` is the conversion used in T4 (`run_grid`) and T7 (CLI per-cell rules reconstruction) — same shape both places.
- [ ] **No phantom symbols:** `_short_axis_name`, `_short_axis_value`, `_mean`, `_pf_or_zero`, `SAMPLE_SIZE_FLOOR`, `PHASE1_EXPECTANCY_THRESHOLD`, `PHASE1_PF_THRESHOLD` are defined in T1/T2/T3 and consumed in T2/T3/T5. `_json_default`, `_warn_if_4h_window_partial`, `_default_provider`, `load_universe` are pre-existing helpers in `cli/app.py` — verified by reading the file before plan-write.
- [ ] **TDD:** T1–T5 + T7 are failing-test-first per step. T6 (configs) and T8/T9 (operational runs) and T10 (bump) are non-TDD because they are configuration / execution / metadata respectively.
- [ ] **Frequent commits:** ~10 commits across 10 tasks. Each is self-contained and revertible. T9 has its own intermediate commit (config pin) so the Phase 2 result fill is a clean tail commit.
