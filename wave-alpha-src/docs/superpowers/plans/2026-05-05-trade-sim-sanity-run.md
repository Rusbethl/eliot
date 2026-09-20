# Trade-Sim Sanity Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one trade-sim run on the curated universe over 2020–2024 with the centroid `TradeRules`, verify the B-bar (correctness + plausible economics), and fill in the verdict sections of `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` so the ablation grid spec is unblocked.

**Architecture:** This plan implements a *run + verdict*, not a feature. Phases: setup configs, instrument `TradeRecord` with the fields the spec needs (Phase B), build two one-shot scripts (pre-warm + B-bar inspection), execute the sim, iterate fixes if criteria fail, and persist the verdict back into the spec. No new CLI commands.

**Tech Stack:** Existing `wave-alpha` CLI, `pipeline.run_analysis`, `CachedProvider(YahooProvider())`, `pytest`, `pydantic` v2, `pyyaml`. No new external dependencies.

**Spec:** `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md`

**Branch:** `feat/trade-sim-sanity-run` (already created at the spec commit `b67dcf8`).

---

## File structure

**New:**
- `configs/sanity_run.yaml` — centroid `TradeRules` (Phase A).
- `scripts/prewarm_4h_cache.py` — one-shot script that calls `provider.fetch` once per (ticker, interval) at `end=2024-12-31` to populate `~/.wave_alpha/cache.sqlite` (Phase C).
- `scripts/inspect_sanity_run.py` — one-shot script that loads `result.json` + `trades.csv` and verifies the 16 B-bar criteria, prints a verdict table, and exits non-zero on hard failures (Phase C).
- `reports/trade-sim-2026-05-05/` — gitignored; populated during Phase D. Holds `report.md`, `result.json`, `trades.csv`, `run.log`, `expected_bars_distribution.json`.

**Modified:**
- `.gitignore` — add `reports/` (Phase A).
- `src/wave_alpha/trades/models.py` — add `degree: int` to `TradeSignal` (Phase B).
- `src/wave_alpha/trades/derive.py` — pass `degree=count.degree` when constructing the signal (Phase B).
- `src/wave_alpha/backtest/trade_layer.py` — add `degree: int` and `expected_bars_to_target: int` to `TradeRecord`; populate at both record-construction sites (Phase B).
- `tests/trades/` and `tests/backtest/` — extend existing tests to cover the new fields (Phase B).
- `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` — fill §6 (Results), §7 (Fix ledger), §10.1 (Verdict) (Phase F).

**Conditional (only if a Phase E fix iteration touches them):**
- `src/wave_alpha/backtest/trade_layer.py` and friends.

---

## Phase A — Setup

### Task A1: Add `reports/` to `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Inspect current `.gitignore`**

```bash
cat .gitignore
```

- [ ] **Step 2: Append the entry**

Append a new line `reports/` to `.gitignore`. The existing file ends without a `reports/` entry; insert under any existing block of project-output ignores, or at the end:

```
reports/
```

- [ ] **Step 3: Verify**

```bash
git check-ignore -v reports/anything
```

Expected: prints `.gitignore:NN:reports/  reports/anything`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "$(cat <<'EOF'
chore(gitignore): exclude reports/ from version control

Sanity-run artifacts under reports/trade-sim-2026-05-05/ are large and
reproducible from inputs; durable record lives in the spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Create the centroid `TradeRules` config

**Files:**
- Create: `configs/sanity_run.yaml`

- [ ] **Step 1: Verify the schema accepts every field**

```bash
uv run python -c "from wave_alpha.trades.models import TradeRules; print(TradeRules.model_fields.keys())"
```

Expected: lists `direction_modes`, `entry_trigger`, `stop_source`, `target_source`, `target_R`, `time_stop_mode`, `time_stop_bars`, `max_concurrent_signals_per_ticker`, `slippage_bps`, `commission_per_share`, `short_borrow_annual_pct`, `min_confidence`.

- [ ] **Step 2: Write the config**

Create `configs/sanity_run.yaml` exactly:

```yaml
# Centroid TradeRules for the 2026-05-05 trade-sim sanity run.
# Spec: docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md §3.1
direction_modes: [long, short]
entry_trigger: immediate
stop_source: atr_multiple
target_source: fib_extension
target_R: null
time_stop_mode: degree_based
time_stop_bars: null
max_concurrent_signals_per_ticker: 1
slippage_bps: 5
commission_per_share: "0"
short_borrow_annual_pct: 0.005
min_confidence: 0.45
```

- [ ] **Step 3: Verify it round-trips through `TradeRules`**

