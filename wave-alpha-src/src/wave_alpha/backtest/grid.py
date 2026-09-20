"""Ablation grid harness: cartesian-product TradeRules sweeps over
run_trade_layer.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Literal

from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer
from wave_alpha.backtest.trade_layer import TradeLayerResult
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.trades.models import TradeRules


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

    def __post_init__(self) -> None:
        axis_names = [ax.name for ax in self.axes]
        seen: set[str] = set()
        for name in axis_names:
            if name in seen:
                raise ValueError(f"duplicate axis name: {name!r}")
            seen.add(name)
        overlap = seen & self.fixed.keys()
        if overlap:
            raise ValueError(f"axis name(s) also in fixed: {sorted(overlap)!r}")

    def cells(self) -> Iterator[GridCell]:
        """Yield GridCell instances in row-major axis order."""
        level_lists = [axis.levels for axis in self.axes]
        for combo in product(*level_lists):
            axis_values = {axis.name: level for axis, level in zip(self.axes, combo, strict=True)}
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


# ---------------------------------------------------------------------------
# Cross-cell aggregation
# ---------------------------------------------------------------------------

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

    def axis_sensitivity(self) -> dict[str, dict[str, dict[str, float]]]:
        """For each axis, marginal mean of headline metrics at each level.

        Returns: {axis_name: {level_str: {metric_name: mean_value}}}
        Metrics: expectancy, profit_factor, closed_trades.
        Levels are stringified via _short_axis_value for stable keys.

        Profit-factor mean handling:
        - If a level has at least one finite-PF cell, the mean is the
          average of those finite cells (inf cells excluded so they don't
          drag the mean toward zero).
        - If ALL cells at a level have inf PF (every cell zero-loss), the
          mean surfaces as inf — uniformly degenerate-positive. Coercing
          to 0.0 would be the opposite of the truth.
        - If the level has no cells at all (impossible after the cartesian
          product but a defensive case), the mean is 0.0.

        Raises ValueError if rows do not all share the same axis set.
        """
        if not self.rows:
            return {}
        first_axes = list(self.rows[0].cell.axis_values.keys())
        expected = set(first_axes)
        for i, row in enumerate(self.rows):
            if set(row.cell.axis_values.keys()) != expected:
                raise ValueError(
                    f"row {i} has axes {set(row.cell.axis_values.keys())}; "
                    f"expected {expected}. axis_sensitivity requires all rows "
                    f"share the same axis set."
                )
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
                    "profit_factor": _pf_mean(group),
                    "closed_trades": _mean(r.result.closed_trades for r in group),
                }
                for level_key, group in level_groups.items()
            }
        return out


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _pf_mean(group: list[GridResultRow]) -> float:
    """Mean profit_factor for a group of rows.

    A finite mean if any cell has finite PF (inf cells excluded so they
    don't drag the average toward zero). If every cell is degenerate
    (PF == inf), surface inf — the level is uniformly zero-loss.
    Empty group → 0.0 (defensive).
    """
    pfs = [row.result.profit_factor for row in group]
    if not pfs:
        return 0.0
    finite = [pf for pf in pfs if pf != float("inf")]
    if not finite:
        return float("inf")
    return sum(finite) / len(finite)


# ---------------------------------------------------------------------------
# Phase-1 GO/NO-GO trigger
# ---------------------------------------------------------------------------

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
        pf = row.result.profit_factor
        # Treat inf PF as qualifying (zero-loss cell — passes by definition,
        # but we surface it explicitly rather than silently coercing).
        pf_qualifies = pf == float("inf") or pf >= pf_threshold
        if row.result.expectancy >= expectancy_threshold or pf_qualifies:
            qualifying.append(row)
    if not qualifying:
        # Identify the best-effort summary for the NO-GO reason
        best_eligible = result.winner()
        if best_eligible is None:
            return (
                "NO-GO",
                f"No cell met the sample-size floor (closed_trades >= {sample_floor}).",
            )
        best_pf = best_eligible.result.profit_factor
        best_pf_str = "∞" if best_pf == float("inf") else f"{best_pf:.2f}"
        return (
            "NO-GO",
            f"Best eligible cell: expectancy={best_eligible.result.expectancy:+.3f}R, "
            f"PF={best_pf_str}, "
            f"closed_trades={best_eligible.result.closed_trades}. "
            f"Threshold: expectancy >= +{expectancy_threshold:.2f}R "
            f"OR PF >= {pf_threshold:.2f}.",
        )
    top = max(qualifying, key=lambda r: r.result.expectancy)
    top_pf = top.result.profit_factor
    top_pf_str = "∞" if top_pf == float("inf") else f"{top_pf:.2f}"
    return (
        "GO",
        f"Best qualifying cell: expectancy={top.result.expectancy:+.3f}R, "
        f"PF={top_pf_str}, "
        f"closed_trades={top.result.closed_trades}. "
        f"Cell axes: {top.cell.axis_values}.",
    )


# ---------------------------------------------------------------------------
# Grid orchestration
# ---------------------------------------------------------------------------


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
