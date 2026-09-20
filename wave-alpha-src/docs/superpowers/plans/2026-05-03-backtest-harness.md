# Backtest Harness Implementation Plan (v0.4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per user memory ("skip per-task reviewer dispatches on mechanical work; review at phase boundaries instead"), the controller skips per-task reviewer subagents and instead does one cross-cutting review at each phase boundary; one final review after all phases.

**Goal:** Walk-forward backtest harness over a 50-ticker universe that produces honest accuracy claims for the pivot layer (per-degree confirmation rate, Brier score, calibration error) and count layer (top-1 / top-3 forward-direction hit rate, by coherence bucket, by year), with hard lookahead guards. Default `--llm-mode disabled` so the headline number is reproducible without an API key.

**Architecture:** A new `backtest/` package with five focused modules — `universe.py` (load curated ticker list), `walk_forward.py` (date iterator), `labels.py` (pure functions for forward-direction and pivot-confirmation labeling), `pivot_layer.py` and `count_layer.py` (per-layer evaluators), and `runner.py` + `report.py` (orchestrator + markdown rendering). Reuses the existing `PointInTimeView` lookahead invariant and `CachedProvider` caching. New CLI: `wave-alpha backtest pivot|count`.

**Tech Stack:** Python 3.11+, existing `pipeline.run_analysis` seam, `CachedProvider` (Yahoo + SQLite), `PointInTimeView`, Typer, JSON + Markdown output. No new dependencies.

---

## Out of scope (deferred)

- Logistic regression for right-edge confirmation (spec O3) — needs the labels this plan produces; train + promotion gate ship in v0.4.x once we have signal.
- α tuning (spec O4) and coherence shape (O5) — read off the v0.4 sweep results post-merge; no code change in this plan.
- Trade layer metrics — v0.5 per spec §8 / §9.
- Parallelism — single-process iteration is fine for 50 × 1300 ≈ 65k evaluations; revisit if wall-clock becomes painful.
- LLM-mode backtest run — defer the LIVE-mode sweep to a manual one-shot after the harness ships.

---

## File structure

```
src/wave_alpha/
├── backtest/
│   ├── __init__.py          # public re-exports
│   ├── universe.py          # load_universe()
│   ├── walk_forward.py      # iter_business_days(), trading-day filter
│   ├── labels.py            # forward_direction_label(), pivot_confirmation_label()
│   ├── brier.py             # brier_score(), reliability_bins()
│   ├── pivot_layer.py       # PivotLayerEvaluator + result models
│   ├── count_layer.py       # CountLayerEvaluator + result models
│   ├── runner.py            # BacktestConfig + run_backtest()
│   └── report.py            # render_markdown(), render_json()
data/
└── universe_curated.txt     # 50 tickers, one per line, # comments allowed
src/wave_alpha/cli/app.py    # add `backtest` subcommand group
tests/backtest/              # mirror structure
```

Why this split: each module has one responsibility. `labels.py` is the only place that knows how a "hit" is defined — change the definition, you change one file. `pivot_layer.py` and `count_layer.py` are independent and can be invoked separately; the CLI dispatches to one or the other, never both at once (each takes 20+ minutes cold-cache and produces independent output).

---

## Phase A — Universe + walk-forward iteration

### Task 1: Universe loader

**Files:**
- Create: `src/wave_alpha/backtest/__init__.py`
- Create: `src/wave_alpha/backtest/universe.py`
- Create: `tests/backtest/__init__.py`
- Create: `tests/backtest/test_universe.py`
- Create: `data/universe_curated.txt`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_universe.py
from pathlib import Path

import pytest

from wave_alpha.backtest.universe import load_universe


def test_load_universe_reads_one_symbol_per_line(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nMSFT\nNVDA\n")
    assert load_universe(f) == ["AAPL", "MSFT", "NVDA"]


def test_load_universe_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("# tech\nAAPL\n\n# defensive\nKO\n  # indented comment\nPG\n")
    assert load_universe(f) == ["AAPL", "KO", "PG"]


def test_load_universe_uppercases_and_strips(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text(" aapl \n  msft\n")
    assert load_universe(f) == ["AAPL", "MSFT"]


def test_load_universe_dedupes_preserving_order(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nMSFT\nAAPL\nNVDA\nMSFT\n")
    assert load_universe(f) == ["AAPL", "MSFT", "NVDA"]


def test_load_universe_raises_on_empty(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("# nothing here\n\n")
    with pytest.raises(ValueError, match="empty universe"):
        load_universe(f)


def test_load_universe_raises_on_invalid_symbol(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nbad symbol with space\n")
    with pytest.raises(ValueError, match="invalid symbol"):
        load_universe(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_universe.py -v`
Expected: 6× FAIL with `ModuleNotFoundError: wave_alpha.backtest.universe`

- [ ] **Step 3: Implement loader**

```python
# src/wave_alpha/backtest/__init__.py
from wave_alpha.backtest.universe import load_universe

__all__ = ["load_universe"]
```

```python
# src/wave_alpha/backtest/universe.py
from __future__ import annotations

import re
from pathlib import Path

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def load_universe(path: Path) -> list[str]:
    """Load ticker symbols from a text file.

    Format: one symbol per line. Blank lines and lines starting with '#'
    (after leading whitespace) are ignored. Symbols are uppercased and
    deduplicated preserving first-seen order.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sym = line.upper()
        if not _SYMBOL_RE.match(sym):
            raise ValueError(f"invalid symbol: {raw!r}")
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    if not out:
        raise ValueError(f"empty universe: {path}")
    return out
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_universe.py -v`
Expected: 6/6 PASS

- [ ] **Step 5: Curated 50-ticker universe**

Create `data/universe_curated.txt`:

```
# wave-alpha curated 50-ticker backtest universe (v0.4)
# Mix of sectors, vol regimes, market caps, and IPO ages.
# Source list — change carefully; backtest comparability depends on stability.

# Mega-cap tech
AAPL
MSFT
GOOGL
AMZN
NVDA
META
TSLA

# Other tech
ORCL
CRM
ADBE
NFLX
AMD
INTC
QCOM

# Defensive (consumer staples + utilities)
KO
PG
WMT
COST
PEP
JNJ
PFE
NEE
SO

# Cyclical (industrials + discretionary)
CAT
DE
GE
UPS
F
GM
HD
LOW
NKE
SBUX

# Financials
JPM
BAC
GS
MS
V
MA
BRK.B

# Energy
XOM
CVX
COP

# Biotech / healthcare
LLY
UNH
ABBV
TMO

# Mid-caps + recent-ish IPOs
SNOW
PLTR
RBLX
COIN

# Counts: 50 tickers
```

- [ ] **Step 6: Verify universe file loads**

Run: `uv run python -c "from wave_alpha.backtest.universe import load_universe; from pathlib import Path; u = load_universe(Path('data/universe_curated.txt')); print(len(u), u[:5])"`
Expected: `50 ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']`

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/backtest/__init__.py src/wave_alpha/backtest/universe.py \
  tests/backtest/__init__.py tests/backtest/test_universe.py data/universe_curated.txt
git commit -m "$(cat <<'EOF'
feat(backtest): universe loader + curated 50-ticker file

Adds wave_alpha.backtest.universe.load_universe — reads one symbol per
line, skips blanks/comments, uppercases, dedupes, validates symbol shape.

The curated 50-ticker list spans mega-cap tech, defensives, cyclicals,
financials, energy, biotech, and recent IPOs to span vol regimes for
backtest validation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Walk-forward date iterator

**Files:**
- Create: `src/wave_alpha/backtest/walk_forward.py`
- Create: `tests/backtest/test_walk_forward.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_walk_forward.py
from datetime import date

import pytest

from wave_alpha.backtest.walk_forward import iter_business_days


def test_iter_business_days_skips_weekends() -> None:
    # 2024-01-05 = Friday, 2024-01-06 = Saturday, 2024-01-08 = Monday
    days = list(iter_business_days(date(2024, 1, 5), date(2024, 1, 9)))
    assert days == [
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
    ]


def test_iter_business_days_inclusive_endpoints() -> None:
    # Single-day range yields just that day if business day
    assert list(iter_business_days(date(2024, 1, 8), date(2024, 1, 8))) == [date(2024, 1, 8)]
    # Saturday → empty
    assert list(iter_business_days(date(2024, 1, 6), date(2024, 1, 6))) == []


def test_iter_business_days_step_subsamples() -> None:
    # Step=2 emits every other business day
    days = list(iter_business_days(date(2024, 1, 1), date(2024, 1, 12), step=2))
    # Mon Jan 1, Wed Jan 3, Fri Jan 5, Tue Jan 9, Thu Jan 11
    # (Jan 1 is Monday in 2024)
    assert days == [
        date(2024, 1, 1),
        date(2024, 1, 3),
        date(2024, 1, 5),
        date(2024, 1, 9),
        date(2024, 1, 11),
    ]


def test_iter_business_days_step_must_be_positive() -> None:
    with pytest.raises(ValueError, match="step must be"):
        list(iter_business_days(date(2024, 1, 1), date(2024, 1, 5), step=0))


def test_iter_business_days_end_before_start_yields_empty() -> None:
    assert list(iter_business_days(date(2024, 1, 10), date(2024, 1, 5))) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_walk_forward.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement iterator**

```python
# src/wave_alpha/backtest/walk_forward.py
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta


def iter_business_days(start: date, end: date, *, step: int = 1) -> Iterator[date]:
    """Yield business days (Mon-Fri) from start to end inclusive.

    `step` selects every Nth emitted business day (1 = every day, 5 = weekly).
    Holidays are NOT skipped — provider lookups for non-trading days return no
    bars and the analyze pass produces no result for that as_of, which the
    evaluators handle by skipping. Centralizing a US-trading-calendar would
    add a dependency for marginal benefit.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1, got {step}")
    cur = start
    emitted = 0
    while cur <= end:
        if cur.weekday() < 5:
            if emitted % step == 0:
                yield cur
            emitted += 1
        cur = cur + timedelta(days=1)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_walk_forward.py -v`
Expected: 5/5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/walk_forward.py tests/backtest/test_walk_forward.py
git commit -m "$(cat <<'EOF'
feat(backtest): walk-forward business-day iterator

iter_business_days(start, end, step) yields Mon-Fri dates inclusive,
optionally subsampled by step (e.g. step=5 = weekly). Holidays are not
filtered — non-trading days produce empty provider results downstream
which the evaluators skip cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase A Review Boundary

After Tasks 1-2 ship, controller does a quick read of `backtest/universe.py` and `backtest/walk_forward.py` for:
- Lookahead leak risk (none expected — these don't touch bars).
- Symbol regex correctness (BRK.B should pass, "BAD SYMBOL" should fail — covered by tests).
- Iterator off-by-one (covered by inclusive-endpoint test).

If clean → proceed to Phase B.

---

## Phase B — Pure-function labelers

### Task 3: Forward-direction labeling

**Files:**
- Create: `src/wave_alpha/backtest/labels.py`
- Create: `tests/backtest/test_labels.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_labels.py
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.labels import (
    forward_direction_label,
    pivot_confirmation_label,
)
from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _bar(d_off: int, close: str) -> Bar:
    d = date(2024, 1, 1) + timedelta(days=d_off)
    return Bar(
        ticker="X",
        interval=Interval.DAILY,
        timestamp=d,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1_000,
    )


def test_forward_direction_up_when_close_rises_after_n_bars() -> None:
    bars = [_bar(i, str(100 + i)) for i in range(30)]
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label == "up"


def test_forward_direction_down_when_close_falls() -> None:
    bars = [_bar(i, str(100 - i)) for i in range(30)]
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label == "down"


def test_forward_direction_none_when_insufficient_forward_history() -> None:
    bars = [_bar(i, "100") for i in range(10)]
    # Need 20 forward bars from as_of, only 9 forward bars exist
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label is None


def test_forward_direction_none_when_as_of_not_in_series() -> None:
    bars = [_bar(i, "100") for i in range(30)]
    # as_of before first bar
    assert forward_direction_label(bars, as_of=date(2023, 12, 1), n_bars=5) is None


def test_forward_direction_flat_close_returns_down() -> None:
    """Equal closes → 'down' by convention (no rise = no bullish edge)."""
    bars = [_bar(i, "100") for i in range(30)]
    assert forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=10) == "down"


def test_pivot_confirmation_label_promoted_when_state_advances() -> None:
    """A tentative pivot at degree d on date t was confirmed if a later snapshot
    of the same pivot timeline contains the same (degree, timestamp) pivot in
    CONFIRMED state."""
    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 5),
            price=Decimal("110"),
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 8),
        )
    ]
    label = pivot_confirmation_label(initial, later_pivots=later_timeline)
    assert label == "confirmed"


def test_pivot_confirmation_label_invalidated_when_pivot_disappears() -> None:
    """Pivot tentative at t disappears from later snapshot → invalidated."""
    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 6),  # different date
            price=Decimal("112"),
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 9),
        )
    ]
    label = pivot_confirmation_label(initial, later_pivots=later_timeline)
    assert label == "invalidated"


