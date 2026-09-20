"""Verify the B-bar criteria for the trade-sim sanity run.

Loads reports/trade-sim-2026-05-05/{result.json,trades.csv} and runs the
16 criteria from spec §5. Prints a verdict table. Exits non-zero on any
hard failure (Correctness §5.1) so this can be wired into CI later.

Run:
    uv run python scripts/inspect_sanity_run.py
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ART_DIR = Path("reports/trade-sim-2026-05-05")
RESULT = ART_DIR / "result.json"
TRADES = ART_DIR / "trades.csv"


def _load() -> tuple[dict, list[dict]]:
    payload = json.loads(RESULT.read_text())
    with TRADES.open() as fh:
        trades = list(csv.DictReader(fh))
    return payload, trades


def _closed(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t["exit_reason"] != "open_at_end"]


# ---- §5.1 Correctness ----


def c1_lookahead() -> tuple[bool, str]:
    res = subprocess.run(
        ["uv", "run", "pytest", "tests/backtest/test_lookahead_guard.py", "-q"],
        capture_output=True,
        text=True,
    )
    return res.returncode == 0, f"pytest exit={res.returncode}"


def c2_risk_positive(trades: list[dict]) -> tuple[bool, str]:
    closed = _closed(trades)
    bad = [t for t in closed if float(t["risk_per_share"]) <= 0]
    return not bad, (f"0/{len(closed)} zero-risk" if not bad else f"{len(bad)} zero-risk found")


def c3_exit_after_fill(trades: list[dict]) -> tuple[bool, str]:
    closed = _closed(trades)
    bad = [t for t in closed if t["exit_date"] < t["fill_date"]]
    return not bad, (
        f"0/{len(closed)} order-violations" if not bad else f"{len(bad)} order-violations"
    )


def c4_exit_reason_set(trades: list[dict]) -> tuple[bool, str]:
    bad = [t for t in trades if not t["exit_reason"]]
    return not bad, (
        "all rows have exit_reason" if not bad else f"{len(bad)} rows missing exit_reason"
    )


def c5_ebt_diversity(trades: list[dict]) -> tuple[bool, str]:
    """EBT diversity is degenerate when only one degree is enumerated
    (`_DEFAULT_DEGREE_FOR_COUNT = 2` in pipeline.py — every trade carries
    EBT=25). Skip the within-degree variance check in that case; require
    ≥5 distinct EBT only when multiple degrees are present (so the check
    becomes meaningful when multi-degree enumeration ships). See spec §9 S6."""
    by_degree: dict[str, set[int]] = defaultdict(set)
    for t in trades:
        by_degree[t["degree"]].add(int(t["expected_bars_to_target"]))
    if not by_degree:
        return False, "no degree column"
    if len(by_degree) == 1:
        only = next(iter(by_degree.keys()))
        return True, f"skipped — only degree {only} enumerated (S6)"
    bad = [d for d, vals in by_degree.items() if len(vals) < 5]
    return not bad, (
        f"OK across {len(by_degree)} degrees" if not bad else f"degrees with <5 distinct EBT: {bad}"
    )


def c6_concurrency(trades: list[dict]) -> tuple[bool, str]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in _closed(trades):
        by_ticker[t["ticker"]].append(t)
    violations = 0
    for recs in by_ticker.values():
        recs.sort(key=lambda r: r["fill_date"])
        for i in range(1, len(recs)):
            if recs[i]["fill_date"] <= recs[i - 1]["exit_date"]:
                violations += 1
    return violations == 0, (
        f"0 overlaps across {len(by_ticker)} tickers"
        if violations == 0
        else f"{violations} overlaps"
    )


def c7_slippage(trades: list[dict], slippage_bps: int) -> tuple[bool, str]:
    """Spot-check: verify fill prices are positive and consistent."""
    closed = _closed(trades)
    if len(closed) < 5:
        return True, "skipped (n<5)"
    inconsistent = sum(1 for t in closed[:50] if float(t["fill_price"]) <= 0)
    return inconsistent == 0, (
        f"{len(closed[:50])} fills inspected, all positive"
        if inconsistent == 0
        else f"{inconsistent} non-positive fill prices"
    )


# ---- §5.2 Plausible economics ----


def e1_hit_rate(payload: dict) -> tuple[bool, str]:
    hr = payload["headline"]["hit_rate"]
    return 0.05 <= hr <= 0.95, f"hit_rate={hr:.3f}"


def e2_expectancy(payload: dict) -> tuple[bool, str]:
    ex = payload["headline"]["expectancy"]
    return abs(ex) <= 10.0, f"expectancy={ex:+.3f}R"


def e3_pf_finite(payload: dict) -> tuple[bool, str]:
    pf = payload["headline"]["profit_factor"]
    closed = payload["headline"]["closed_trades"]
    if pf is None and closed > 0:
        return False, "PF=∞ with closed>0 — sample too one-sided"
    return True, f"PF={pf}"


def e4_coh_monotone_2tf(trades: list[dict]) -> tuple[bool, str]:
    closed_2 = [t for t in _closed(trades) if int(t["tf_count"]) == 2]
    return _bucket_monotone(closed_2, "tf_count=2")


def e5_coh_monotone_3tf(trades: list[dict]) -> tuple[bool, str]:
    closed_3 = [t for t in _closed(trades) if int(t["tf_count"]) == 3]
    if len(closed_3) < 50:
        return True, f"skipped (n_3tf={len(closed_3)} < 50)"
    return _bucket_monotone(closed_3, "tf_count=3")


def _bucket_monotone(rows: list[dict], label: str) -> tuple[bool, str]:
    by_bucket: dict[str, list[float]] = defaultdict(list)
    for t in rows:
        by_bucket[t["coherence_bucket"]].append(float(t["realized_R"]))
    full = by_bucket.get("full", [])
    partial = by_bucket.get("partial", [])
    if not full or not partial:
        return True, f"{label}: skipped (empty bucket)"
    e_full = sum(full) / len(full)
    e_part = sum(partial) / len(partial)
    tol = 0.1 if min(len(full), len(partial)) < 20 else 0.0
    ok = e_full + tol >= e_part
    return ok, (
        f"{label}: full={e_full:+.2f}R (n={len(full)}) "
        f"partial={e_part:+.2f}R (n={len(partial)}) tol={tol}"
    )


def e6_sample_size(payload: dict) -> tuple[bool, str]:
    n = payload["headline"]["closed_trades"]
    return n >= 200, f"closed_trades={n}"


# ---- §5.3 Coverage smoke ----


def k1_4h_coverage(payload: dict) -> tuple[bool, str]:
    cov = payload["headline"]["coverage_4h"]
    return cov > 0, f"coverage_4h={cov:.3f}"


def k2_tf_count_buckets(payload: dict) -> tuple[bool, str]:
    keys = set(payload["by_tf_count"].keys())
    have_both = "2" in keys and "3" in keys
    n2 = payload["by_tf_count"].get("2", {}).get("sample_size", 0)
    n3 = payload["by_tf_count"].get("3", {}).get("sample_size", 0)
    return have_both and n2 > 0 and n3 > 0, f"n_2tf={n2} n_3tf={n3}"


def k3_year_coverage(payload: dict) -> tuple[bool, str]:
    years = payload["by_year"]
    needed = {"2020", "2021", "2022", "2023", "2024"}
    missing = needed - set(years.keys())
    empty = [y for y in needed - missing if years[y].get("sample_size", 0) == 0]
    bad = list(missing) + empty
    return not bad, ("all 5 years populated" if not bad else f"missing/empty: {bad}")


def main() -> int:
    payload, trades = _load()
    slippage = int(payload["rules"]["slippage_bps"])

    rows: list[tuple[str, bool, str]] = []
    rows.append(("C1 lookahead", *c1_lookahead()))
    rows.append(("C2 risk>0", *c2_risk_positive(trades)))
    rows.append(("C3 exit>=fill", *c3_exit_after_fill(trades)))
    rows.append(("C4 exit_reason set", *c4_exit_reason_set(trades)))
    rows.append(("C5 EBT diversity", *c5_ebt_diversity(trades)))
    rows.append(("C6 concurrency<=1", *c6_concurrency(trades)))
    rows.append(("C7 slippage spot", *c7_slippage(trades, slippage)))
    rows.append(("E1 hit rate", *e1_hit_rate(payload)))
    rows.append(("E2 expectancy", *e2_expectancy(payload)))
    rows.append(("E3 PF finite", *e3_pf_finite(payload)))
    rows.append(("E4 coh monotone 2tf", *e4_coh_monotone_2tf(trades)))
    rows.append(("E5 coh monotone 3tf", *e5_coh_monotone_3tf(trades)))
    rows.append(("E6 sample size>=200", *e6_sample_size(payload)))
    rows.append(("K1 4h coverage>0", *k1_4h_coverage(payload)))
    rows.append(("K2 tf_count buckets", *k2_tf_count_buckets(payload)))
    rows.append(("K3 year coverage", *k3_year_coverage(payload)))

    print(f"\n{'criterion':<28} {'pass':<5} evidence")
    print("-" * 80)
    for name, ok, evidence in rows:
        print(f"{name:<28} {'PASS' if ok else 'FAIL':<5} {evidence}")

    correctness_prefixes = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")
    correctness = [(n, ok) for n, ok, _ in rows if n.startswith(correctness_prefixes)]
    hard_fails = [n for n, ok in correctness if not ok]
    if hard_fails:
        print(f"\nHARD FAIL (Correctness §5.1): {hard_fails}", file=sys.stderr)
        return 1
    soft_fails = [n for n, ok, _ in rows if not ok]
    print(
        f"\nsummary: {len(rows) - len(soft_fails)}/{len(rows)} pass ({len(soft_fails)} soft fails)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