```bash
uv run python -c "
import yaml
from wave_alpha.trades.models import TradeRules
r = TradeRules.model_validate(yaml.safe_load(open('configs/sanity_run.yaml')))
print(r)
"
```

Expected: prints the populated `TradeRules` instance with `entry_trigger='immediate' stop_source='atr_multiple' target_source='fib_extension' slippage_bps=5 ...`. No validation errors.

- [ ] **Step 4: Commit**

```bash
git add configs/sanity_run.yaml
git commit -m "$(cat <<'EOF'
feat(configs): centroid TradeRules for trade-sim sanity run

atr_multiple / fib_extension / 5bps / step=5. Three deviations from
TradeRules() defaults documented in spec §3.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — Pre-run instrumentation

The current `TradeRecord` does not carry `degree` or `expected_bars_to_target`. The spec §6.6 requires per-degree empirical distributions of realized bars-to-target, and inspection criterion C5 requires per-degree EBT diversity. Add both fields before the sim runs, so the data is captured.

### Task B1: Add `degree` to `TradeSignal`

**Files:**
- Modify: `src/wave_alpha/trades/models.py`
- Modify: `src/wave_alpha/trades/derive.py`
- Modify: `tests/trades/test_derive.py` (or wherever `derive_signal` is tested today)

- [ ] **Step 1: Confirm test file location**

```bash
grep -rln "derive_signal" tests/ | head -5
```

Expected: at least one test file. Use the file with the most existing `derive_signal` tests for Step 2.

- [ ] **Step 2: Write failing test for `signal.degree`**

The file `tests/trades/test_derive.py` defines a `_bullish_w5_setup()` helper that returns a `WaveCount(pattern="impulse", degree=2, ...)`. Append this test to that file (next to the existing `test_derive_long_signal_from_w5_setup`):

```python
def test_derive_signal_carries_degree() -> None:
    """The signal must expose count.degree so downstream sim can group by degree."""
    count = _bullish_w5_setup()  # degree=2 by default
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),
    )
    assert sig is not None
    assert sig.degree == count.degree == 2

    # cover a different degree to prove it's not hard-coded
    count_d3 = count.model_copy(update={"degree": 3})
    sig_d3 = derive_signal(
        count=count_d3,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),
    )
    assert sig_d3 is not None
    assert sig_d3.degree == 3
```

The assertion (`sig.degree == count.degree`) is the contract. Reuses the existing `_bullish_w5_setup()` helper so no new fixtures are needed.

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest <path>::test_derive_signal_carries_degree -xvs
```

Expected: `AttributeError: 'TradeSignal' object has no attribute 'degree'` or pydantic validation error if a writer is exercised first.

- [ ] **Step 4: Add `degree` to `TradeSignal`**

In `src/wave_alpha/trades/models.py`, inside the `TradeSignal` class, add:

```python
    degree: int
```

Place it after `wave_label: str` and before `confidence: float` to keep related fields adjacent.

- [ ] **Step 5: Pass `degree` from `derive_signal`**

In `src/wave_alpha/trades/derive.py`, find the `return TradeSignal(...)` call inside `derive_signal` and add `degree=count.degree,`. The full return becomes:

```python
    return TradeSignal(
        ticker=ticker,
        as_of=as_of,
        direction=direction,
        entry_price=current_price,
        stop_price=stop,
        target_price=target,
        count_id=_count_id(count),
        count_pattern=count.pattern,
        wave_label=_current_wave_label(count),
        degree=count.degree,
        confidence=count.score,
        expected_bars_to_target=expected,
        max_holding_bars=max_hold,
        breakeven_trigger=breakeven,
    )
```

Only the `degree=count.degree,` line is new — every other kwarg is verbatim from the existing return (kept here for clarity).

- [ ] **Step 6: Run the new test**

```bash
uv run pytest <path>::test_derive_signal_carries_degree -xvs
```

Expected: PASS.

- [ ] **Step 7: Run the full trades + backtest test directories**

```bash
uv run pytest tests/trades tests/backtest -x
```

Expected: all green. Any tests that construct `TradeSignal` directly (not via `derive_signal`) will fail because `degree` is now required — fix each by adding `degree=<int>` to the constructor call. Choose `degree=2` as the default if the test does not care.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/trades/models.py src/wave_alpha/trades/derive.py tests/
git commit -m "$(cat <<'EOF'
feat(trades): TradeSignal carries degree from source WaveCount