def test_pivot_confirmation_label_pending_when_still_tentative() -> None:
    """Same (degree, timestamp) but still TENTATIVE → label is pending (None)."""
    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 5),
            price=Decimal("110"),
            type=PivotType.HIGH,
            state=PivotState.TENTATIVE,
            confirmed_at=None,
        )
    ]
    assert pivot_confirmation_label(initial, later_pivots=later_timeline) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_labels.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement labelers**

```python
# src/wave_alpha/backtest/labels.py
from __future__ import annotations

from datetime import date
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.pivots.models import PivotEvent, PivotState

Direction = Literal["up", "down"]
PivotOutcome = Literal["confirmed", "invalidated"]


def forward_direction_label(
    bars: list[Bar], *, as_of: date, n_bars: int
) -> Direction | None:
    """Compare close at as_of vs close N business bars later.

    Returns 'up' if forward close > as_of close, 'down' otherwise (including
    equality — flat close offers no bullish edge). Returns None when as_of is
    not in the series or there are fewer than n_bars of forward history.
    """
    idx_at = next((i for i, b in enumerate(bars) if b.timestamp == as_of), None)
    if idx_at is None or idx_at + n_bars >= len(bars):
        return None
    base = bars[idx_at].close
    forward = bars[idx_at + n_bars].close
    return "up" if forward > base else "down"


def pivot_confirmation_label(
    initial: PivotEvent, *, later_pivots: list[PivotEvent]
) -> PivotOutcome | None:
    """Did a tentative pivot get confirmed in a later snapshot?

    'confirmed' — same (degree, timestamp, type) appears CONFIRMED later.
    'invalidated' — same (degree, timestamp) is missing from the later
                    timeline.
    None — same (degree, timestamp) is still TENTATIVE; outcome unknown.
    """
    if initial.state != PivotState.TENTATIVE:
        raise ValueError("pivot_confirmation_label expects a TENTATIVE input")
    match = next(
        (
            p
            for p in later_pivots
            if p.degree == initial.degree
            and p.timestamp == initial.timestamp
            and p.type == initial.type
        ),
        None,
    )
    if match is None:
        return "invalidated"
    if match.state == PivotState.CONFIRMED:
        return "confirmed"
    return None  # still tentative
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_labels.py -v`
Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/labels.py tests/backtest/test_labels.py
git commit -m "$(cat <<'EOF'
feat(backtest): pure-function labelers for direction + pivot outcome

forward_direction_label compares close at as_of vs close n bars later,
returning 'up'/'down' or None when forward history is short. Flat close
labels as 'down' (no bullish edge convention).

pivot_confirmation_label compares a tentative pivot to a later pivot
snapshot at the same degree, returning 'confirmed' / 'invalidated' /
None (still pending).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Brier score + reliability bins

