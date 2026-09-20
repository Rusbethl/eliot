"""Compute per-degree empirical EBT (median + p75) from the
2026-05-08 multi-degree backtest's trades.csv.

Filters to winners (exit_reason == "target"), groups by degree,
applies the >=100-winner threshold gate, and prints proposed
_EBT_MEDIAN_BY_DEGREE and _EBT_P75_BY_DEGREE dict updates.

Run:
    uv run python scripts/inspect_ebt_refit.py

The script is read-only: it does NOT edit src/wave_alpha/trades/derive.py.
The verdict spec author applies the proposed dict updates manually
based on the script's output.

Spec: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

TRADES_CSV = Path("reports/ebt-refit-2026-05-08/trades.csv")
THRESHOLD = 100  # min winners per degree to ship empirical fit
DEGREES_IN_SCOPE = (1, 3)  # degree 2 is out of scope (kept at 17/29)


def _percentile(sorted_values: list[int], p: float) -> int:
    """Inclusive linear interpolation; rounds to int (bars are integer-valued)."""
    if not sorted_values:
        raise ValueError("empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def main() -> int:
    if not TRADES_CSV.exists():
        print(f"ERROR: {TRADES_CSV} does not exist. Run Phase A first.")
        return 1

    by_degree_winners: dict[int, list[int]] = defaultdict(list)
    total_trades = 0
    total_target_hits = 0

    with TRADES_CSV.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total_trades += 1
            if row["exit_reason"] != "target":
                continue
            total_target_hits += 1
            degree = int(row["degree"])
            bars_held = int(row["bars_held"])
            by_degree_winners[degree].append(bars_held)

    print(f"Total trades: {total_trades}")
    print(f"Target hits (winners): {total_target_hits}")
    print()
    print(f"{'degree':>6} | {'n_winners':>9} | {'median':>6} | {'p75':>4} | verdict")
    print(f"{'-' * 6}-+-{'-' * 9}-+-{'-' * 6}-+-{'-' * 4}-+--------")

    proposed_median: dict[int, int] = {0: 5, 2: 17, 4: 150}
    proposed_p75: dict[int, int] = {2: 29}

    for degree in sorted(by_degree_winners.keys()):
        bars_list = sorted(by_degree_winners[degree])
        n = len(bars_list)
        med = round(median(bars_list)) if bars_list else 0
        p75 = _percentile(bars_list, 0.75) if bars_list else 0

        if degree not in DEGREES_IN_SCOPE:
            verdict = "(out of scope)"
        elif n >= THRESHOLD:
            verdict = "SHIP"
            proposed_median[degree] = med
            proposed_p75[degree] = p75
        else:
            verdict = f"HOLD: n={n} < {THRESHOLD}"

        print(f"{degree:>6} | {n:>9} | {med:>6} | {p75:>4} | {verdict}")

    print()
    print("Proposed dict updates (apply manually to src/wave_alpha/trades/derive.py):")
    print(f"  _EBT_MEDIAN_BY_DEGREE: dict[int, int] = {dict(sorted(proposed_median.items()))}")
    print(f"  _EBT_P75_BY_DEGREE: dict[int, int] = {dict(sorted(proposed_p75.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