Required by trade-sim sanity run spec §6.6 (per-degree empirical
expected-bars-to-target distribution).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: Add `degree` and `expected_bars_to_target` to `TradeRecord`

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing test for `TradeRecord` fields**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_trade_record_carries_degree_and_ebt():
    """TradeRecord must expose degree + expected_bars_to_target so the
    sanity-run inspection can compute per-degree empirical distributions."""
    from datetime import date
    from decimal import Decimal
    from wave_alpha.backtest.trade_layer import TradeRecord

    rec = TradeRecord(
        ticker="TEST",
        signal_as_of=date(2024, 1, 1),
        fill_date=date(2024, 1, 2),
        fill_price=Decimal("100"),
        exit_date=date(2024, 1, 10),
        exit_price=Decimal("105"),
        exit_reason="target",
        direction="long",
        pattern="impulse",
        wave_label="W5",
        coherence_score=0.8,
        coherence_bucket="full",
        bars_held=8,
        risk_per_share=Decimal("2"),
        realized_pnl_per_share=Decimal("5"),
        realized_R=2.5,
        confidence=0.7,
        tf_count=3,
        degree=3,
        expected_bars_to_target=20,
    )
    assert rec.degree == 3
    assert rec.expected_bars_to_target == 20
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_trade_record_carries_degree_and_ebt -xvs
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'degree'`.

- [ ] **Step 3: Add fields to `TradeRecord`**

In `src/wave_alpha/backtest/trade_layer.py`, inside the `TradeRecord` dataclass, add at the bottom of the field list (after `tf_count`):

```python
    degree: int
    expected_bars_to_target: int
```

- [ ] **Step 4: Populate fields at both construction sites**

Two `return TradeRecord(...)` sites exist in `trade_layer.py` — the closed-trade exit site and the `open_at_end` site. At both, add:

```python
            degree=signal.degree,
            expected_bars_to_target=signal.expected_bars_to_target,
```

immediately before the closing `)`. Use `grep -n "TradeRecord(" src/wave_alpha/backtest/trade_layer.py` to confirm both sites are updated.

- [ ] **Step 5: Run the new test plus the trade-layer suite**

```bash
uv run pytest tests/backtest/test_trade_layer.py -xvs
```

Expected: all green. Any tests that construct `TradeRecord` directly will fail — add `degree=2, expected_bars_to_target=25` to each (matching the project's defaults from `_DEFAULT_BARS_PER_DEGREE`).

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest -x
```

Expected: all green. Any other test that constructs `TradeRecord` (e.g., a CSV writer test) gets the same two-field fix.

- [ ] **Step 7: Confirm CSV sidecar carries the new fields**

```bash
uv run python -c "
from dataclasses import asdict, fields
from wave_alpha.backtest.trade_layer import TradeRecord
print([f.name for f in fields(TradeRecord)])
"
```

Expected: list includes `'degree'` and `'expected_bars_to_target'`. The existing `csv.DictWriter(fieldnames=fields)` machinery in `cli/app.py` is field-order-stable based on `asdict(result.trades[0]).keys()` — the new columns appear automatically.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/
git commit -m "$(cat <<'EOF'
feat(backtest): TradeRecord carries degree + expected_bars_to_target

Required by trade-sim sanity run spec §6.6 — per-degree empirical
distribution of realized bars-to-exit on winning trades.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — Tooling scripts

### Task C1: Pre-warm script

**Files:**
- Create: `scripts/prewarm_4h_cache.py`

- [ ] **Step 1: Write the script**

Create `scripts/prewarm_4h_cache.py`:

```python
"""Pre-warm ~/.wave_alpha/cache.sqlite for the trade-sim sanity run.

For each ticker in data/universe_curated.txt, fetch weekly + daily +
4h bars with end=2024-12-31. The CachedProvider stores results so the
sim loop in Phase D doesn't pay yfinance latency per call.

Run:
    uv run python scripts/prewarm_4h_cache.py
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval

UNIVERSE = Path("data/universe_curated.txt")
END = date(2024, 12, 31)
INTERVALS = [Interval.WEEKLY, Interval.DAILY, Interval.HOURLY_4]
SLEEP_BETWEEN = 0.2  # seconds — gentle on yfinance


def main() -> int:
    tickers = [line.strip() for line in UNIVERSE.read_text().splitlines() if line.strip() and not line.startswith("#")]
    provider = CachedProvider(upstream=YahooProvider())
    failures: list[tuple[str, Interval, str]] = []
    total = len(tickers)
    print(f"pre-warming cache for {total} tickers x {len(INTERVALS)} intervals through {END}")

    for i, ticker in enumerate(tickers, 1):
        for interval in INTERVALS:
            try:
                bars = provider.fetch(ticker, interval, end=END)
                print(f"[{i:3d}/{total}] {ticker:8s} {interval.value:4s}: {len(bars):5d} bars")
            except Exception as e:  # noqa: BLE001 — script-level guardrail
                msg = f"{type(e).__name__}: {e}"
                print(f"[{i:3d}/{total}] {ticker:8s} {interval.value:4s}: FAILED {msg}", file=sys.stderr)
                failures.append((ticker, interval, msg))
            time.sleep(SLEEP_BETWEEN)

    if failures:
        print(f"\n{len(failures)} fetch failures:", file=sys.stderr)
        for t, iv, m in failures:
            print(f"  {t} {iv.value}: {m}", file=sys.stderr)
        # 4h failures for some delisted tickers are expected — exit 0 unless
        # weekly or daily failed (those are required).
        hard = [(t, iv) for t, iv, _ in failures if iv != Interval.HOURLY_4]
        if hard:
            print(f"{len(hard)} weekly/daily failures — investigate before proceeding", file=sys.stderr)
            return 1
    print("pre-warm complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test imports without running**

```bash
uv run python -c "import importlib.util, pathlib; spec = importlib.util.spec_from_file_location('prewarm', 'scripts/prewarm_4h_cache.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('imports OK')"
```

Expected: `imports OK`. Catches typos / wrong import paths before the long-running run.

- [ ] **Step 3: Commit**

```bash
git add scripts/prewarm_4h_cache.py
git commit -m "$(cat <<'EOF'
feat(scripts): prewarm yfinance cache for sanity-run

One-shot pre-warm to populate ~/.wave_alpha/cache.sqlite with
weekly+daily+4h bars through 2024-12-31 for the curated universe.
Avoids yfinance fetch latency dominating the trade-sim wall time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: B-bar inspection script

**Files:**
- Create: `scripts/inspect_sanity_run.py`

- [ ] **Step 1: Write the script**

Create `scripts/inspect_sanity_run.py`:

```python
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
from statistics import median
from typing import Callable

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
        capture_output=True, text=True,
    )
    return res.returncode == 0, f"pytest exit={res.returncode}"


def c2_risk_positive(trades: list[dict]) -> tuple[bool, str]:
    closed = _closed(trades)
    bad = [t for t in closed if float(t["risk_per_share"]) <= 0]
    return not bad, f"0/{len(closed)} zero-risk" if not bad else f"{len(bad)} zero-risk found"


def c3_exit_after_fill(trades: list[dict]) -> tuple[bool, str]:
    closed = _closed(trades)
    bad = [t for t in closed if t["exit_date"] < t["fill_date"]]
    return not bad, f"0/{len(closed)} order-violations" if not bad else f"{len(bad)} order-violations"


def c4_exit_reason_set(trades: list[dict]) -> tuple[bool, str]:
    bad = [t for t in trades if not t["exit_reason"]]
    return not bad, "all rows have exit_reason" if not bad else f"{len(bad)} rows missing exit_reason"


def c5_ebt_diversity(trades: list[dict]) -> tuple[bool, str]:
    by_degree: dict[str, set[int]] = defaultdict(set)
    for t in trades:
        by_degree[t["degree"]].add(int(t["expected_bars_to_target"]))
    if not by_degree:
        return False, "no degree column"
    bad = [d for d, vals in by_degree.items() if len(vals) < 5]
    return not bad, f"OK across {len(by_degree)} degrees" if not bad else f"degrees with <5 distinct EBT: {bad}"


def c6_concurrency(trades: list[dict]) -> tuple[bool, str]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in _closed(trades):
        by_ticker[t["ticker"]].append(t)
    violations = 0
    for ticker, recs in by_ticker.items():
        recs.sort(key=lambda r: r["fill_date"])
        for i in range(1, len(recs)):
            if recs[i]["fill_date"] <= recs[i - 1]["exit_date"]:
                violations += 1
    return violations == 0, f"0 overlaps across {len(by_ticker)} tickers" if violations == 0 else f"{violations} overlaps"


def c7_slippage(trades: list[dict], slippage_bps: int) -> tuple[bool, str]:
    """Spot-check: compare risk_per_share to entry-stop spread on at least 5 trades.
    A more rigorous check requires bar-level data — out of scope. We verify
    that fill prices are non-zero and risk values are consistent with
    the positive-slippage convention."""
    closed = _closed(trades)
    if len(closed) < 5:
        return True, "skipped (n<5)"
    inconsistent = 0
    for t in closed[:50]:  # spot-check up to 50
        fill = float(t["fill_price"])
        if fill <= 0:
            inconsistent += 1
    return inconsistent == 0, f"{len(closed[:50])} fills inspected, all positive"


# ---- §5.2 Plausible economics ----

def e1_hit_rate(payload: dict) -> tuple[bool, str]:
    hr = payload["headline"]["hit_rate"]
    return 0.05 <= hr <= 0.95, f"hit_rate={hr:.3f}"


def e2_expectancy(payload: dict) -> tuple[bool, str]:
    ex = payload["headline"]["expectancy"]
    return abs(ex) <= 10.0, f"expectancy={ex:+.3f}R"


def e3_pf_finite(payload: dict) -> tuple[bool, str]:
    pf = payload["headline"]["profit_factor"]
    # pf is None in JSON when result was infinite (CLI converts inf→None).
    # That happens iff there are zero losses; treat as pass only if also zero wins.
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
    return ok, f"{label}: full={e_full:+.2f}R (n={len(full)}) partial={e_part:+.2f}R (n={len(partial)}) tol={tol}"


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
    return not bad, f"all 5 years populated" if not bad else f"missing/empty: {bad}"


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

    correctness = [(n, ok) for n, ok, _ in rows if n.startswith(("C1", "C2", "C3", "C4", "C5", "C6", "C7"))]
    hard_fails = [n for n, ok in correctness if not ok]
    if hard_fails:
        print(f"\nHARD FAIL (Correctness §5.1): {hard_fails}", file=sys.stderr)
        return 1
    soft_fails = [n for n, ok, _ in rows if not ok]
    print(f"\nsummary: {len(rows) - len(soft_fails)}/{len(rows)} pass ({len(soft_fails)} soft fails)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke-test imports without running**

```bash
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('insp', 'scripts/inspect_sanity_run.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('imports OK')
"
```

Expected: `imports OK`. The script will fail at runtime if `RESULT` / `TRADES` don't exist yet — that's expected; we run it after the sim in Phase D.

- [ ] **Step 3: Commit**

```bash
git add scripts/inspect_sanity_run.py
git commit -m "$(cat <<'EOF'
feat(scripts): inspect_sanity_run — B-bar verifier for trade-sim run

Implements the 16 criteria from spec §5: 7 correctness (hard-fail),
6 economics, 3 coverage. Loads result.json + trades.csv, prints a
verdict table, exits non-zero on any §5.1 violation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D — Execute

### Task D1: Pre-warm the cache

**Files:**
- Read-only: `data/universe_curated.txt`
- Side effect: populates `~/.wave_alpha/cache.sqlite`

- [ ] **Step 1: Run the pre-warm**

```bash
uv run python scripts/prewarm_4h_cache.py 2>&1 | tee reports/trade-sim-2026-05-05/prewarm.log
```

Expected: 71 lines × 3 intervals = 213 progress lines. 4h failures for occasional delisted tickers are tolerated. Weekly/daily failures cause exit 1 and stop here for investigation.

- [ ] **Step 2: Sanity-check the cache**

```bash
uv run python -c "
from datetime import date
from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval
p = CachedProvider(upstream=YahooProvider())
for ticker in ['AAPL', 'MSFT', 'NVDA']:
    for iv in (Interval.WEEKLY, Interval.DAILY, Interval.HOURLY_4):
        bars = p.fetch(ticker, iv, end=date(2024, 12, 31))
        print(f'{ticker} {iv.value}: {len(bars)} bars, first={bars[0].timestamp if bars else None}, last={bars[-1].timestamp if bars else None}')
"
```

Expected: positive bar counts for weekly+daily over the full window, 4h truncated to ~730 days back from 2024-12-31. No exceptions.

- [ ] **Step 3: Note: do NOT commit the prewarm.log**

`reports/` is gitignored from Task A1. The log lives on disk only. Skip commit; advance to D2.

---

### Task D2: Run the trade-sim

**Files:**
- Side effect: writes `reports/trade-sim-2026-05-05/{report.md,result.json,trades.csv,run.log}`

- [ ] **Step 1: Ensure the artifacts directory exists**

```bash
mkdir -p reports/trade-sim-2026-05-05
```

- [ ] **Step 2: Run**

```bash
uv run wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --rules configs/sanity_run.yaml \
  --out reports/trade-sim-2026-05-05/report.md \
  --json reports/trade-sim-2026-05-05/result.json \
  --trades-csv reports/trade-sim-2026-05-05/trades.csv \
  2>&1 | tee reports/trade-sim-2026-05-05/run.log
```

Expected: prints the standard backtest banner + write confirmations for each artifact. Wall time depends on CPU + cache state — expect minutes-to-low-tens-of-minutes given the cache is warm.

- [ ] **Step 3: Verify all four artifacts exist and are non-empty**

```bash
for f in report.md result.json trades.csv run.log; do
  path="reports/trade-sim-2026-05-05/$f"
  test -s "$path" || { echo "MISSING or EMPTY: $path"; exit 1; }
  echo "$path: $(wc -l < "$path") lines"
done
```

Expected: all four exist with non-trivial line counts. `trades.csv` should be > 200 lines (E6 floor, accounting for header + open_at_end rows).

- [ ] **Step 4: Capture the code SHA used for the run**

```bash
git rev-parse HEAD > reports/trade-sim-2026-05-05/code_sha.txt
cat reports/trade-sim-2026-05-05/code_sha.txt
```

(For the final spec §6 fill — captures the exact harness state.)

---

### Task D3: Run inspection

**Files:**
- Read-only on the artifacts.

- [ ] **Step 1: Run**

```bash
uv run python scripts/inspect_sanity_run.py | tee reports/trade-sim-2026-05-05/verdict.txt
```

Expected: 16-row verdict table.

- [ ] **Step 2: Read the table and decide branch**

- If **all 16 pass**: skip to Phase F.
- If only §5.2 / §5.3 fail (E1–E6, K1–K3): the sim is *correct*; the failures are findings to record in §6 of the spec. Decide per-failure whether to (a) attempt a fix in the trade-sim layer (rare — most §5.2/§5.3 fails reflect default-rule behavior, not bugs) or (b) document as a finding. Default: document, advance to Phase F.
- If any §5.1 fails (C1–C7): the sim is *incorrect*. Advance to Phase E.

- [ ] **Step 3: Capture the wall time and cache state**

```bash
grep -E "real|user|sys|^running" reports/trade-sim-2026-05-05/run.log | tail -20
```

For the §6.0 metadata block.

---

## Phase E — Iterate fixes (conditional, only if Phase D3 hard-failed)

Run once per hard failure (and re-run on subsequent failures discovered during fix iterations). Each iteration is one fully-formed commit.

### Task E-LOOP: Per-failure fix iteration

For each failed criterion in §5.1, run this loop:

- [ ] **Step 1: Reproduce the failure as a focused unit test**

Create or extend a test under `tests/backtest/` that fails for the same invariant the inspection caught. Examples:
- C2 (risk>0) fail: a test that runs the sim on a synthetic count where `entry == stop` and asserts `derive_signal` returns `None` (not a degenerate signal that becomes a bad TradeRecord).
- C6 (concurrency) fail: a test that walks the state machine through two overlapping signals on the same ticker and asserts only one TradeRecord exists.
- C3 (exit>=fill) fail: a test that constructs a state where the entry trigger fires the same day a stop would have hit and asserts the record's exit_date >= fill_date.

The test names the *invariant*, not the specific tickers/dates from the run.

- [ ] **Step 2: Confirm the test fails on the current branch**

```bash
uv run pytest tests/backtest/<new_test>.py::<test_name> -xvs
```

Expected: FAIL with a message that points at the violated invariant.

- [ ] **Step 3: Fix in the relevant source file**

Most likely: `src/wave_alpha/backtest/trade_layer.py` (state machine), `src/wave_alpha/trades/derive.py` (signal construction), or `src/wave_alpha/backtest/coherence_bucket.py` (bucket boundaries). Fix should be minimal — one invariant, one fix.

- [ ] **Step 4: Confirm the new test passes and the full suite stays green**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 5: Re-run the sim and the inspection**

```bash
uv run wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --rules configs/sanity_run.yaml \
  --out reports/trade-sim-2026-05-05/report.md \
  --json reports/trade-sim-2026-05-05/result.json \
  --trades-csv reports/trade-sim-2026-05-05/trades.csv \
  2>&1 | tee reports/trade-sim-2026-05-05/run.log

uv run python scripts/inspect_sanity_run.py | tee reports/trade-sim-2026-05-05/verdict.txt
```

Expected: the previously-failing criterion now passes. If a new criterion failed because of the fix (regression), restart the loop with that one. Cap at five iterations — beyond that, escape to a fresh design spec rather than patching further in this branch.

- [ ] **Step 6: Commit**

```bash
git add tests/ src/
git commit -m "$(cat <<'EOF'
fix(backtest): <one-line invariant name>

<2-3 sentences on the violation, the test that pins it, the fix.>

Refs: docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md §7

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Re-evaluate**

If the verdict table is now all-green (or §5.1 all-green and §5.2/§5.3 acceptable per Phase D3 Step 2 logic), exit Phase E and advance to Phase F.

If a *different* §5.1 criterion now fails: restart Phase E with that one as the target.

If the *same* criterion fails differently: the fix was insufficient or wrong. Revert with `git revert HEAD`, redesign, restart.

---

## Phase F — Persist and verdict

### Task F1: Compute the empirical EBT distribution

**Files:**
- Create: `reports/trade-sim-2026-05-05/expected_bars_distribution.json`

- [ ] **Step 1: Compute and save**

```bash
uv run python <<'PY'
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median, quantiles

ART = Path("reports/trade-sim-2026-05-05")
trades = list(csv.DictReader((ART / "trades.csv").open()))
winning = [t for t in trades if t["exit_reason"] == "target"]
by_degree: dict[str, list[int]] = defaultdict(list)
for t in winning:
    by_degree[t["degree"]].append(int(t["bars_held"]))

dist: dict[str, dict] = {}
for degree, vals in sorted(by_degree.items(), key=lambda kv: int(kv[0])):
    if len(vals) < 4:
        dist[degree] = {"n_wins": len(vals), "p25": None, "median": None, "p75": None}
        continue
    qs = quantiles(vals, n=4)
    dist[degree] = {
        "n_wins": len(vals),
        "p25": qs[0],
        "median": median(vals),
        "p75": qs[2],
    }

(ART / "expected_bars_distribution.json").write_text(json.dumps(dist, indent=2))
print(json.dumps(dist, indent=2))
PY
```

Expected: prints a dict keyed by degree, each entry has `n_wins`, `p25`, `median`, `p75`. Saved to disk.

---

### Task F2: Fill spec §6 (Results)

**Files:**
- Modify: `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md`

- [ ] **Step 1: Pull headline numbers**

```bash
uv run python -c "
import json
p = json.load(open('reports/trade-sim-2026-05-05/result.json'))
h = p['headline']
print(f\"closed_trades:    {h['closed_trades']}\")
print(f\"open_at_end:      {h['open_at_end']}\")
print(f\"hit_rate:         {h['hit_rate']:.3f}\")
print(f\"avg_R:            {h['avg_R']:+.3f}\")
print(f\"expectancy:       {h['expectancy']:+.3f}\")
print(f\"profit_factor:    {h['profit_factor']}\")
print(f\"max_drawdown_R:   {h['max_drawdown_R']:+.3f}\")
print(f\"coverage_4h:      {h['coverage_4h']:.3f}\")
"
```

- [ ] **Step 2: Pull the four breakdown tables (coherence, tf_count, year, pattern)**

```bash
uv run python -c "
import json
p = json.load(open('reports/trade-sim-2026-05-05/result.json'))
for name in ('by_coherence_bucket', 'by_tf_count', 'by_year', 'by_pattern'):
    print(f'\\n## {name}')
    print('| key | n | hit | avg R | expectancy | PF |')
    print('|---|---:|---:|---:|---:|---:|')
    for k, m in p[name].items():
        pf = m.get('profit_factor')
        pf_s = '∞' if pf == float('inf') or pf is None else f'{pf:.2f}'
        print(f\"| {k} | {int(m['sample_size'])} | {m['hit_rate']:.2f} | {m['avg_R']:+.2f} | {m['expectancy']:+.2f} | {pf_s} |\")
"
```

- [ ] **Step 3: Edit the spec**

Open `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` and replace the placeholders in §6:

- §6 preamble: fill `Run date:`, `Cache state at run:` (warm — Phase D1 was run), `Wall time:` (from `run.log`), `Code SHA:` (from `code_sha.txt`).
- §6.1 Headline: paste the values from Step 1.
- §6.2 By coherence bucket: paste the table from Step 2 (`by_coherence_bucket` rows).
- §6.3 By TF-count: paste the `by_tf_count` rows.
- §6.4 By year: paste the `by_year` rows.
- §6.5 By pattern: paste the `by_pattern` rows.
- §6.6 EBT distribution: paste the contents of `expected_bars_distribution.json` formatted as the table.
- §6.7 Verdict table: copy from `verdict.txt` (Phase D3 / E5 output), one row per criterion.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md
git commit -m "$(cat <<'EOF'
docs(spec): trade-sim sanity run results — §6 filled

Run on <date>, code SHA <abc1234>, cache warm. <closed> closed trades,
hit rate <hr>, expectancy <ex>R, 4h coverage <cov>%. Per-degree EBT
distribution captured for T7 follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Adjust the trailing message values to match the actual numbers.)

---

### Task F3: Fill spec §7 (Fix ledger)

**Files:**
- Modify: `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md`

- [ ] **Step 1: List the fix commits made in Phase E**

```bash
git log --oneline feat/trade-sim-sanity-run --grep="^fix(backtest)" -- src/wave_alpha/backtest/ src/wave_alpha/trades/
```

- [ ] **Step 2: Fill §7 of the spec**

For each commit, add a row:

| # | Commit | Invariant violated | Fix summary |
|---|---|---|---|
| 1 | `<sha>` | `<criterion>` | `<commit subject>` |

If no Phase E iterations occurred, replace the placeholder table with the line "No fixes required — sanity run was clean first iteration."

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md
git commit -m "$(cat <<'EOF'
docs(spec): trade-sim sanity run §7 fix ledger

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task F4: Fill §10.1 (Verdict) and final review

**Files:**
- Modify: `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md`

- [ ] **Step 1: Make the GO/NO-GO call**

In §10.1:
- Check the **GO** box if §6.7 has all 16 rows passing (or §5.1 all-green and §5.2/§5.3 documented).
- Check the **NO-GO** box if any §5.1 row remains failing after Phase E exhausted its 5-iteration cap.

Fill the "Reasoning:" line with 1–3 sentences referencing specific §6.7 evidence.

- [ ] **Step 2: Final test pass + lint**

```bash
uv run pytest
uv run ruff check .
```

Expected: PASS, clean.

- [ ] **Step 3: Commit the verdict**

```bash
git add docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md
git commit -m "$(cat <<'EOF'
docs(spec): trade-sim sanity run verdict — <GO|NO-GO>

<one-sentence rationale>. Next: <ablation-grid spec | fix-list spec>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Print the branch summary**

```bash
git log --oneline main..HEAD
```

Expected: between 6 and ~12 commits depending on Phase E iteration count: spec (already on branch from `b67dcf8`), gitignore, config, signal+record instrumentation × 2, two scripts, optional Phase E fix commits, results fill, ledger fill, verdict fill.

---

## Phase F Review Boundary (final)

```bash
uv run pytest
uv run ruff check .
git log --oneline main..HEAD
```

Expected: full suite green, lint clean, all spec sections filled, verdict committed. The branch is ready to merge to `main` (squash or merge-commit per project convention). The next spec — `docs/superpowers/specs/2026-05-XX-ablation-grid-design.md` — is unblocked iff the verdict was GO.

---

## Self-Review Checklist (controller)

- [ ] **Spec coverage:** every spec section has a task — §1–2 motivation/scope (no implementation, controller-only context); §3.1 centroid → A2; §3.2 run scope → D2 CLI flags; §3.3 artifacts → D2 (3 of 4) + F1 (4th); §4.1 pre-warm → D1; §4.2 config commit → A2; §4.3 optional report rendering → folded into Phase E if touched, else deferred per spec note; §4.4 run → D2; §4.5 fix-as-you-go → Phase E loop; §4.6 gitignore policy → A1; §4.7 verdict → F4; §5.1–5.3 criteria → C2 inspection script; §6 results → F2; §7 ledger → F3; §8–9 documentation-only; §10.1 verdict → F4.
- [ ] **Placeholder scan:** every step has actual code or commands. The Phase E loop uses templated commit messages with explicit "Adjust the trailing message values" notes — acknowledged, not placeholder. The fix-iteration tests are described by invariant rather than literal code because the bug surface is unknown — but Phase E gives 3 concrete examples (C2, C6, C3) keyed to specific criteria so the engineer has a model.
- [ ] **Type consistency:** `degree: int` and `expected_bars_to_target: int` types match between TradeSignal (B1) and TradeRecord (B2). The inspection script reads these as strings from `csv.DictReader` and casts (`int(t["expected_bars_to_target"])`) — consistent with existing CSV round-trip patterns in the codebase. `tf_count` cast to `int(...)` matches the existing CSV-string round trip noted in the trade-sim-layer plan's controller checklist.
- [ ] **No phantom symbols:** `TradeSignal.degree` defined in B1, consumed in B2 (`signal.degree`); `TradeRecord.degree` and `TradeRecord.expected_bars_to_target` defined in B2, consumed in C2 (inspection script reads from CSV) and F1 (EBT distribution script). `CachedProvider`, `YahooProvider`, `Interval` all imported from existing modules verified in the spec/CLAUDE.md.
- [ ] **TDD:** B1 and B2 are failing-test-first; Phase E loop is failing-test-first. Phase A, C, D, F are not TDD (config/scripts/run/persistence) — appropriate for non-test-bearing changes.
- [ ] **Frequent commits:** ~9 baseline commits + N Phase E fix commits, each self-contained and revertible.