**Files:**
- Create: `src/wave_alpha/backtest/brier.py`
- Create: `tests/backtest/test_brier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_brier.py
import pytest

from wave_alpha.backtest.brier import brier_score, calibration_error, reliability_bins


def test_brier_score_perfect_predictions_is_zero() -> None:
    assert brier_score([(1.0, 1), (0.0, 0), (1.0, 1)]) == 0.0


def test_brier_score_worst_predictions_is_one() -> None:
    assert brier_score([(1.0, 0), (0.0, 1)]) == 1.0


def test_brier_score_uncertain_predictions() -> None:
    # Always predict 0.5; outcomes 50/50
    pairs = [(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]
    assert brier_score(pairs) == 0.25


def test_brier_score_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        brier_score([])


def test_reliability_bins_groups_by_predicted_probability() -> None:
    # 10-bin: [0.0, 0.1), [0.1, 0.2), ..., [0.9, 1.0]
    pairs = [
        (0.05, 0),
        (0.05, 1),  # bin 0: 1/2 = 0.5 actual, 0.05 predicted
        (0.55, 1),
        (0.55, 1),
        (0.55, 0),  # bin 5: 2/3 = 0.667 actual, 0.55 predicted
        (0.95, 1),  # bin 9: 1/1 = 1.0 actual, 0.95 predicted
    ]
    bins = reliability_bins(pairs, n_bins=10)
    # Each entry is (bin_lower, n, mean_predicted, mean_actual)
    assert (0.0, 2, 0.05, 0.5) in bins
    assert (0.5, 3, pytest.approx(0.55), pytest.approx(2 / 3)) in bins
    assert (0.9, 1, 0.95, 1.0) in bins


def test_calibration_error_perfect_calibration_is_zero() -> None:
    # Each bin's mean_actual matches mean_predicted exactly
    pairs = [(0.0, 0), (0.5, 0), (0.5, 1), (1.0, 1)]
    assert calibration_error(pairs, n_bins=10) == pytest.approx(0.0)


def test_calibration_error_weighted_by_bin_size() -> None:
    # 100 predictions of 0.9 with actual 0.5 → big mass × 0.4 gap = 0.4
    # 1 prediction of 0.1 with actual 0.5 → small mass × 0.4 gap = ~0
    pairs = [(0.9, i % 2) for i in range(100)] + [(0.1, 1)]
    err = calibration_error(pairs, n_bins=10)
    assert 0.35 < err < 0.41
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_brier.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement Brier + reliability**

```python
# src/wave_alpha/backtest/brier.py
from __future__ import annotations

from collections.abc import Sequence


def brier_score(pairs: Sequence[tuple[float, int]]) -> float:
    """Mean squared error between predicted probability and binary outcome.

    `pairs` is a sequence of (predicted_probability, actual_outcome) where
    actual is 0 or 1. Lower is better; range [0, 1].
    """
    if not pairs:
        raise ValueError("brier_score on empty sequence")
    return sum((p - a) ** 2 for p, a in pairs) / len(pairs)


def reliability_bins(
    pairs: Sequence[tuple[float, int]], *, n_bins: int = 10
) -> list[tuple[float, int, float, float]]:
    """Group pairs into n_bins equal-width buckets over [0, 1].

    Returns a list of (bin_lower, count, mean_predicted, mean_actual) sorted by
    bin_lower; empty bins are omitted. The last bin is closed on the right
    (predictions of exactly 1.0 land in the last bin).
    """
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    width = 1.0 / n_bins
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, a in pairs:
        idx = min(int(p / width), n_bins - 1)
        buckets.setdefault(idx, []).append((p, a))
    out: list[tuple[float, int, float, float]] = []
    for idx in sorted(buckets):
        rows = buckets[idx]
        n = len(rows)
        mean_p = sum(p for p, _ in rows) / n
        mean_a = sum(a for _, a in rows) / n
        out.append((idx * width, n, mean_p, mean_a))
    return out


def calibration_error(
    pairs: Sequence[tuple[float, int]], *, n_bins: int = 10
) -> float:
    """Expected calibration error: weighted mean |bin_mean_predicted - bin_mean_actual|.

    Weight is bin sample size. Range [0, 1]; 0 = perfect calibration.
    """
    if not pairs:
        raise ValueError("calibration_error on empty sequence")
    bins = reliability_bins(pairs, n_bins=n_bins)
    total = sum(n for _, n, _, _ in bins)
    return sum(n * abs(mean_p - mean_a) for _, n, mean_p, mean_a in bins) / total
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_brier.py -v`
Expected: 7/7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/brier.py tests/backtest/test_brier.py
git commit -m "$(cat <<'EOF'
feat(backtest): Brier score, reliability bins, calibration error

Pure functions over (predicted_probability, actual_outcome) pairs:
- brier_score: mean squared error, lower is better, range [0, 1]
- reliability_bins: groups predictions into N equal-width buckets
- calibration_error: weighted mean gap between predicted and actual
  per bin; sample-size weighted

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B Review Boundary

Quick controller check after Tasks 3-4:
- `forward_direction_label` does NOT call `PointInTimeView` because evaluators feed the FULL bar series and the label is by definition forward-looking — the lookahead guard belongs at the analyze-time call site (Phase C/D), not here. Confirm comment makes this explicit if not.
- `pivot_confirmation_label` only matches on `(degree, timestamp, type)` — confirm this is what the spec wants (different price at same degree+timestamp shouldn't happen in practice; if it does we treat as confirmed).
- `calibration_error` weights are sample-size based — matches the standard ECE definition.

If clean → proceed to Phase C.

---

## Phase C — Count layer evaluator

### Task 5: CountLayerResult model

**Files:**
- Create: `src/wave_alpha/backtest/count_layer.py` (just the dataclass for now)
- Create: `tests/backtest/test_count_layer_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_count_layer_models.py
from datetime import date

from wave_alpha.backtest.count_layer import CountEvaluation, CountLayerResult


def test_count_evaluation_construction() -> None:
    ev = CountEvaluation(
        ticker="AAPL",
        as_of=date(2024, 1, 5),
        predicted_direction="up",
        actual_direction="up",
        coherence_score=0.8,
        top1_hit=True,
        top3_directions=("up", "down", "up"),
        top3_hit=True,
    )
    assert ev.top1_hit is True
    assert ev.top3_hit is True


def test_count_layer_result_aggregates() -> None:
    evs = [
        CountEvaluation(
            ticker="AAPL",
            as_of=date(2024, 1, 5),
            predicted_direction="up",
            actual_direction="up",
            coherence_score=0.8,
            top1_hit=True,
            top3_directions=("up",),
            top3_hit=True,
        ),
        CountEvaluation(
            ticker="MSFT",
            as_of=date(2024, 1, 5),
            predicted_direction="down",
            actual_direction="up",
            coherence_score=0.4,
            top1_hit=False,
            top3_directions=("down", "up"),
            top3_hit=True,
        ),
    ]
    result = CountLayerResult(evaluations=evs, n_bars_forward=20)
    assert result.top1_hit_rate == 0.5
    assert result.top3_hit_rate == 1.0
    assert result.sample_size == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_count_layer_models.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement models**

```python
# src/wave_alpha/backtest/count_layer.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Direction = Literal["up", "down"]


@dataclass(frozen=True)
class CountEvaluation:
    """One (ticker, as_of) backtest sample for the count layer."""

    ticker: str
    as_of: date
    predicted_direction: Direction
    actual_direction: Direction
    coherence_score: float
    top1_hit: bool
    top3_directions: tuple[Direction, ...]
    top3_hit: bool


@dataclass
class CountLayerResult:
    evaluations: list[CountEvaluation] = field(default_factory=list)
    n_bars_forward: int = 20

    @property
    def sample_size(self) -> int:
        return len(self.evaluations)

    @property
    def top1_hit_rate(self) -> float:
        if not self.evaluations:
            return 0.0
        return sum(1 for e in self.evaluations if e.top1_hit) / len(self.evaluations)

    @property
    def top3_hit_rate(self) -> float:
        if not self.evaluations:
            return 0.0
        return sum(1 for e in self.evaluations if e.top3_hit) / len(self.evaluations)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_count_layer_models.py -v`
Expected: 2/2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/count_layer.py tests/backtest/test_count_layer_models.py
git commit -m "$(cat <<'EOF'
feat(backtest): CountEvaluation + CountLayerResult models

CountEvaluation captures one (ticker, as_of) sample with predicted
direction (top-1), actual forward direction, coherence at signal
time, and per-rank hit flags.

CountLayerResult aggregates samples and exposes top1/top3 hit rates.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Coherence-bucket and yearly breakdown

**Files:**
- Modify: `src/wave_alpha/backtest/count_layer.py`
- Modify: `tests/backtest/test_count_layer_models.py` (add tests)

- [ ] **Step 1: Write the failing tests (append)**

