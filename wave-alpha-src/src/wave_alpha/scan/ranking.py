from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from wave_alpha.scan.models import ScanRow

SortKey = str  # "score" | "rr" | "ticker" | "coherence" | "right_edge"


def sort_by(rows: Iterable[ScanRow], key: SortKey, *, reverse: bool = True) -> list[ScanRow]:
    """Stable sort. Rows with None on the sort key go last (regardless of reverse)."""
    rows_list = list(rows)
    if key == "ticker":
        return sorted(rows_list, key=lambda r: r.ticker, reverse=reverse)

    def numeric(r: ScanRow) -> float | None:
        if key == "score":
            return r.top_score
        if key == "rr":
            return float(r.signal_rr) if r.signal_rr is not None else None
        if key == "coherence":
            return r.coherence
        if key == "right_edge":
            return r.right_edge_proba
        return None

    none_rows = [r for r in rows_list if numeric(r) is None]
    val_rows = [r for r in rows_list if numeric(r) is not None]
    val_rows.sort(key=lambda r: numeric(r) or 0.0, reverse=reverse)
    return val_rows + none_rows


def filter_signals(rows: Iterable[ScanRow]) -> list[ScanRow]:
    """Keep only rows with a derived trade signal."""
    return [r for r in rows if r.has_signal]


Bias = Literal["any", "long", "short"]
_BIAS_TO_DIRECTION = {"long": "up", "short": "down"}


def filter_by_bias(rows: Iterable[ScanRow], bias: Bias) -> list[ScanRow]:
    """Keep rows whose direction matches the requested bias.
    "any" returns rows unchanged. "long" / "short" map to the engine's
    "up" / "down" direction values. Rows with `direction=None` are
    excluded for "long" / "short" but included for "any"."""
    if bias == "any":
        return list(rows)
    target = _BIAS_TO_DIRECTION[bias]
    return [r for r in rows if r.direction == target]


def filter_by_min_rr(rows: Iterable[ScanRow], min_rr: float) -> list[ScanRow]:
    """Keep rows whose `signal_rr` is at least `min_rr`.
    Rows with no signal (signal_rr is None) are dropped."""
    return [r for r in rows if r.signal_rr is not None and float(r.signal_rr) >= min_rr]