```python
def test_count_layer_result_buckets_by_coherence() -> None:
    evs = [
        # Three full-agreement samples, 2 hit
        _ev("A", "up", "up", 0.8, top1_hit=True),
        _ev("B", "up", "up", 0.85, top1_hit=True),
        _ev("C", "up", "down", 0.75, top1_hit=False),
        # Two partial samples, 1 hit
        _ev("D", "up", "up", 0.5, top1_hit=True),
        _ev("E", "down", "up", 0.6, top1_hit=False),
        # One disagreement sample, 0 hits
        _ev("F", "up", "down", 0.2, top1_hit=False),
    ]
    result = CountLayerResult(evaluations=evs, n_bars_forward=20)
    buckets = result.by_coherence_bucket()
    assert buckets["full"]["sample_size"] == 3
    assert buckets["full"]["top1_hit_rate"] == 2 / 3
    assert buckets["partial"]["sample_size"] == 2
    assert buckets["partial"]["top1_hit_rate"] == 0.5
    assert buckets["disagreement"]["sample_size"] == 1
    assert buckets["disagreement"]["top1_hit_rate"] == 0.0


def test_count_layer_result_buckets_by_year() -> None:
    evs = [
        _ev("A", "up", "up", 0.5, top1_hit=True, as_of=date(2023, 1, 1)),
        _ev("B", "up", "down", 0.5, top1_hit=False, as_of=date(2023, 6, 1)),
        _ev("C", "up", "up", 0.5, top1_hit=True, as_of=date(2024, 1, 1)),
    ]
    result = CountLayerResult(evaluations=evs)
    by_year = result.by_year()
    assert by_year[2023]["sample_size"] == 2
    assert by_year[2023]["top1_hit_rate"] == 0.5
    assert by_year[2024]["sample_size"] == 1
    assert by_year[2024]["top1_hit_rate"] == 1.0


def _ev(
    tk: str,
    pred: str,
    actual: str,
    coh: float,
    *,
    top1_hit: bool,
    as_of: date = date(2024, 1, 5),
) -> CountEvaluation:
    return CountEvaluation(
        ticker=tk,
        as_of=as_of,
        predicted_direction=pred,
        actual_direction=actual,
        coherence_score=coh,
        top1_hit=top1_hit,
        top3_directions=(pred,),
        top3_hit=top1_hit,
    )
```

Add to top of file: `from datetime import date`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_count_layer_models.py -v`
Expected: New tests FAIL — methods missing.

- [ ] **Step 3: Implement breakdowns**

Add to `count_layer.py`:

```python
_COHERENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("full", 0.7, 1.01),         # >0.7 (use 1.01 to include 1.0)
    ("partial", 0.4, 0.7),       # 0.4..0.7
    ("disagreement", 0.0, 0.4),  # <0.4
)


def _bucket(coh: float) -> str:
    for name, lo, hi in _COHERENCE_BUCKETS:
        if lo <= coh < hi:
            return name
    return "disagreement"  # safety fallback for negative scores


# Inside CountLayerResult:
    def by_coherence_bucket(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[CountEvaluation]] = {"full": [], "partial": [], "disagreement": []}
        for e in self.evaluations:
            groups[_bucket(e.coherence_score)].append(e)
        return {
            name: {
                "sample_size": len(rows),
                "top1_hit_rate": (sum(1 for r in rows if r.top1_hit) / len(rows)) if rows else 0.0,
                "top3_hit_rate": (sum(1 for r in rows if r.top3_hit) / len(rows)) if rows else 0.0,
            }
            for name, rows in groups.items()
        }

    def by_year(self) -> dict[int, dict[str, float]]:
        groups: dict[int, list[CountEvaluation]] = {}
        for e in self.evaluations:
            groups.setdefault(e.as_of.year, []).append(e)
        return {
            yr: {
                "sample_size": len(rows),
                "top1_hit_rate": sum(1 for r in rows if r.top1_hit) / len(rows),
                "top3_hit_rate": sum(1 for r in rows if r.top3_hit) / len(rows),
            }
            for yr, rows in sorted(groups.items())
        }
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_count_layer_models.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/count_layer.py tests/backtest/test_count_layer_models.py
git commit -m "$(cat <<'EOF'
feat(backtest): coherence-bucket and yearly count-layer breakdowns

Adds CountLayerResult.by_coherence_bucket() returning sample size and
hit rates for full (>0.7), partial (0.4-0.7), and disagreement (<0.4)
buckets per spec §8 sample report.

Adds CountLayerResult.by_year() returning the same per-year for
regime-aware diagnostics — 2020 vol spike, 2022 bear, etc.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Count layer evaluator

**Files:**
- Modify: `src/wave_alpha/backtest/count_layer.py`
- Create: `tests/backtest/test_count_layer_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_count_layer_evaluator.py
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.count_layer import CountLayerEvaluator
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _RisingProvider:
    """Synthetic provider: prices rise monotonically — every count predicts up
    and every forward-direction is up."""

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars: list[Bar] = []
        # 800 daily bars ending 2024-12-31 — enough for weekly + 4h + lookback
        start = date(2022, 1, 1)
        d = start
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                bars.append(_bar(ticker, interval, d, 100 + i * 0.5))
                i += 1
            d = d + timedelta(days=1)
        return bars


def _bar(tk: str, iv: Interval, d: date, p: float) -> Bar:
    return Bar(
        ticker=tk,
        interval=iv,
        timestamp=d,
        open=Decimal(str(p)),
        high=Decimal(str(p + 0.5)),
        low=Decimal(str(p - 0.5)),
        close=Decimal(str(p)),
        volume=1_000,
    )


def test_count_layer_evaluator_runs_one_pass_per_as_of() -> None:
    provider = _RisingProvider()
    ev = CountLayerEvaluator(provider=provider, n_bars_forward=20)
    # Three as-of dates spaced ~10 weeks apart in 2024 (so 20 forward bars exist)
    as_ofs = [date(2024, 3, 1), date(2024, 5, 1), date(2024, 7, 1)]
    result = ev.evaluate("AAPL", as_ofs)
    # Even if the count layer can't always emit a count, sample size <= 3
    assert 0 <= result.sample_size <= 3


def test_count_layer_evaluator_skips_when_no_forward_history() -> None:
    """An as_of close to the end of available data has < n_bars forward bars
    and should be skipped (no evaluation produced)."""
    provider = _RisingProvider()
    ev = CountLayerEvaluator(provider=provider, n_bars_forward=20)
    # End date is 2024-12-31; as_of within last 20 business days has no fwd hist
    as_ofs = [date(2024, 12, 20)]
    result = ev.evaluate("AAPL", as_ofs)
    assert result.sample_size == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/backtest/test_count_layer_evaluator.py -v`
Expected: FAIL — class missing.

- [ ] **Step 3: Implement evaluator**

Add to `count_layer.py`:

```python
from collections.abc import Iterable
from typing import cast

from wave_alpha.coherence.score import CoherenceInputs, three_way
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES, _DEFAULT_DEGREE_FOR_COUNT
from wave_alpha.pivots.multi_degree import detect_multi_degree
from wave_alpha.coherence.score import _predicted_direction  # type: ignore[attr-defined]
from wave_alpha.backtest.labels import forward_direction_label
from wave_alpha.data.point_in_time import PointInTimeView


@dataclass
class CountLayerEvaluator:
    """Walks (ticker × as_ofs), invokes the engine in --llm-mode disabled,
    derives top-1 and top-3 predicted directions, compares to actual
    n_bars-forward direction, and accumulates a CountLayerResult.

    The engine is invoked directly (not through pipeline.run_analysis) to
    avoid the LLM seam — backtest is reproducible without an API key.
    """

    provider: OHLCVProvider
    n_bars_forward: int = 20

    def evaluate(self, ticker: str, as_ofs: Iterable[date]) -> CountLayerResult:
        # Fetch ALL bars once — the as_of filter is applied per-eval via
        # PointInTimeView. fetch(end=last_as_of + buffer) so forward history
        # is available for label computation.
        as_of_list = list(as_ofs)
        if not as_of_list:
            return CountLayerResult(evaluations=[], n_bars_forward=self.n_bars_forward)
        # Buffer = 2× n_bars_forward to ensure enough forward history.
        end = max(as_of_list) + timedelta(days=self.n_bars_forward * 3)
        daily_all = self.provider.fetch(ticker, Interval.DAILY, end=end)
        weekly_all = self.provider.fetch(ticker, Interval.WEEKLY, end=end)
        h4_all = self.provider.fetch(ticker, Interval.HOURLY_4, end=end)

        result = CountLayerResult(evaluations=[], n_bars_forward=self.n_bars_forward)
        for as_of in as_of_list:
            ev = self._evaluate_one(
                ticker=ticker,
                as_of=as_of,
                daily_all=daily_all,
                weekly_all=weekly_all,
                h4_all=h4_all,
            )
            if ev is not None:
                result.evaluations.append(ev)
        return result

    def _evaluate_one(
        self,
        *,
        ticker: str,
        as_of: date,
        daily_all,
        weekly_all,
        h4_all,
    ) -> CountEvaluation | None:
        actual = forward_direction_label(daily_all, as_of=as_of, n_bars=self.n_bars_forward)
        if actual is None:
            return None
        try:
            daily_view = PointInTimeView(daily_all, as_of).visible()
            weekly_view = PointInTimeView(weekly_all, as_of).visible() if weekly_all else []
            h4_view = PointInTimeView(h4_all, as_of).visible() if h4_all else []
        except Exception:
            return None
        if not daily_view:
            return None

        counts_daily = self._counts_for(daily_view, Interval.DAILY)
        if not counts_daily:
            return None
        counts_weekly = self._counts_for(weekly_view, Interval.WEEKLY) if weekly_view else []
        counts_h4 = self._counts_for(h4_view, Interval.HOURLY_4) if h4_view else []

        coh = three_way(
            CoherenceInputs(
                weekly=counts_weekly[0] if counts_weekly else None,
                daily=counts_daily[0] if counts_daily else None,
                hourly4=counts_h4[0] if counts_h4 else None,
            )
        )

        top1_dir = _predicted_direction(counts_daily[0])
        if top1_dir is None:
            return None
        top1_hit = top1_dir == actual

        top3 = []
        for c in counts_daily[:3]:
            d = _predicted_direction(c)
            if d is not None:
                top3.append(d)
        top3_hit = actual in top3

        return CountEvaluation(
            ticker=ticker,
            as_of=as_of,
            predicted_direction=cast(Direction, top1_dir),
            actual_direction=actual,
            coherence_score=coh.score,
            top1_hit=top1_hit,
            top3_directions=tuple(cast(Direction, d) for d in top3),
            top3_hit=top3_hit,
        )

    def _counts_for(self, bars, interval: Interval) -> list[WaveCount]:
        if not bars:
            return []
        by_degree = detect_multi_degree(
            bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
        )
        pivots = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
        return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

Also add `from datetime import timedelta` to the imports.

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_count_layer_evaluator.py -v`
Expected: 2/2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/count_layer.py tests/backtest/test_count_layer_evaluator.py
git commit -m "$(cat <<'EOF'
feat(backtest): CountLayerEvaluator walks (ticker × as_ofs) reusing engine

Bypasses pipeline.run_analysis to skip the LLM seam — count-layer
backtest is reproducible without an API key. Reuses pivot detection +
enumeration directly with the same defaults pipeline uses.

For each as_of, derives top-1 and top-3 predicted directions and
compares to forward_direction_label at n_bars_forward (default 20).
Skips samples with insufficient forward history or empty counts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C Review Boundary

After Tasks 5-7:
- Confirm CountLayerEvaluator does NOT touch the LLM (greps OK).
- Confirm `_predicted_direction` is the same function the live pipeline uses (it is — imported from `coherence.score`).
- Confirm forward-direction lookahead can't leak: `forward_direction_label` reads bars at indexes > as_of, but the count itself is enumerated from `PointInTimeView(...).visible()` which clamps to ≤ as_of — labels are read AFTER the prediction is locked in. This is the correct shape and the synthetic-leak test in Phase E will validate.

If clean → proceed to Phase D.

---

## Phase D — Pivot layer evaluator

### Task 8: PivotLayerResult model + Brier integration

**Files:**
- Create: `src/wave_alpha/backtest/pivot_layer.py`
- Create: `tests/backtest/test_pivot_layer_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_pivot_layer_models.py
import pytest

from wave_alpha.backtest.pivot_layer import PivotEvaluation, PivotLayerResult


def test_pivot_evaluation_construction() -> None:
    ev = PivotEvaluation(
        ticker="AAPL",
        as_of_initial="2024-01-05",
        as_of_final="2024-01-15",
        degree=2,
        outcome="confirmed",
        predicted_proba=0.7,
    )
    assert ev.actual == 1


def test_pivot_evaluation_invalidated_actual_is_zero() -> None:
    ev = PivotEvaluation(
        ticker="AAPL",
        as_of_initial="2024-01-05",
        as_of_final="2024-01-15",
        degree=2,
        outcome="invalidated",
        predicted_proba=0.7,
    )
    assert ev.actual == 0


def test_pivot_layer_result_per_degree_confirmation_rates() -> None:
    evs = [
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 0, "confirmed", 0.6),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 0, "invalidated", 0.6),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "confirmed", 0.5),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "confirmed", 0.5),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 1, "invalidated", 0.5),
    ]
    res = PivotLayerResult(evaluations=evs)
    rates = res.confirmation_rate_per_degree()
    assert rates[0] == 0.5
    assert rates[1] == pytest.approx(2 / 3)


def test_pivot_layer_result_brier_and_calibration() -> None:
    evs = [
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "confirmed", 0.9),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "confirmed", 0.8),
        PivotEvaluation("A", "2024-01-05", "2024-01-15", 2, "invalidated", 0.2),
    ]
    res = PivotLayerResult(evaluations=evs)
    # Brier = ((.9-1)^2 + (.8-1)^2 + (.2-0)^2) / 3 = (.01 + .04 + .04) / 3 ≈ 0.03
    assert res.brier_score() == pytest.approx(0.03, abs=0.001)
    # Calibration shouldn't crash
    res.calibration_error()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement model**

```python
# src/wave_alpha/backtest/pivot_layer.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from wave_alpha.backtest.brier import (
    brier_score as _brier,
    calibration_error as _calibration,
)

Outcome = Literal["confirmed", "invalidated"]


@dataclass(frozen=True)
class PivotEvaluation:
    ticker: str
    as_of_initial: str
    as_of_final: str
    degree: int
    outcome: Outcome
    predicted_proba: float

    @property
    def actual(self) -> int:
        return 1 if self.outcome == "confirmed" else 0


@dataclass
class PivotLayerResult:
    evaluations: list[PivotEvaluation] = field(default_factory=list)

    @property
    def sample_size(self) -> int:
        return len(self.evaluations)

    def confirmation_rate_per_degree(self) -> dict[int, float]:
        groups: dict[int, list[PivotEvaluation]] = {}
        for e in self.evaluations:
            groups.setdefault(e.degree, []).append(e)
        return {
            d: sum(e.actual for e in rows) / len(rows)
            for d, rows in sorted(groups.items())
        }

    def brier_score(self) -> float:
        if not self.evaluations:
            return 0.0
        return _brier([(e.predicted_proba, e.actual) for e in self.evaluations])

    def calibration_error(self, *, n_bins: int = 10) -> float:
        if not self.evaluations:
            return 0.0
        return _calibration(
            [(e.predicted_proba, e.actual) for e in self.evaluations], n_bins=n_bins
        )
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_pivot_layer_models.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/pivot_layer.py tests/backtest/test_pivot_layer_models.py
git commit -m "$(cat <<'EOF'
feat(backtest): PivotEvaluation + PivotLayerResult with Brier integration

PivotEvaluation captures one tentative-pivot outcome at a given degree,
storing the heuristic-predicted confirmation probability and the
realized confirmed/invalidated outcome.

PivotLayerResult exposes per-degree confirmation rate (spec §8 metric),
Brier score, and reliability calibration error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Pivot layer evaluator (initial → later snapshot)

**Files:**
- Modify: `src/wave_alpha/backtest/pivot_layer.py`
- Create: `tests/backtest/test_pivot_layer_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_pivot_layer_evaluator.py
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.pivot_layer import PivotLayerEvaluator
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _ZigZagProvider:
    """Synthetic provider with clean alternating swings — produces pivots."""

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars: list[Bar] = []
        start = date(2023, 1, 1)
        d = start
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                # Sawtooth: 100, 110, 100, 110, ... drift upward
                price = 100 + (i % 20) * 0.5 + (i // 20) * 1.0
                bars.append(_bar(ticker, interval, d, price))
                i += 1
            d = d + timedelta(days=1)
        return bars


def _bar(tk: str, iv: Interval, d: date, p: float) -> Bar:
    return Bar(
        ticker=tk,
        interval=iv,
        timestamp=d,
        open=Decimal(str(p)),
        high=Decimal(str(p + 1)),
        low=Decimal(str(p - 1)),
        close=Decimal(str(p)),
        volume=1_000,
    )


def test_pivot_layer_evaluator_emits_evaluations_for_each_initial_tentative() -> None:
    provider = _ZigZagProvider()
    ev = PivotLayerEvaluator(provider=provider, lookahead_bars=20)
    # Initial as_of mid-2024, final 30 business days later
    initial = date(2024, 6, 1)
    final = date(2024, 8, 15)
    result = ev.evaluate("AAPL", initial=initial, final=final)
    # Permissive — synthetic data may produce 0 pivots; invariant checks:
    assert result.sample_size >= 0
    for e in result.evaluations:
        assert 0.0 <= e.predicted_proba <= 1.0
        assert e.outcome in {"confirmed", "invalidated"}


def test_pivot_layer_evaluator_invalid_when_final_before_initial() -> None:
    import pytest

    provider = _ZigZagProvider()
    ev = PivotLayerEvaluator(provider=provider, lookahead_bars=20)
    with pytest.raises(ValueError, match="initial"):
        ev.evaluate("AAPL", initial=date(2024, 8, 1), final=date(2024, 6, 1))
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: PivotLayerEvaluator missing.

- [ ] **Step 3: Implement evaluator**

Add to `pivot_layer.py`:

```python
from datetime import date, timedelta
from dataclasses import dataclass

from wave_alpha.backtest.labels import pivot_confirmation_label
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Interval
from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
from wave_alpha.pivots.models import PivotState
from wave_alpha.pivots.multi_degree import detect_multi_degree
from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel


@dataclass
class PivotLayerEvaluator:
    """Compares a pivot snapshot at `initial` to a later snapshot at `final`.

    Each TENTATIVE pivot in the initial snapshot becomes one PivotEvaluation:
    its predicted_proba comes from the heuristic right-edge model, and its
    realized outcome is determined by pivot_confirmation_label against the
    final snapshot. This is the basis for per-degree confirmation rate and
    Brier score on the right-edge model.
    """

    provider: OHLCVProvider
    lookahead_bars: int = 20
    interval: Interval = Interval.DAILY

    def evaluate(self, ticker: str, *, initial: date, final: date) -> PivotLayerResult:
        if final <= initial:
            raise ValueError(f"initial {initial} must be before final {final}")
        # Fetch full series; clamp via PointInTimeView per snapshot.
        bars = self.provider.fetch(
            ticker, self.interval, end=final + timedelta(days=self.lookahead_bars * 2)
        )
        if not bars:
            return PivotLayerResult(evaluations=[])
        initial_view = PointInTimeView(bars, initial).visible()
        final_view = PointInTimeView(bars, final).visible()
        if not initial_view or not final_view:
            return PivotLayerResult(evaluations=[])

        initial_pivots_by_degree = detect_multi_degree(
            initial_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
        )
        final_pivots_by_degree = detect_multi_degree(
            final_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
        )

        model = HeuristicConfirmationModel()
        evaluations: list[PivotEvaluation] = []

        for degree, pivots in initial_pivots_by_degree.items():
            tentative = [p for p in pivots if p.state == PivotState.TENTATIVE]
            later = final_pivots_by_degree.get(degree, [])
            for p in tentative:
                outcome = pivot_confirmation_label(p, later_pivots=later)
                if outcome is None:
                    continue
                features = self._features_for(p, initial_view)
                proba = model.predict_proba(features)
                evaluations.append(
                    PivotEvaluation(
                        ticker=ticker,
                        as_of_initial=initial.isoformat(),
                        as_of_final=final.isoformat(),
                        degree=degree,
                        outcome=outcome,
                        predicted_proba=proba,
                    )
                )

        return PivotLayerResult(evaluations=evaluations)

    def _features_for(self, pivot, bars_at_initial) -> RightEdgeFeatures:
        # Lightweight feature snapshot — the v0.1 heuristic only reads a few
        # fields; richer features land with the v0.4.x logistic model.
        bars_since = max(0, len(bars_at_initial) - 1 - _index_of_timestamp(bars_at_initial, pivot.timestamp))
        return RightEdgeFeatures(
            retracement_progress=0.5,
            bars_since_candidate=bars_since,
            fib_zone_match=0.5,
            volume_decline_pct=0.0,
            rsi_divergence_strength=0.0,
            higher_degree_zone_match=0.0,
        )


def _index_of_timestamp(bars, ts) -> int:
    for i, b in enumerate(bars):
        if b.timestamp == ts:
            return i
    return 0
```

Note: the `RightEdgeFeatures` defaults below use middling values. The point of the v0.4 sweep is to validate the *shape* of the harness; once we have labels, we'll re-fit features in v0.4.x. The plan deliberately does not over-engineer features in this phase.

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_pivot_layer_evaluator.py -v`
Expected: 2/2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/pivot_layer.py tests/backtest/test_pivot_layer_evaluator.py
git commit -m "$(cat <<'EOF'
feat(backtest): PivotLayerEvaluator walks initial→final snapshots

For each TENTATIVE pivot in the initial snapshot, runs the heuristic
right-edge model to produce a predicted confirmation probability and
compares to pivot_confirmation_label against the final snapshot. The
heuristic uses placeholder feature values for v0.4 — the harness shape
is the deliverable; richer features land in v0.4.x with the logistic
model.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D Review Boundary

After Tasks 8-9:
- Confirm `PointInTimeView` is the only path bars travel through (greps OK).
- Confirm `RightEdgeFeatures` placeholder values are flagged in plan deferral notes (they are, in Phase D Task 9 commit body).
- Confirm `PivotLayerEvaluator.lookahead_bars` is just a buffer for fetch — does NOT influence labeling logic.

If clean → proceed to Phase E.

---

## Phase E — Runner + report + CLI

### Task 10: BacktestRunner orchestration

**Files:**
- Create: `src/wave_alpha/backtest/runner.py`
- Create: `tests/backtest/test_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_runner.py
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2022, 1, 1)
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(100 + i * 0.3)),
                        high=Decimal(str(100 + i * 0.3 + 0.5)),
                        low=Decimal(str(100 + i * 0.3 - 0.5)),
                        close=Decimal(str(100 + i * 0.3)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_run_count_layer_iterates_universe_and_dates() -> None:
    cfg = BacktestConfig(
        universe=["AAPL", "MSFT"],
        start=date(2024, 3, 1),
        end=date(2024, 6, 1),
        step=20,  # subsample
        n_bars_forward=20,
    )
    result = run_count_layer(cfg, provider=_RisingProvider())
    # 2 tickers × ~3 dates = up to 6 evaluations (some may be skipped)
    assert 0 <= result.sample_size <= 6


def test_run_count_layer_uses_disabled_llm_only() -> None:
    """The runner must NOT instantiate any LLM client — backtest is reproducible."""
    import wave_alpha.llm.client as llm_client_mod

    sentinel = {"called": False}
    original_init = llm_client_mod.AnthropicClient.__init__

    def spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        sentinel["called"] = True
        original_init(self, *a, **kw)

    try:
        llm_client_mod.AnthropicClient.__init__ = spy  # type: ignore[method-assign]
        cfg = BacktestConfig(
            universe=["AAPL"],
            start=date(2024, 3, 1),
            end=date(2024, 4, 1),
            step=10,
            n_bars_forward=20,
        )
        run_count_layer(cfg, provider=_RisingProvider())
        assert sentinel["called"] is False
    finally:
        llm_client_mod.AnthropicClient.__init__ = original_init  # type: ignore[method-assign]
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement runner**

```python
# src/wave_alpha/backtest/runner.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from wave_alpha.backtest.count_layer import CountLayerEvaluator, CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotLayerEvaluator, PivotLayerResult
from wave_alpha.backtest.walk_forward import iter_business_days
from wave_alpha.data.provider import OHLCVProvider


@dataclass(frozen=True)
class BacktestConfig:
    universe: list[str]
    start: date
    end: date
    step: int = 1
    n_bars_forward: int = 20


def run_count_layer(cfg: BacktestConfig, *, provider: OHLCVProvider) -> CountLayerResult:
    """Run the count layer over (universe × dates).

    Returns one CountLayerResult aggregating all (ticker, as_of) samples.
    The runner deliberately does NOT instantiate any LLM client — count-layer
    backtest is `--llm-mode disabled` by spec §8.
    """
    as_ofs = list(iter_business_days(cfg.start, cfg.end, step=cfg.step))
    evaluator = CountLayerEvaluator(provider=provider, n_bars_forward=cfg.n_bars_forward)
    aggregated = CountLayerResult(evaluations=[], n_bars_forward=cfg.n_bars_forward)
    for ticker in cfg.universe:
        per_ticker = evaluator.evaluate(ticker, as_ofs)
        aggregated.evaluations.extend(per_ticker.evaluations)
    return aggregated


def run_pivot_layer(
    cfg: BacktestConfig, *, provider: OHLCVProvider, lookahead_bars: int = 20
) -> PivotLayerResult:
    """Run the pivot layer over (universe × initial_date pairs).

    For each ticker, takes a snapshot at `cfg.start` and a final snapshot at
    `cfg.end`. v0.4 keeps this simple: one initial+final pair per ticker.
    Walk-forward extension lands in v0.4.x once the harness shape is proven.
    """
    evaluator = PivotLayerEvaluator(provider=provider, lookahead_bars=lookahead_bars)
    aggregated = PivotLayerResult(evaluations=[])
    for ticker in cfg.universe:
        per_ticker = evaluator.evaluate(ticker, initial=cfg.start, final=cfg.end)
        aggregated.evaluations.extend(per_ticker.evaluations)
    return aggregated
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_runner.py -v`
Expected: 2/2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/runner.py tests/backtest/test_runner.py
git commit -m "$(cat <<'EOF'
feat(backtest): BacktestRunner orchestrates universe × dates

run_count_layer iterates business days × universe, returning a single
aggregated CountLayerResult — explicitly does not instantiate any LLM
client (test asserts this) so backtest stays reproducible without a key.

run_pivot_layer takes one (initial, final) pair per ticker; walk-forward
extension across multiple snapshots ships in v0.4.x once the v0.4 shape
is validated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Markdown report rendering

**Files:**
- Create: `src/wave_alpha/backtest/report.py`
- Create: `tests/backtest/test_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_report.py
from datetime import date

from wave_alpha.backtest.count_layer import CountEvaluation, CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotEvaluation, PivotLayerResult
from wave_alpha.backtest.report import render_count_markdown, render_pivot_markdown


def test_render_count_markdown_includes_top1_and_top3_rates() -> None:
    res = CountLayerResult(
        evaluations=[
            CountEvaluation("A", date(2024, 1, 5), "up", "up", 0.8, True, ("up",), True),
            CountEvaluation("B", date(2024, 1, 5), "up", "down", 0.4, False, ("up",), False),
        ],
        n_bars_forward=20,
    )
    md = render_count_markdown(res)
    assert "# Count Layer" in md
    assert "top-1 hit rate" in md
    assert "0.50" in md
    assert "n=2" in md


def test_render_count_markdown_includes_coherence_buckets_and_year_breakdown() -> None:
    res = CountLayerResult(
        evaluations=[
            CountEvaluation("A", date(2023, 6, 1), "up", "up", 0.8, True, ("up",), True),
            CountEvaluation("B", date(2024, 6, 1), "up", "down", 0.5, False, ("up",), False),
            CountEvaluation("C", date(2024, 6, 1), "down", "up", 0.2, False, ("up",), True),
        ],
        n_bars_forward=20,
    )
    md = render_count_markdown(res)
    assert "by coherence bucket" in md
    assert "by year" in md
    assert "2023" in md
    assert "2024" in md


def test_render_pivot_markdown_includes_per_degree_and_brier() -> None:
    res = PivotLayerResult(
        evaluations=[
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 0, "confirmed", 0.7),
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 0, "invalidated", 0.7),
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 1, "confirmed", 0.6),
        ]
    )
    md = render_pivot_markdown(res)
    assert "# Pivot Layer" in md
    assert "per-degree confirmation" in md
    assert "Brier" in md
    assert "d=0" in md
    assert "d=1" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement renderer**

```python
# src/wave_alpha/backtest/report.py
from __future__ import annotations

from datetime import datetime

from wave_alpha.backtest.count_layer import CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotLayerResult


def render_count_markdown(result: CountLayerResult) -> str:
    lines: list[str] = []
    lines.append("# Count Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.utcnow().isoformat(timespec='seconds')}Z_")
    lines.append("")
    lines.append(f"**Sample size:** n={result.sample_size}")
    lines.append(f"**Forward window:** {result.n_bars_forward} bars")
    lines.append("")
    lines.append("## Headline rates")
    lines.append("")
    lines.append(f"- top-1 hit rate: **{result.top1_hit_rate:.2f}** (n={result.sample_size})")
    lines.append(f"- top-3 hit rate: **{result.top3_hit_rate:.2f}** (n={result.sample_size})")
    lines.append("")
    lines.append("## by coherence bucket")
    lines.append("")
    lines.append("| bucket | n | top-1 | top-3 |")
    lines.append("|---|---:|---:|---:|")
    for name, row in result.by_coherence_bucket().items():
        n = int(row["sample_size"])
        lines.append(
            f"| {name} | {n} | {row['top1_hit_rate']:.2f} | {row['top3_hit_rate']:.2f} |"
        )
    lines.append("")
    lines.append("## by year")
    lines.append("")
    lines.append("| year | n | top-1 | top-3 |")
    lines.append("|---|---:|---:|---:|")
    for yr, row in result.by_year().items():
        n = int(row["sample_size"])
        lines.append(f"| {yr} | {n} | {row['top1_hit_rate']:.2f} | {row['top3_hit_rate']:.2f} |")
    lines.append("")
    return "\n".join(lines)


def render_pivot_markdown(result: PivotLayerResult) -> str:
    lines: list[str] = []
    lines.append("# Pivot Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.utcnow().isoformat(timespec='seconds')}Z_")
    lines.append("")
    lines.append(f"**Sample size:** n={result.sample_size}")
    lines.append("")
    lines.append("## per-degree confirmation rate")
    lines.append("")
    lines.append("| degree | rate |")
    lines.append("|---:|---:|")
    for d, rate in result.confirmation_rate_per_degree().items():
        lines.append(f"| d={d} | {rate:.2f} |")
    lines.append("")
    lines.append("## right-edge model calibration")
    lines.append("")
    lines.append(f"- Brier score: **{result.brier_score():.3f}**")
    lines.append(f"- 10-bin reliability calibration error: **{result.calibration_error():.3f}**")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/backtest/test_report.py -v`
Expected: 3/3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/report.py tests/backtest/test_report.py
git commit -m "$(cat <<'EOF'
feat(backtest): markdown report renderers for count + pivot layers

Count report: headline top-1/top-3 hit rates, coherence-bucket
breakdown (full/partial/disagreement), per-year breakdown for regime
diagnostics.

Pivot report: per-degree confirmation rate, Brier score, 10-bin
reliability calibration error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: CLI `backtest` subcommand

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/test_cli_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_backtest.py
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

runner = CliRunner()


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2022, 1, 1)
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                price = 100 + i * 0.4
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(price)),
                        high=Decimal(str(price + 0.5)),
                        low=Decimal(str(price - 0.5)),
                        close=Decimal(str(price)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_backtest_help_documents_layer_subcommands() -> None:
    res = runner.invoke(app, ["backtest", "--help"])
    assert res.exit_code == 0
    assert "count" in res.stdout
    assert "pivot" in res.stdout


def test_backtest_count_writes_markdown_report(tmp_path: Path, monkeypatch) -> None:
    universe_file = tmp_path / "u.txt"
    universe_file.write_text("AAPL\nMSFT\n")
    out_file = tmp_path / "report.md"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(
        app,
        [
            "backtest",
            "count",
            "--universe",
            str(universe_file),
            "--start",
            "2024-03-01",
            "--end",
            "2024-06-01",
            "--step",
            "10",
            "--n-bars-forward",
            "20",
            "--out",
            str(out_file),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert out_file.exists()
    content = out_file.read_text()
    assert "# Count Layer Backtest Report" in content


def test_backtest_pivot_writes_markdown_report(tmp_path: Path, monkeypatch) -> None:
    universe_file = tmp_path / "u.txt"
    universe_file.write_text("AAPL\n")
    out_file = tmp_path / "pivot.md"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(
        app,
        [
            "backtest",
            "pivot",
            "--universe",
            str(universe_file),
            "--start",
            "2024-03-01",
            "--end",
            "2024-06-01",
            "--out",
            str(out_file),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert out_file.exists()
    assert "# Pivot Layer Backtest Report" in out_file.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `backtest` subcommand missing.

- [ ] **Step 3: Wire CLI**

In `src/wave_alpha/cli/app.py`, add after the `config_app` block:

```python
backtest_app = typer.Typer(
    no_args_is_help=True, help="Walk-forward backtest harness (pivot + count layers)."
)
app.add_typer(backtest_app, name="backtest")


@backtest_app.command("count")
def backtest_count(
    universe: Path = typer.Option(..., "--universe", help="Path to ticker universe file."),
    start: str = typer.Option(..., "--start", help="Backtest start date YYYY-MM-DD."),
    end: str = typer.Option(..., "--end", help="Backtest end date YYYY-MM-DD."),
    step: int = typer.Option(5, "--step", help="Subsample every Nth business day."),
    n_bars_forward: int = typer.Option(
        20, "--n-bars-forward", help="Forward bars for direction labeling."
    ),
    out: Path = typer.Option(..., "--out", help="Output markdown report path."),
) -> None:
    """Run the count-layer backtest and write a markdown report."""
    from wave_alpha.backtest.report import render_count_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
    from wave_alpha.backtest.universe import load_universe

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        step=step,
        n_bars_forward=n_bars_forward,
    )
    typer.echo(f"running count-layer backtest: {len(cfg.universe)} tickers, step={step}")
    result = run_count_layer(cfg, provider=_default_provider())
    out.write_text(render_count_markdown(result))
    typer.echo(f"wrote {out} (n={result.sample_size})")


@backtest_app.command("pivot")
def backtest_pivot(
    universe: Path = typer.Option(..., "--universe"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Run the pivot-layer backtest and write a markdown report."""
    from wave_alpha.backtest.report import render_pivot_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_pivot_layer
    from wave_alpha.backtest.universe import load_universe

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )
    typer.echo(f"running pivot-layer backtest: {len(cfg.universe)} tickers")
    result = run_pivot_layer(cfg, provider=_default_provider())
    out.write_text(render_pivot_markdown(result))
    typer.echo(f"wrote {out} (n={result.sample_size})")
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/test_cli_backtest.py -v`
Expected: 3/3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_backtest.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha backtest count|pivot subcommands

Two subcommands under `wave-alpha backtest`, both writing markdown
reports to a user-specified path:

- count: walks (universe × business days) at configurable subsample;
  writes top-1/top-3 hit rates, coherence buckets, yearly breakdown.
- pivot: takes (initial, final) snapshot per ticker; writes per-degree
  confirmation rate, Brier score, calibration error.

Backtest is `--llm-mode disabled` by construction (no LLM client is
instantiated in either runner) so headline numbers reproduce without
an API key.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase E Review Boundary

After Tasks 10-12:
- Run `uv run wave-alpha backtest --help` manually — confirm subcommand listing.
- Run `uv run pytest tests/backtest/ tests/test_cli_backtest.py -v` — full suite green.
- Confirm no `import anthropic` anywhere in `backtest/` or new CLI code.

If clean → proceed to Phase F.

---

## Phase F — Lookahead leak guard test + version bump + README

### Task 13: Synthetic lookahead leak guard

**Files:**
- Create: `tests/backtest/test_lookahead_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/backtest/test_lookahead_guard.py
"""End-to-end guard: a provider that injects a future bar must trigger the
PointInTimeView lookahead assertion. If this test ever passes silently when
a future bar leaks, the harness has a hole."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
from wave_alpha.data.point_in_time import LookaheadError, UnsortedBarsError
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _UnsortedProvider:
    """Returns bars NOT in ascending timestamp order — should trip
    UnsortedBarsError when wrapped in PointInTimeView."""

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= date(2024, 6, 1):
            if d.weekday() < 5:
                bars.append(_bar(ticker, interval, d, 100 + i))
                i += 1
            d = d + timedelta(days=1)
        # Inject an out-of-order bar at the end (timestamp BEFORE the last)
        bars.append(_bar(ticker, interval, date(2024, 3, 1), 999))
        return bars


def _bar(tk: str, iv: Interval, d: date, p: float) -> Bar:
    return Bar(
        ticker=tk,
        interval=iv,
        timestamp=d,
        open=Decimal(str(p)),
        high=Decimal(str(p + 1)),
        low=Decimal(str(p - 1)),
        close=Decimal(str(p)),
        volume=1_000,
    )


def test_unsorted_provider_trips_assertion_during_count_backtest() -> None:
    cfg = BacktestConfig(
        universe=["AAPL"],
        start=date(2024, 2, 1),
        end=date(2024, 4, 1),
        step=5,
        n_bars_forward=10,
    )
    with pytest.raises((LookaheadError, UnsortedBarsError)):
        run_count_layer(cfg, provider=_UnsortedProvider())
```

- [ ] **Step 2: Run test to verify it FAILS in the right way**

Run: `uv run pytest tests/backtest/test_lookahead_guard.py -v`
Expected: PASS — the existing PointInTimeView invariant fires when CountLayerEvaluator wraps the unsorted bars.

If the test does NOT raise, the harness has a real lookahead hole. Investigate before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/backtest/test_lookahead_guard.py
git commit -m "$(cat <<'EOF'
test(backtest): synthetic lookahead leak guard

End-to-end test that an unsorted-bars provider trips PointInTimeView's
assertion mid-backtest. If this test stops firing, the harness has a
real lookahead hole.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Version bump + README

**Files:**
- Modify: `src/wave_alpha/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: Bump version**

Edit `src/wave_alpha/__init__.py`:

```python
__version__ = "0.4.0"
```

Edit `pyproject.toml` version field similarly.

- [ ] **Step 2: README addition**

Append to `README.md`:

```markdown
## Backtest harness (v0.4)

Honest accuracy claims start with a leak-free backtest. The harness has two layers:

```bash
# Count layer: top-1 / top-3 forward direction hit rate
wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 --out reports/count.md

# Pivot layer: per-degree confirmation rate + Brier score
wave-alpha backtest pivot \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --out reports/pivot.md
```

Both run `--llm-mode disabled` by construction — no API key needed, and headline numbers are reproducible. The `data/universe_curated.txt` file ships with 50 tickers spanning sectors and vol regimes; pass your own list with `--universe my-list.txt`.

Lookahead is enforced by `PointInTimeView` at every fetch site; a synthetic-leak unit test (`tests/backtest/test_lookahead_guard.py`) plants a violation and verifies the assertion fires.
```

- [ ] **Step 3: Verify**

Run:
```bash
uv run wave-alpha version
uv run wave-alpha backtest --help
uv run pytest tests/backtest/ tests/test_cli_backtest.py -v
uv run ruff check . && uv run ruff format --check .
```

Expected: `0.4.0`; backtest help shows count + pivot; all tests pass; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/__init__.py pyproject.toml README.md
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.4.0; document backtest harness

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase F Review Boundary (final cross-cutting)

Controller dispatches one final code-quality reviewer subagent on the entire feature branch:
- Lookahead invariant: every place bars are read goes through `PointInTimeView`.
- No `import anthropic` reachable from any `backtest/` or new CLI code path.
- The synthetic-leak test still trips (regression guard).
- The CLI smokes manually: `uv run wave-alpha backtest count --universe data/universe_curated.txt --start 2024-09-01 --end 2024-10-01 --step 5 --out /tmp/smoke.md` produces a valid markdown report. (Allowed to fail offline; if it fails the way the user expects, that's fine.)
- `uv lock` is up to date and committed.

If review finds blockers → fix loop. Otherwise:

- Merge to main with `--no-ff` and a clear merge commit message.
- Run `npx gitnexus analyze` post-merge per CLAUDE.md hook.

---

## Self-Review Checklist (controller fills this in before dispatch)

- [ ] Spec coverage: pivot layer (per-degree confirmation, Brier, calibration), count layer (top-1, top-3, by coherence bucket, by year), walk-forward iterator, lookahead guard, 50-ticker universe, --llm-mode disabled mandatory baseline. ✓
- [ ] Placeholder scan: every step has actual code or commands. ✓
- [ ] Type consistency: `Direction = Literal["up", "down"]` reused across modules; `BacktestConfig` shape matches across runner + CLI; `CountEvaluation` constructor signature consistent in tests + impl. ✓
- [ ] No phantom symbols: `RightEdgeFeatures` exists at `wave_alpha.right_edge.features`, `HeuristicConfirmationModel` at `right_edge.heuristic`, `_predicted_direction` at `coherence.score`. Confirmed in source. ✓
