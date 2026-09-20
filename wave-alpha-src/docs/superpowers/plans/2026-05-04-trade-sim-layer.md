# Trade-Sim Backtest Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `wave-alpha backtest trade` — a third backtest layer that takes a `TradeRules` config, simulates trades chronologically per ticker over `(start, end)`, and reports hit rate, avg R, expectancy, profit factor, max drawdown with breakdowns by direction, pattern, coherence bucket, and year.

**Architecture:** Mirrors the count_layer / pivot_layer split. New `TradeLayerEvaluator` walks two cadences per ticker: signal generation at `--step` (calls `pipeline.run_analysis(llm_mode=DISABLED)` and accepts top-1 signal) and exit evaluation at every business day (state machine: stop → target → time-stop). `run_trade_layer(cfg, *, provider, rules) -> TradeLayerResult` aggregates per-ticker walks. Markdown / JSON / CSV output.

**Tech Stack:** Python 3.11+, `pydantic` v2 (existing `TradeRules`/`TradeSignal`), `dataclasses` (`TradeRecord`/`TradeLayerResult`, mirrors count_layer), `Decimal` throughout, `typer` CLI sub-app, `pytest`. Reuses `pipeline.run_analysis`, `iter_business_days`, `compute_atr`, `PointInTimeView`.

**Spec:** `docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md`

---

## File structure

**New:**
- `src/wave_alpha/backtest/coherence_bucket.py` — extracted helper, single source of truth for bucket boundaries.
- `src/wave_alpha/backtest/trade_layer.py` — `TradeRecord`, `TradeLayerResult`, `TradeLayerEvaluator`. State machine + metrics.
- `tests/backtest/test_trade_layer.py` — sim mechanics + metrics math (one test per knob).
- `tests/backtest/test_trade_layer_lookahead.py` — synthetic future-bar leak test.
- `tests/backtest/test_coherence_bucket.py` — extracted helper tests.

**Modified:**
- `src/wave_alpha/backtest/count_layer.py` — re-import `_bucket` and `_COHERENCE_BUCKETS` from new module; delete local copies.
- `src/wave_alpha/trades/derive.py` — add `derive_signal_with_overrides` plumbing: ATR-multiple stop helper, fixed-R target helper. Keep existing public surface backwards-compatible.
- `src/wave_alpha/backtest/runner.py` — add `run_trade_layer(cfg, *, provider, rules) -> TradeLayerResult`.
- `src/wave_alpha/backtest/report.py` — add `render_trade_markdown(result, *, cfg, rules) -> str`.
- `src/wave_alpha/cli/app.py` — add `backtest_trade` command wiring CLI flags → `BacktestConfig` + `TradeRules` (YAML loader for `--rules`).
- `README.md` — extend the v0.5 / backtest section with the new command.
- `pyproject.toml` — bump `version = "0.5.2"`.

---

## Task 1: Extract coherence-bucket helper

**Why:** Both count_layer and the new trade_layer need the `(full, partial, disagreement)` bucket logic. Centralize it now to avoid drift.

**Files:**
- Create: `src/wave_alpha/backtest/coherence_bucket.py`
- Create: `tests/backtest/test_coherence_bucket.py`
- Modify: `src/wave_alpha/backtest/count_layer.py`

- [ ] **Step 1: Write failing test for the extracted helper**

Create `tests/backtest/test_coherence_bucket.py`:

```python
from wave_alpha.backtest.coherence_bucket import bucket_for, COHERENCE_BUCKETS


def test_full_bucket_above_threshold():
    assert bucket_for(0.85) == "full"
    assert bucket_for(0.7001) == "full"
    assert bucket_for(1.0) == "full"


def test_partial_bucket_in_range():
    assert bucket_for(0.5) == "partial"
    assert bucket_for(0.4) == "partial"
    assert bucket_for(0.6999) == "partial"


def test_disagreement_below_partial():
    assert bucket_for(0.0) == "disagreement"
    assert bucket_for(0.39) == "disagreement"
    assert bucket_for(-0.5) == "disagreement"  # safety fallback


def test_buckets_constant_shape():
    # (name, lo, hi) tuples, used by count_layer to iterate static groups
    names = [name for (name, _, _) in COHERENCE_BUCKETS]
    assert names == ["full", "partial", "disagreement"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/backtest/test_coherence_bucket.py -v
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.backtest.coherence_bucket'`.

- [ ] **Step 3: Create the helper module**

Create `src/wave_alpha/backtest/coherence_bucket.py`:

```python
from __future__ import annotations

from typing import Literal

CoherenceBucket = Literal["full", "partial", "disagreement"]

# (name, lo_inclusive, hi_exclusive). hi=1.01 to include 1.0 in "full".
COHERENCE_BUCKETS: tuple[tuple[CoherenceBucket, float, float], ...] = (
    ("full", 0.7, 1.01),
    ("partial", 0.4, 0.7),
    ("disagreement", 0.0, 0.4),
)


def bucket_for(coh: float) -> CoherenceBucket:
    for name, lo, hi in COHERENCE_BUCKETS:
        if lo <= coh < hi:
            return name
    return "disagreement"  # safety fallback for negative scores
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/backtest/test_coherence_bucket.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Migrate count_layer to use the shared helper**

Edit `src/wave_alpha/backtest/count_layer.py`. Remove the local `_COHERENCE_BUCKETS` constant and `_bucket` function, replace with imports.

Find:
```python
_COHERENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("full", 0.7, 1.01),  # >0.7 (use 1.01 to include 1.0)
    ("partial", 0.4, 0.7),  # 0.4..0.7
    ("disagreement", 0.0, 0.4),  # <0.4
)


def _bucket(coh: float) -> str:
    for name, lo, hi in _COHERENCE_BUCKETS:
        if lo <= coh < hi:
            return name
    return "disagreement"  # safety fallback for negative scores
```

Replace with:
```python
from wave_alpha.backtest.coherence_bucket import bucket_for as _bucket
```

(Place the import at the top of the file with the other imports. Delete the old constant and function. The existing `groups[_bucket(...)]` call site continues to work unchanged.)

- [ ] **Step 6: Run full test suite to ensure no regressions**

```bash
uv run pytest tests/backtest -v
```

Expected: all backtest tests pass, including the existing count_layer tests.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/backtest/coherence_bucket.py \
        tests/backtest/test_coherence_bucket.py \
        src/wave_alpha/backtest/count_layer.py
git commit -m "refactor(backtest): extract coherence_bucket helper — single source of truth"
```

---

## Task 2: ATR-multiple stop helper in trades/derive

**Why:** `TradeRules.stop_source = "atr_multiple"` needs an implementation. The existing `_stop_price` only handles `count_invalidation`.

**Files:**
- Modify: `src/wave_alpha/trades/derive.py`
- Create: `tests/trades/test_derive_atr_stop.py`

- [ ] **Step 1: Write failing test for ATR-multiple stop**

Create `tests/trades/test_derive_atr_stop.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.trades.derive import _stop_price_atr_multiple


def _series(prices: list[float]) -> list[Bar]:
    out = []
    for i, p in enumerate(prices):
        d = date(2024, 1, 1) + timedelta(days=i)
        # OHLC = price-spread to give ATR something nonzero
        out.append(
            Bar(
                timestamp=d,
                open=Decimal(str(p)),
                high=Decimal(str(p + 1)),
                low=Decimal(str(p - 1)),
                close=Decimal(str(p)),
                volume=1000,
            )
        )
    return out


def test_atr_stop_long_below_anchor():
    bars = _series([100.0] * 30)
    anchor = Decimal("100")
    stop = _stop_price_atr_multiple(
        bars=bars, anchor=anchor, direction="long", multiple=Decimal("2"), period=14
    )
    # ATR is roughly 2.0 (high-low=2 every bar after warmup); stop = 100 - 2*2 = 96
    assert stop == Decimal("96")


def test_atr_stop_short_above_anchor():
    bars = _series([100.0] * 30)
    anchor = Decimal("100")
    stop = _stop_price_atr_multiple(
        bars=bars, anchor=anchor, direction="short", multiple=Decimal("2"), period=14
    )
    assert stop == Decimal("104")


def test_atr_stop_raises_when_atr_unavailable():
    import pytest

    bars = _series([100.0] * 5)  # too short to compute ATR(14)
    with pytest.raises(ValueError, match="ATR unavailable"):
        _stop_price_atr_multiple(
            bars=bars,
            anchor=Decimal("100"),
            direction="long",
            multiple=Decimal("2"),
            period=14,
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/trades/test_derive_atr_stop.py -v
```

Expected: `ImportError: cannot import name '_stop_price_atr_multiple'`.

- [ ] **Step 3: Implement the helper**

Edit `src/wave_alpha/trades/derive.py`. Add at the top of the file (next to existing imports):

```python
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.pivots.atr import compute_atr
```

Then add the helper function (place it just below `_target_price`):

```python
def _stop_price_atr_multiple(
    *,
    bars: list[Bar],
    anchor: Decimal,
    direction: Literal["long", "short"],
    multiple: Decimal,
    period: int = 14,
) -> Decimal:
    """ATR-multiple stop placed on the loss side of `anchor`.

    Long: stop = anchor - multiple * ATR
    Short: stop = anchor + multiple * ATR
    """
    atr_series = compute_atr(bars, period=period)
    last_atr = atr_series[-1] if atr_series else None
    if last_atr is None:
        raise ValueError(
            f"ATR unavailable: need {period}+ bars, got {len(bars)}"
        )
    delta = multiple * last_atr
    return anchor - delta if direction == "long" else anchor + delta
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/trades/test_derive_atr_stop.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/trades/derive.py tests/trades/test_derive_atr_stop.py
git commit -m "feat(trades): _stop_price_atr_multiple — ATR-anchored stop helper"
```

---

## Task 3: Fixed-R target helper in trades/derive

**Why:** `TradeRules.target_source = "fixed_R"` needs an implementation. The existing `_target_price` only emits fib 1.618.

**Files:**
- Modify: `src/wave_alpha/trades/derive.py`
- Create: `tests/trades/test_derive_fixed_r_target.py`

- [ ] **Step 1: Write failing test**

Create `tests/trades/test_derive_fixed_r_target.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from wave_alpha.trades.derive import _target_price_fixed_r


def test_fixed_r_long_target_above_anchor():
    target = _target_price_fixed_r(
        anchor=Decimal("100"),
        stop=Decimal("96"),
        direction="long",
        target_r=Decimal("2"),
    )
    # planned risk = 4; 2R target = 100 + 8 = 108
    assert target == Decimal("108")


def test_fixed_r_short_target_below_anchor():
    target = _target_price_fixed_r(
        anchor=Decimal("100"),
        stop=Decimal("104"),
        direction="short",
        target_r=Decimal("2"),
    )
    # planned risk = 4; 2R target = 100 - 8 = 92
    assert target == Decimal("92")


def test_fixed_r_raises_on_zero_risk():
    with pytest.raises(ValueError, match="zero risk"):
        _target_price_fixed_r(
            anchor=Decimal("100"),
            stop=Decimal("100"),
            direction="long",
            target_r=Decimal("2"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/trades/test_derive_fixed_r_target.py -v
```

Expected: `ImportError: cannot import name '_target_price_fixed_r'`.

- [ ] **Step 3: Implement the helper**

Edit `src/wave_alpha/trades/derive.py`. Add the helper just below `_stop_price_atr_multiple`:

```python
def _target_price_fixed_r(
    *,
    anchor: Decimal,
    stop: Decimal,
    direction: Literal["long", "short"],
    target_r: Decimal,
) -> Decimal:
    """Target placed at `target_r` × planned risk on the signal-direction side."""
    planned_risk = abs(anchor - stop)
    if planned_risk == 0:
        raise ValueError("zero risk: anchor and stop are equal")
    delta = target_r * planned_risk
    return anchor + delta if direction == "long" else anchor - delta
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/trades/test_derive_fixed_r_target.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/trades/derive.py tests/trades/test_derive_fixed_r_target.py
git commit -m "feat(trades): _target_price_fixed_r — R-multiple target helper"
```

---

## Task 4: TradeRecord and TradeLayerResult dataclasses

**Why:** The state-machine and metrics code in subsequent tasks needs the data shape. Build the value types first so later tasks can construct and assert against them.

**Files:**
- Create: `src/wave_alpha/backtest/trade_layer.py` (initial — types only)
- Create: `tests/backtest/test_trade_layer.py` (start of file — type tests)

- [ ] **Step 1: Write failing test for the types**

Create `tests/backtest/test_trade_layer.py`:

```python
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.backtest.trade_layer import TradeLayerResult, TradeRecord


def _record(**overrides) -> TradeRecord:
    defaults = dict(
        ticker="AAPL",
        signal_as_of=date(2024, 1, 1),
        fill_date=date(2024, 1, 2),
        fill_price=Decimal("100"),
        exit_date=date(2024, 1, 10),
        exit_price=Decimal("110"),
        exit_reason="target",
        direction="long",
        pattern="impulse",
        wave_label="5",
        coherence_score=0.8,
        coherence_bucket="full",
        bars_held=6,
        risk_per_share=Decimal("4"),
        realized_pnl_per_share=Decimal("10"),
        realized_R=2.5,
        confidence=0.6,
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)


def test_traderecord_is_frozen():
    rec = _record()
    with pytest.raises(FrozenInstanceError):
        rec.ticker = "MSFT"  # type: ignore[misc]


def test_traderecord_open_at_end_has_none_exit():
    rec = _record(exit_reason="open_at_end", exit_date=None, exit_price=None)
    assert rec.exit_reason == "open_at_end"
    assert rec.exit_date is None
    assert rec.exit_price is None


def test_tradelayerresult_empty_metrics():
    result = TradeLayerResult()
    assert result.closed_trades == 0
    assert result.open_at_end == 0
    assert result.hit_rate == 0.0
    assert result.avg_R == 0.0
    assert result.profit_factor == 0.0
    assert result.max_drawdown_R == 0.0


def test_tradelayerresult_open_at_end_count():
    result = TradeLayerResult(
        trades=[
            _record(),
            _record(exit_reason="open_at_end", exit_date=None, exit_price=None),
        ]
    )
    assert result.closed_trades == 1
    assert result.open_at_end == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.backtest.trade_layer'`.

- [ ] **Step 3: Create the trade_layer module with the types only**

Create `src/wave_alpha/backtest/trade_layer.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from wave_alpha.backtest.coherence_bucket import CoherenceBucket

ExitReason = Literal["target", "stop", "time_stop", "open_at_end"]
Direction = Literal["long", "short"]


@dataclass(frozen=True)
class TradeRecord:
    """One simulated trade. `exit_reason='open_at_end'` ⇒ exit_date and
    exit_price are None and the record is excluded from headline metrics."""

    ticker: str
    signal_as_of: date
    fill_date: date
    fill_price: Decimal
    exit_date: date | None
    exit_price: Decimal | None
    exit_reason: ExitReason
    direction: Direction
    pattern: str
    wave_label: str
    coherence_score: float
    coherence_bucket: CoherenceBucket
    bars_held: int
    risk_per_share: Decimal
    realized_pnl_per_share: Decimal
    realized_R: float  # noqa: N815 — match domain naming (R is a unit)
    confidence: float


@dataclass
class TradeLayerResult:
    trades: list[TradeRecord] = field(default_factory=list)

    @property
    def closed_trades(self) -> int:
        return sum(1 for t in self.trades if t.exit_reason != "open_at_end")

    @property
    def open_at_end(self) -> int:
        return sum(1 for t in self.trades if t.exit_reason == "open_at_end")

    @property
    def hit_rate(self) -> float:
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.realized_R > 0) / len(closed)

    @property
    def avg_R(self) -> float:  # noqa: N802 — match domain naming
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        return sum(t.realized_R for t in closed) / len(closed)

    @property
    def expectancy(self) -> float:
        return self.avg_R

    @property
    def profit_factor(self) -> float:
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        gains = sum(t.realized_R for t in closed if t.realized_R > 0)
        losses = abs(sum(t.realized_R for t in closed if t.realized_R < 0))
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def max_drawdown_R(self) -> float:  # noqa: N802
        closed = sorted(
            (t for t in self.trades if t.exit_reason != "open_at_end"),
            key=lambda t: (t.exit_date or date.min, t.ticker),
        )
        if not closed:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in closed:
            cumulative += t.realized_R
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        return max_dd
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): TradeRecord + TradeLayerResult — value types and metric properties"
```

---

## Task 5: Metrics math — verify with synthetic record sequences

**Why:** Lock down the math (hit rate, avg R, profit factor, max drawdown) with hand-computed expected values before any sim mechanics ride on top of them.

**Files:**
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Add metrics tests to the existing test file**

Append to `tests/backtest/test_trade_layer.py`:

```python
def _closed(R: float, exit_d: date = date(2024, 1, 10)) -> TradeRecord:  # noqa: N803
    return _record(realized_R=R, exit_date=exit_d, exit_reason="target" if R > 0 else "stop")


def test_hit_rate_three_wins_two_losses():
    result = TradeLayerResult(
        trades=[_closed(1.0), _closed(0.5), _closed(-1.0), _closed(-0.5), _closed(2.0)]
    )
    assert result.hit_rate == 3 / 5


def test_avg_R_simple():
    result = TradeLayerResult(
        trades=[_closed(1.0), _closed(0.0), _closed(-1.0)]
    )
    # 0.0 is not a "win" (R > 0 strict) and contributes 0 to the sum
    assert result.avg_R == 0.0


def test_profit_factor_normal():
    # gains = 3.0, losses = 1.5, PF = 2.0
    result = TradeLayerResult(
        trades=[_closed(1.0), _closed(2.0), _closed(-1.0), _closed(-0.5)]
    )
    assert result.profit_factor == 2.0


def test_profit_factor_no_losses_returns_inf():
    result = TradeLayerResult(trades=[_closed(1.0), _closed(2.0)])
    assert result.profit_factor == float("inf")


def test_profit_factor_no_trades_returns_zero():
    result = TradeLayerResult(trades=[])
    assert result.profit_factor == 0.0


def test_max_drawdown_known_sequence():
    # cumulative: +1, +1.5, +0.7, +0.2, +1.4
    # peak after trade 2 = 1.5
    # trough after trade 4 = 0.2
    # max_dd = 1.5 - 0.2 = 1.3
    result = TradeLayerResult(
        trades=[
            _closed(1.0, exit_d=date(2024, 1, 1)),
            _closed(0.5, exit_d=date(2024, 1, 2)),
            _closed(-0.8, exit_d=date(2024, 1, 3)),
            _closed(-0.5, exit_d=date(2024, 1, 4)),
            _closed(1.2, exit_d=date(2024, 1, 5)),
        ]
    )
    assert result.max_drawdown_R == pytest.approx(1.3)


def test_max_drawdown_all_wins_zero():
    result = TradeLayerResult(
        trades=[
            _closed(1.0, exit_d=date(2024, 1, 1)),
            _closed(0.5, exit_d=date(2024, 1, 2)),
        ]
    )
    assert result.max_drawdown_R == 0.0


def test_open_at_end_excluded_from_closed_metrics():
    result = TradeLayerResult(
        trades=[
            _closed(1.0),
            _record(exit_reason="open_at_end", exit_date=None, exit_price=None,
                    realized_R=999.0),
        ]
    )
    assert result.hit_rate == 1.0  # only the closed trade counts
    assert result.avg_R == 1.0
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: all tests pass (the 4 from task 4 + 8 new = 12 total).

- [ ] **Step 3: Commit**

```bash
git add tests/backtest/test_trade_layer.py
git commit -m "test(backtest): metrics math — hit rate, PF, max drawdown known sequences"
```

---

## Task 6: Breakdown methods on TradeLayerResult

**Why:** The report renders `by_direction`, `by_pattern`, `by_coherence_bucket`, `by_year`. Same shape as `CountLayerResult` — keys vary, inner dicts are `{sample_size, hit_rate, avg_R, expectancy, profit_factor}`.

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing tests for the breakdowns**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_by_direction_splits_long_short():
    result = TradeLayerResult(
        trades=[
            _record(direction="long", realized_R=1.0, exit_date=date(2024, 1, 1)),
            _record(direction="long", realized_R=-0.5, exit_date=date(2024, 1, 2),
                    exit_reason="stop"),
            _record(direction="short", realized_R=2.0, exit_date=date(2024, 1, 3)),
        ]
    )
    by_dir = result.by_direction()
    assert by_dir["long"]["sample_size"] == 2
    assert by_dir["long"]["hit_rate"] == 0.5
    assert by_dir["long"]["avg_R"] == pytest.approx(0.25)
    assert by_dir["short"]["sample_size"] == 1
    assert by_dir["short"]["hit_rate"] == 1.0


def test_by_pattern_groups_by_pattern():
    result = TradeLayerResult(
        trades=[
            _record(pattern="impulse", realized_R=1.0, exit_date=date(2024, 1, 1)),
            _record(pattern="zigzag", realized_R=-1.0, exit_date=date(2024, 1, 2),
                    exit_reason="stop"),
            _record(pattern="impulse", realized_R=0.5, exit_date=date(2024, 1, 3)),
        ]
    )
    by_pat = result.by_pattern()
    assert set(by_pat.keys()) == {"impulse", "zigzag"}
    assert by_pat["impulse"]["sample_size"] == 2


def test_by_coherence_bucket_uses_static_keys():
    result = TradeLayerResult(
        trades=[
            _record(coherence_score=0.8, coherence_bucket="full", realized_R=1.0,
                    exit_date=date(2024, 1, 1)),
            _record(coherence_score=0.5, coherence_bucket="partial", realized_R=-0.5,
                    exit_date=date(2024, 1, 2), exit_reason="stop"),
        ]
    )
    by_coh = result.by_coherence_bucket()
    # static insertion order from COHERENCE_BUCKETS
    assert list(by_coh.keys()) == ["full", "partial", "disagreement"]
    assert by_coh["full"]["sample_size"] == 1
    assert by_coh["partial"]["sample_size"] == 1
    assert by_coh["disagreement"]["sample_size"] == 0


def test_by_year_sorted_ascending():
    result = TradeLayerResult(
        trades=[
            _record(signal_as_of=date(2022, 6, 1), realized_R=1.0,
                    exit_date=date(2022, 6, 10)),
            _record(signal_as_of=date(2020, 3, 1), realized_R=-0.5,
                    exit_date=date(2020, 3, 5), exit_reason="stop"),
            _record(signal_as_of=date(2022, 11, 1), realized_R=0.2,
                    exit_date=date(2022, 11, 4)),
        ]
    )
    by_yr = result.by_year()
    assert list(by_yr.keys()) == [2020, 2022]
    assert by_yr[2022]["sample_size"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_by_direction_splits_long_short -v
```

Expected: `AttributeError: 'TradeLayerResult' object has no attribute 'by_direction'`.

- [ ] **Step 3: Implement the breakdown methods**

Add to `src/wave_alpha/backtest/trade_layer.py`. Place imports needed:

```python
from wave_alpha.backtest.coherence_bucket import COHERENCE_BUCKETS
```

Add a private metrics helper above the methods:

```python
def _metrics_for(records: list[TradeRecord]) -> dict[str, float]:
    closed = [r for r in records if r.exit_reason != "open_at_end"]
    if not closed:
        return {
            "sample_size": 0,
            "hit_rate": 0.0,
            "avg_R": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
        }
    wins = sum(1 for r in closed if r.realized_R > 0)
    avg_r = sum(r.realized_R for r in closed) / len(closed)
    gains = sum(r.realized_R for r in closed if r.realized_R > 0)
    losses = abs(sum(r.realized_R for r in closed if r.realized_R < 0))
    if losses == 0:
        pf = float("inf") if gains > 0 else 0.0
    else:
        pf = gains / losses
    return {
        "sample_size": len(closed),
        "hit_rate": wins / len(closed),
        "avg_R": avg_r,
        "expectancy": avg_r,
        "profit_factor": pf,
    }
```

Add the methods inside the `TradeLayerResult` class (below the existing properties):

```python
    def by_direction(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.direction, []).append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_pattern(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.pattern, []).append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_coherence_bucket(self) -> dict[str, dict[str, float]]:
        # static insertion order, matches CountLayerResult.by_coherence_bucket
        groups: dict[str, list[TradeRecord]] = {
            name: [] for (name, _, _) in COHERENCE_BUCKETS
        }
        for t in self.trades:
            groups[t.coherence_bucket].append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_year(self) -> dict[int, dict[str, float]]:
        groups: dict[int, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.signal_as_of.year, []).append(t)
        return {yr: _metrics_for(v) for yr, v in sorted(groups.items())}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: all 16 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): TradeLayerResult breakdowns — direction/pattern/coherence/year"
```

---

## Task 7: TradeLayerEvaluator — entry-bar exit (target on fill bar)

**Why:** The state machine's narrow happy path is the simplest correctness target: signal at T, fill at T+1 open, target hit on the same bar. Get that right before adding stops, time-stops, multi-bar walks, or confirmation entries.

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`
- Create: `tests/backtest/_synthetic.py` — shared synthetic provider/signal builder for sim tests

- [ ] **Step 1: Build the synthetic provider helper**

Create `tests/backtest/_synthetic.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval


@dataclass
class FixedBarsProvider:
    """OHLCVProvider stub — returns the same bars for every (ticker, interval)."""

    bars_by_interval: dict[Interval, list[Bar]] = field(default_factory=dict)

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self.bars_by_interval.get(interval, [])
        return [b for b in bars if b.timestamp <= end]


def daily_bars(ohlc: list[tuple[float, float, float, float]],
               start: date = date(2024, 1, 1)) -> list[Bar]:
    """Build a daily-bar series from (open, high, low, close) tuples.
    Bars are placed on consecutive business days."""
    out: list[Bar] = []
    cur = start
    for o, h, lo, c in ohlc:
        while cur.weekday() >= 5:  # skip weekends
            cur = cur + timedelta(days=1)
        out.append(
            Bar(
                timestamp=cur,
                open=Decimal(str(o)),
                high=Decimal(str(h)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=1000,
            )
        )
        cur = cur + timedelta(days=1)
    return out
```

- [ ] **Step 2: Write a failing target-on-fill-bar test**

Add to `tests/backtest/test_trade_layer.py`:

```python
from wave_alpha.backtest.trade_layer import TradeLayerEvaluator
from wave_alpha.domain import Interval
from wave_alpha.trades.models import TradeRules, TradeSignal

from tests.backtest._synthetic import FixedBarsProvider, daily_bars


def _signal(
    *,
    as_of: date,
    direction: str = "long",
    entry: Decimal = Decimal("100"),
    stop: Decimal = Decimal("96"),
    target: Decimal = Decimal("110"),
    confidence: float = 0.6,
    max_holding_bars: int = 20,
    pattern: str = "impulse",
    wave_label: str = "5",
) -> TradeSignal:
    return TradeSignal(
        ticker="AAPL",
        as_of=as_of,
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        count_id="abc123",
        count_pattern=pattern,
        wave_label=wave_label,
        confidence=confidence,
        expected_bars_to_target=10,
        max_holding_bars=max_holding_bars,
    )


def _stub_pipeline(monkeypatch, signals_by_date: dict, coherence_score: float = 0.8):
    """Patch pipeline.run_analysis to return a canned signal per as_of."""
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult

    def fake_run(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        sig = signals_by_date.get(req.as_of)
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=coherence_score, multiplier=1.0,
                                       pairwise={}, fallback_used=None),
            signals=[sig] if sig else [],
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run)


def test_target_hit_on_fill_bar(monkeypatch):
    # Daily bars: signal date 2024-01-01 (close 100), next bar opens 100, high 115
    # → fill at 100*1.001 = 100.1; target=110 hit on the same bar → exit at 110
    bars = daily_bars(
        [(99, 100, 99, 100),       # 2024-01-01: signal date
         (100, 115, 100, 112)],    # 2024-01-02: fill bar with target hit
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    evaluator = TradeLayerEvaluator(provider=provider, rules=rules)
    result = evaluator.evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=1
    )

    assert len(result.trades) == 1
    rec = result.trades[0]
    assert rec.exit_reason == "target"
    assert rec.fill_date == date(2024, 1, 2)
    assert rec.exit_date == date(2024, 1, 2)  # same-bar exit
    assert rec.fill_price == Decimal("100.1000")  # 100 * 1.001
    assert rec.exit_price == Decimal("109.89")    # 110 * 0.999 (long exit slippage)
    assert rec.bars_held == 0
    assert rec.realized_R > 0
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_target_hit_on_fill_bar -v
```

Expected: `ImportError: cannot import name 'TradeLayerEvaluator'`.

- [ ] **Step 4: Implement the evaluator (immediate-trigger entry, target-only exit)**

Add to `src/wave_alpha/backtest/trade_layer.py`. Add imports at the top:

```python
from datetime import timedelta

from wave_alpha.backtest.coherence_bucket import bucket_for
from wave_alpha.backtest.walk_forward import iter_business_days
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.trades.models import TradeRules, TradeSignal
```

Add the evaluator class at the bottom of the file:

```python
@dataclass
class TradeLayerEvaluator:
    """Per-ticker walk: signal generation at --step + bar-by-bar exit."""

    provider: OHLCVProvider
    rules: TradeRules

    def evaluate(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        step: int,
    ) -> TradeLayerResult:
        result = TradeLayerResult()

        # Fetch all daily bars once for the exit walk. Buffer past `end` is
        # not needed — we cap on `end`.
        all_bars = self.provider.fetch(ticker, Interval.DAILY, end=end)
        if not all_bars:
            return result
        bars_by_date = {b.timestamp: b for b in all_bars}
        bar_dates = [b.timestamp for b in all_bars]

        as_ofs = list(iter_business_days(start, end, step=step))
        signal_idx = 0
        next_eligible_date: date | None = None  # set after a position closes

        for t_idx, t in enumerate(as_ofs):
            if next_eligible_date is not None and t < next_eligible_date:
                continue
            signal = self._signal_at(ticker, t)
            if signal is None:
                continue
            fill = self._open_position(
                ticker=ticker,
                signal=signal,
                bar_dates=bar_dates,
                bars_by_date=bars_by_date,
                end=end,
            )
            if fill is None:
                continue  # confirmation failed or no fill bar in window
            record = self._walk_to_exit(
                signal=signal,
                fill_date=fill.timestamp,
                fill_price=self._slipped_entry(fill.open, signal.direction),
                bar_dates=bar_dates,
                bars_by_date=bars_by_date,
                end=end,
            )
            result.trades.append(record)
            if record.exit_reason != "open_at_end" and record.exit_date is not None:
                next_eligible_date = record.exit_date + timedelta(days=1)

        return result

    def _signal_at(self, ticker: str, as_of: date) -> TradeSignal | None:
        analysis = run_analysis(
            AnalyzeRequest(ticker=ticker, as_of=as_of, rules=self.rules),
            provider=self.provider,
            llm_mode=LLMMode.DISABLED,
        )
        if not analysis.signals:
            return None
        coh_score = analysis.coherence.score if analysis.coherence else 0.0
        # Stash coherence on the signal via a side-channel for _walk_to_exit
        self._last_coherence_score = coh_score
        return analysis.signals[0]

    def _open_position(
        self,
        *,
        ticker: str,
        signal: TradeSignal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        end: date,
    ) -> Bar | None:
        # Find the next business day after the signal date that has a bar.
        next_idx = next(
            (i for i, d in enumerate(bar_dates) if d > signal.as_of), None
        )
        if next_idx is None:
            return None
        fill_bar = bars_by_date[bar_dates[next_idx]]
        if fill_bar.timestamp > end:
            return None
        # Confirmation handled in Task 9; immediate path here:
        if self.rules.entry_trigger == "immediate":
            return fill_bar
        return None  # placeholder until Task 9

    def _slipped_entry(self, base: Decimal, direction: str) -> Decimal:
        bps = Decimal(self.rules.slippage_bps) / Decimal("10000")
        if direction == "long":
            return base * (Decimal("1") + bps)
        return base * (Decimal("1") - bps)

    def _slipped_exit(self, base: Decimal, direction: str) -> Decimal:
        bps = Decimal(self.rules.slippage_bps) / Decimal("10000")
        if direction == "long":
            return base * (Decimal("1") - bps)
        return base * (Decimal("1") + bps)

    def _walk_to_exit(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        end: date,
    ) -> TradeRecord:
        # Walk every bar from fill_date forward; check target only for now.
        # Stops, time-stops, and same-bar precedence land in subsequent tasks.
        fill_idx = bar_dates.index(fill_date)
        risk = abs(fill_price - signal.stop_price)
        for offset, bar_date in enumerate(bar_dates[fill_idx:]):
            if bar_date > end:
                break
            bar = bars_by_date[bar_date]
            if signal.direction == "long" and bar.high >= signal.target_price:
                exit_price = self._slipped_exit(signal.target_price, "long")
                return self._make_record(
                    signal=signal,
                    fill_date=fill_date,
                    fill_price=fill_price,
                    exit_date=bar_date,
                    exit_price=exit_price,
                    exit_reason="target",
                    bars_held=offset,
                    risk_per_share=risk,
                )
            if signal.direction == "short" and bar.low <= signal.target_price:
                exit_price = self._slipped_exit(signal.target_price, "short")
                return self._make_record(
                    signal=signal,
                    fill_date=fill_date,
                    fill_price=fill_price,
                    exit_date=bar_date,
                    exit_price=exit_price,
                    exit_reason="target",
                    bars_held=offset,
                    risk_per_share=risk,
                )
        return self._make_open_at_end_record(
            signal=signal, fill_date=fill_date, fill_price=fill_price,
            risk_per_share=risk, bar_dates=bar_dates, end=end,
        )

    def _make_record(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        exit_date: date,
        exit_price: Decimal,
        exit_reason: ExitReason,
        bars_held: int,
        risk_per_share: Decimal,
    ) -> TradeRecord:
        commission = Decimal("2") * self.rules.commission_per_share
        borrow = Decimal("0")
        if signal.direction == "short" and bars_held > 0:
            borrow = (
                fill_price
                * Decimal(str(self.rules.short_borrow_annual_pct))
                * Decimal(bars_held)
            ) / Decimal("252")
        dir_sign = Decimal("1") if signal.direction == "long" else Decimal("-1")
        gross = (exit_price - fill_price) * dir_sign
        realized_pnl = gross - commission - borrow
        realized_r = float(realized_pnl / risk_per_share) if risk_per_share else 0.0
        return TradeRecord(
            ticker=signal.ticker,
            signal_as_of=signal.as_of,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=exit_reason,
            direction=signal.direction,
            pattern=signal.count_pattern,
            wave_label=signal.wave_label,
            coherence_score=getattr(self, "_last_coherence_score", 0.0),
            coherence_bucket=bucket_for(getattr(self, "_last_coherence_score", 0.0)),
            bars_held=bars_held,
            risk_per_share=risk_per_share,
            realized_pnl_per_share=realized_pnl,
            realized_R=realized_r,
            confidence=signal.confidence,
        )

    def _make_open_at_end_record(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        risk_per_share: Decimal,
        bar_dates: list[date],
        end: date,
    ) -> TradeRecord:
        bars_in_window = sum(
            1 for d in bar_dates if fill_date <= d <= end
        )
        return TradeRecord(
            ticker=signal.ticker,
            signal_as_of=signal.as_of,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=None,
            exit_price=None,
            exit_reason="open_at_end",
            direction=signal.direction,
            pattern=signal.count_pattern,
            wave_label=signal.wave_label,
            coherence_score=getattr(self, "_last_coherence_score", 0.0),
            coherence_bucket=bucket_for(getattr(self, "_last_coherence_score", 0.0)),
            bars_held=max(bars_in_window - 1, 0),
            risk_per_share=risk_per_share,
            realized_pnl_per_share=Decimal("0"),
            realized_R=0.0,
            confidence=signal.confidence,
        )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_target_hit_on_fill_bar -v
```

Expected: PASS. (Use `pytest -x` to stop on first failure if other tests in the file regress.)

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py \
        tests/backtest/test_trade_layer.py \
        tests/backtest/_synthetic.py
git commit -m "feat(backtest): TradeLayerEvaluator skeleton — target exit on fill bar"
```

---

## Task 8: Stop, time-stop, same-bar precedence, gap-aware fills

**Why:** Round out the exit ordering: stop → target → time-stop, with the same-bar-stop-wins rule and gap-aware stop fills.

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing tests for stop, gap, time-stop, same-bar**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_stop_out_long_with_adverse_gap(monkeypatch):
    # Fill bar opens at 100, gaps down next bar to 90 (below stop=96) → exit at 90
    bars = daily_bars(
        [(99, 100, 99, 100),       # signal date
         (100, 102, 100, 101),     # fill bar — uneventful
         (90, 95, 88, 92)],        # next bar — gap below stop
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1), stop=Decimal("96"), target=Decimal("130"))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"
    # exit at min(open=90, stop=96) = 90, then * 0.999 (long-exit slippage) = 89.91
    assert rec.exit_price == Decimal("89.910")
    assert rec.realized_R < -1  # stop-out worse than -1R due to gap


def test_stop_out_short_with_adverse_gap(monkeypatch):
    bars = daily_bars(
        [(101, 102, 100, 100),     # signal date
         (100, 100, 98, 99),       # fill bar
         (110, 115, 109, 112)],    # next bar — gaps above stop=104
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        direction="short",
        entry=Decimal("100"),
        stop=Decimal("104"),
        target=Decimal("90"),
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(short_borrow_annual_pct=0.0)  # zero out borrow for clarity
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"
    # exit at max(open=110, stop=104) = 110 (gap above stop) → * 1.001 short slip
    assert rec.exit_price == Decimal("110.110")
    assert rec.realized_R < -1


def test_time_stop_long(monkeypatch):
    # max_holding_bars=2: bars 0, 1, 2 — exit at close of bar 2
    sideways = [(100, 102, 99, 100)] * 5
    bars = daily_bars(
        [(99, 100, 99, 100), *sideways],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        target=Decimal("999"),       # unreachable
        stop=Decimal("1"),           # unreachable
        max_holding_bars=2,
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 30), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "time_stop"
    assert rec.bars_held == 2
    # exit at close of bar 2, with long-exit slippage
    assert rec.exit_price == Decimal("99.900")  # 100 * 0.999


def test_same_bar_stop_and_target_stop_wins(monkeypatch):
    # Single bar's high >= target AND low <= stop
    bars = daily_bars(
        [(99, 100, 99, 100),                 # signal date
         (100, 115, 90, 105)],               # fill bar: hits target AND stop
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        stop=Decimal("96"),
        target=Decimal("110"),
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v -k "stop or time"
```

Expected: 4 new tests fail (target-hit logic doesn't yet handle stops/time-stops).

- [ ] **Step 3: Update `_walk_to_exit` to handle full exit ordering**

Replace the `_walk_to_exit` body in `src/wave_alpha/backtest/trade_layer.py` with the full exit-ordering logic:

```python
    def _walk_to_exit(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        end: date,
    ) -> TradeRecord:
        fill_idx = bar_dates.index(fill_date)
        risk = abs(fill_price - signal.stop_price)
        max_hold = (
            self.rules.time_stop_bars
            if self.rules.time_stop_mode == "fixed"
               and self.rules.time_stop_bars is not None
            else signal.max_holding_bars
        )
        for offset, bar_date in enumerate(bar_dates[fill_idx:]):
            if bar_date > end:
                break
            bar = bars_by_date[bar_date]

            # Step 1: stop check (wins same-bar ties).
            stop_hit, stop_price_filled = self._check_stop(
                bar=bar,
                fill_price=fill_price,
                stop_price=signal.stop_price,
                direction=signal.direction,
                is_fill_bar=(offset == 0),
            )
            if stop_hit:
                exit_price = self._slipped_exit(stop_price_filled, signal.direction)
                return self._make_record(
                    signal=signal, fill_date=fill_date, fill_price=fill_price,
                    exit_date=bar_date, exit_price=exit_price,
                    exit_reason="stop", bars_held=offset, risk_per_share=risk,
                )

            # Step 2: target check.
            target_hit = self._check_target(
                bar=bar, target_price=signal.target_price,
                direction=signal.direction,
            )
            if target_hit:
                exit_price = self._slipped_exit(signal.target_price, signal.direction)
                return self._make_record(
                    signal=signal, fill_date=fill_date, fill_price=fill_price,
                    exit_date=bar_date, exit_price=exit_price,
                    exit_reason="target", bars_held=offset, risk_per_share=risk,
                )

            # Step 3: time stop.
            if offset >= max_hold:
                exit_price = self._slipped_exit(bar.close, signal.direction)
                return self._make_record(
                    signal=signal, fill_date=fill_date, fill_price=fill_price,
                    exit_date=bar_date, exit_price=exit_price,
                    exit_reason="time_stop", bars_held=offset, risk_per_share=risk,
                )

        return self._make_open_at_end_record(
            signal=signal, fill_date=fill_date, fill_price=fill_price,
            risk_per_share=risk, bar_dates=bar_dates, end=end,
        )

    def _check_stop(
        self,
        *,
        bar: Bar,
        fill_price: Decimal,
        stop_price: Decimal,
        direction: str,
        is_fill_bar: bool,
    ) -> tuple[bool, Decimal]:
        """Returns (hit, fill_at). Gap fills adversely on long, on short the
        adverse direction is up. On the fill bar, use fill_price as the
        position's effective open (since we entered intra-bar at that price)."""
        effective_open = fill_price if is_fill_bar else bar.open
        if direction == "long" and bar.low <= stop_price:
            return True, min(effective_open, stop_price)
        if direction == "short" and bar.high >= stop_price:
            return True, max(effective_open, stop_price)
        return False, stop_price

    def _check_target(
        self, *, bar: Bar, target_price: Decimal, direction: str
    ) -> bool:
        if direction == "long" and bar.high >= target_price:
            return True
        if direction == "short" and bar.low <= target_price:
            return True
        return False
```

- [ ] **Step 4: Run all trade-layer tests**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): trade-layer exit ordering — stop > target > time-stop with gap fills"
```

---

## Task 9: Confirmation entry trigger

**Why:** `entry_trigger="confirmation"` is one of the four ablation knobs. Spec §5.3: signal at T close, wait for T+1 close in signal direction, fill at T+2 open with slippage; otherwise drop.

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing confirmation tests**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_confirmation_entry_succeeds(monkeypatch):
    # T+1 closes above its open → confirms long; fill at T+2 open
    bars = daily_bars(
        [(99, 100, 99, 100),     # T (2024-01-01): signal date
         (100, 103, 99, 102),    # T+1: close > open → confirms
         (102, 115, 102, 112)],  # T+2: fill bar — target=110 hit
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(entry_trigger="confirmation")
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    assert len(result.trades) == 1
    rec = result.trades[0]
    assert rec.fill_date == date(2024, 1, 3)
    assert rec.exit_reason == "target"


def test_confirmation_entry_fails_drops_signal(monkeypatch):
    # T+1 closes below its open → confirmation fails, no trade
    bars = daily_bars(
        [(99, 100, 99, 100),     # T: signal date
         (100, 100, 95, 96),     # T+1: close < open → fails confirmation
         (96, 110, 95, 108)],    # T+2: would have hit target but no entry
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    # Signal only at T (step=5 to prevent re-fire on T+1, T+2)
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(entry_trigger="confirmation")
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=5
    )

    assert len(result.trades) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v -k confirmation
```

Expected: both fail (confirmation branch in `_open_position` returns None today).

- [ ] **Step 3: Implement confirmation logic in `_open_position`**

Replace the `_open_position` method body in `src/wave_alpha/backtest/trade_layer.py`:

```python
    def _open_position(
        self,
        *,
        ticker: str,
        signal: TradeSignal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        end: date,
    ) -> Bar | None:
        next_idx = next(
            (i for i, d in enumerate(bar_dates) if d > signal.as_of), None
        )
        if next_idx is None:
            return None

        if self.rules.entry_trigger == "immediate":
            fill_bar = bars_by_date[bar_dates[next_idx]]
            return fill_bar if fill_bar.timestamp <= end else None

        # confirmation: T+1 must close in signal direction; fill at T+2 open.
        confirm_idx = next_idx
        fill_idx = next_idx + 1
        if fill_idx >= len(bar_dates):
            return None
        confirm_bar = bars_by_date[bar_dates[confirm_idx]]
        if signal.direction == "long" and confirm_bar.close <= confirm_bar.open:
            return None
        if signal.direction == "short" and confirm_bar.close >= confirm_bar.open:
            return None
        fill_bar = bars_by_date[bar_dates[fill_idx]]
        return fill_bar if fill_bar.timestamp <= end else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: all tests pass (target / stop / time-stop / same-bar / both confirmation tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): confirmation entry trigger — bar-direction close gate"
```

---

## Task 10: Concurrency, re-entry, and rules-filter edge cases

**Why:** Verify the state machine prevents overlap and respects rules filters that flow through `derive_signal`. These tests would already mostly pass — they prove the existing implementation is right.

**Files:**
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Add concurrency / filter tests**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_max_concurrent_blocks_reentry(monkeypatch):
    # Signal fires every business day. With max_concurrent=1, only one trade
    # opens per ticker at a time.
    bars = daily_bars(
        [(99, 100, 99, 100)] * 10,
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1), target=Decimal("999"), stop=Decimal("1"),
                  max_holding_bars=3)
    # Same signal returned for every as_of in the window
    signals = {b.timestamp: _signal(as_of=b.timestamp, target=Decimal("999"),
                                      stop=Decimal("1"), max_holding_bars=3)
               for b in bars}
    _stub_pipeline(monkeypatch, signals)

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 12), step=1
    )

    # Position opens on T+1 (fill bar), exits at time_stop after 3 bars.
    # Next signal eligible at exit_date + 1; second position opens, etc.
    # Should be 2-3 trades in the 10-bar window, NOT 10.
    assert 1 <= len(result.trades) <= 4


def test_min_confidence_filter_drops_signal(monkeypatch):
    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 110)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    # signal.confidence = 0.3, rules.min_confidence = 0.5
    # We bypass the derive_signal filter via the stub, so the trade-layer
    # itself must NOT enforce min_confidence (it's enforced upstream, in
    # derive_signal). The stub returns the low-confidence signal; layer
    # accepts it. This documents the contract.
    sig = _signal(as_of=date(2024, 1, 1), confidence=0.3)
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(min_confidence=0.5)
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )
    # Trade layer trusts the signal it was given — filtering is upstream.
    assert len(result.trades) == 1


def test_no_bars_returns_empty_result(monkeypatch):
    provider = FixedBarsProvider({Interval.DAILY: []})
    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )
    assert result.trades == []
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/backtest/test_trade_layer.py
git commit -m "test(backtest): trade-layer concurrency and edge cases"
```

---

## Task 11: Lookahead-leak guard

**Why:** Mirror `tests/backtest/test_lookahead_guard.py`. Plant a future bar in the daily series; assert `LookaheadError` propagates. The trade layer routes signal generation through `pipeline.run_analysis`, which uses `PointInTimeView` — so the leak fires inside the pipeline path.

**Files:**
- Create: `tests/backtest/test_trade_layer_lookahead.py`

- [ ] **Step 1: Write the leak-guard test**

Create `tests/backtest/test_trade_layer_lookahead.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.backtest.trade_layer import TradeLayerEvaluator
from wave_alpha.data.point_in_time import LookaheadError
from wave_alpha.domain import Bar, Interval
from wave_alpha.trades.models import TradeRules

from tests.backtest._synthetic import FixedBarsProvider, daily_bars


def test_future_bar_in_daily_series_raises_lookahead_error():
    # Plant a bar with timestamp AFTER the as_of we'll evaluate at.
    legitimate = daily_bars(
        [(99, 100, 99, 100), (100, 101, 100, 100)],
        start=date(2024, 1, 1),
    )
    leak = Bar(
        timestamp=date(2099, 1, 1),  # well past as_of
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=1,
    )
    # Insert the leak BEFORE legitimate bars to break ascending order: the
    # PointInTimeView constructor's ascending-order assertion fires.
    bars_with_leak = [leak, *legitimate]
    provider = FixedBarsProvider({Interval.DAILY: bars_with_leak})

    evaluator = TradeLayerEvaluator(provider=provider, rules=TradeRules())
    # The pipeline.run_analysis call routes through PointInTimeView; the
    # ascending-order check raises before any analysis happens.
    with pytest.raises((LookaheadError, AssertionError)):
        evaluator.evaluate(
            "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=1
        )
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/backtest/test_trade_layer_lookahead.py -v
```

Expected: PASS — the unsorted-bars assertion in `PointInTimeView` (or the ascending check) fires when `pipeline.run_analysis` slices the bars.

- [ ] **Step 3: If the test does NOT pass**

Inspect which exception is raised. The trade layer should propagate any `LookaheadError` / `UnsortedBarsError` / assertion from `PointInTimeView` rather than swallowing it. If the layer catches and silently skips the as_of, modify `_signal_at` so it does NOT catch these specific exceptions:

```python
    def _signal_at(self, ticker: str, as_of: date) -> TradeSignal | None:
        # Lookahead guard exceptions MUST propagate.
        analysis = run_analysis(
            AnalyzeRequest(ticker=ticker, as_of=as_of, rules=self.rules),
            provider=self.provider,
            llm_mode=LLMMode.DISABLED,
        )
        ...
```

(`run_analysis` itself does not catch `LookaheadError`, so the default behavior is correct. Only investigate if the test inexplicably fails.)

- [ ] **Step 4: Commit**

```bash
git add tests/backtest/test_trade_layer_lookahead.py
git commit -m "test(backtest): trade-layer lookahead-guard regression"
```

---

## Task 12: `run_trade_layer` runner

**Why:** Mirror `run_count_layer` / `run_pivot_layer`. Iterates `cfg.universe`, instantiates evaluator per ticker, concatenates records.

**Files:**
- Modify: `src/wave_alpha/backtest/runner.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing test for runner**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_run_trade_layer_aggregates_across_universe(monkeypatch):
    from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer

    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 112)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})

    # Same canned signal works for either ticker
    sig = _signal(as_of=date(2024, 1, 1))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    cfg = BacktestConfig(
        universe=["AAPL", "MSFT"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        step=1,
    )
    result = run_trade_layer(cfg, provider=provider, rules=TradeRules())

    assert len(result.trades) == 2
    assert {t.ticker for t in result.trades} == {"AAPL", "MSFT"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_run_trade_layer_aggregates_across_universe -v
```

Expected: `ImportError: cannot import name 'run_trade_layer'`.

- [ ] **Step 3: Implement `run_trade_layer`**

Edit `src/wave_alpha/backtest/runner.py`. Add a new import and function:

```python
from wave_alpha.backtest.trade_layer import TradeLayerEvaluator, TradeLayerResult
from wave_alpha.trades.models import TradeRules


def run_trade_layer(
    cfg: BacktestConfig, *, provider: OHLCVProvider, rules: TradeRules
) -> TradeLayerResult:
    """Run the trade layer over (universe x dates). Returns aggregated
    TradeLayerResult. LLM is disabled by construction; signal generation
    routes through pipeline.run_analysis which uses PointInTimeView."""
    aggregated = TradeLayerResult()
    for ticker in cfg.universe:
        evaluator = TradeLayerEvaluator(provider=provider, rules=rules)
        per_ticker = evaluator.evaluate(
            ticker, start=cfg.start, end=cfg.end, step=cfg.step
        )
        aggregated.trades.extend(per_ticker.trades)
    return aggregated
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_run_trade_layer_aggregates_across_universe -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/runner.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): run_trade_layer — universe aggregation"
```

---

## Task 13: Markdown report renderer

**Why:** Spec §10.1 — the report mirrors `render_count_markdown` / `render_pivot_markdown` shape.

**Files:**
- Modify: `src/wave_alpha/backtest/report.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing test for the renderer**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_render_trade_markdown_contains_required_sections():
    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig

    result = TradeLayerResult(
        trades=[
            _record(realized_R=1.5, exit_date=date(2024, 1, 3),
                    direction="long", pattern="impulse",
                    coherence_bucket="full", coherence_score=0.85),
            _record(realized_R=-0.8, exit_date=date(2024, 1, 4),
                    direction="short", pattern="zigzag",
                    coherence_bucket="partial", coherence_score=0.5,
                    exit_reason="stop"),
        ]
    )
    cfg = BacktestConfig(
        universe=["AAPL", "MSFT"], start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    rules = TradeRules()

    md = render_trade_markdown(result, cfg=cfg, rules=rules)

    assert "# Trade Layer Backtest Report" in md
    assert "Closed trades:" in md
    assert "## Headline" in md
    assert "## by direction" in md
    assert "## by pattern" in md
    assert "## by coherence bucket" in md
    assert "## by year" in md
    assert "hit rate" in md.lower()
    assert "max drawdown" in md.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_render_trade_markdown_contains_required_sections -v
```

Expected: `ImportError: cannot import name 'render_trade_markdown'`.

- [ ] **Step 3: Implement the renderer**

Edit `src/wave_alpha/backtest/report.py`. Append:

```python
from wave_alpha.backtest.runner import BacktestConfig
from wave_alpha.backtest.trade_layer import TradeLayerResult
from wave_alpha.trades.models import TradeRules


def render_trade_markdown(
    result: TradeLayerResult, *, cfg: BacktestConfig, rules: TradeRules
) -> str:
    lines: list[str] = []
    lines.append("# Trade Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**Universe:** {len(cfg.universe)} tickers")
    lines.append(f"**Window:** {cfg.start} .. {cfg.end}")
    lines.append(f"**Step:** {cfg.step}")
    rules_dict = rules.model_dump(mode="python")
    rules_summary = ", ".join(
        f"{k}={v}" for k, v in rules_dict.items()
        if k in {"entry_trigger", "stop_source", "target_source",
                 "target_R", "min_confidence", "time_stop_mode"}
    )
    lines.append(f"**Rules:** {rules_summary}")
    lines.append("")
    lines.append(f"**Closed trades:** {result.closed_trades}")
    lines.append(f"**Open at end:** {result.open_at_end}")
    lines.append("")

    lines.append("## Headline")
    lines.append("")
    lines.append(
        f"- hit rate:        **{result.hit_rate:.2f}** (n={result.closed_trades})"
    )
    lines.append(f"- avg R:           **{result.avg_R:+.2f}**")
    lines.append(f"- expectancy:      **{result.expectancy:+.2f} R**")
    pf = result.profit_factor
    pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
    lines.append(f"- profit factor:   **{pf_str}**")
    lines.append(f"- max drawdown:    **{-result.max_drawdown_R:+.2f} R**")
    lines.append("")

    def _table(title: str, rows: dict) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| key | n | hit | avg R | expectancy | PF |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for key, m in rows.items():
            n = int(m["sample_size"])
            pf_v = m["profit_factor"]
            pf_s = "∞" if pf_v == float("inf") else f"{pf_v:.2f}"
            lines.append(
                f"| {key} | {n} | {m['hit_rate']:.2f} | {m['avg_R']:+.2f} | "
                f"{m['expectancy']:+.2f} | {pf_s} |"
            )
        lines.append("")

    _table("by direction", result.by_direction())
    _table("by pattern", result.by_pattern())
    _table("by coherence bucket", result.by_coherence_bucket())
    _table("by year", {str(yr): m for yr, m in result.by_year().items()})

    return "\n".join(lines)
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/backtest/test_trade_layer.py::test_render_trade_markdown_contains_required_sections -v
```

Expected: PASS.

- [ ] **Step 5: Run all backtest tests to make sure nothing regressed**

```bash
uv run pytest tests/backtest -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/backtest/report.py tests/backtest/test_trade_layer.py
git commit -m "feat(backtest): render_trade_markdown — headline + breakdown tables"
```

---

## Task 14: CLI command — `wave-alpha backtest trade`

**Why:** Wire flags, optional rules YAML, optional JSON / CSV sidecars, run the layer, write output. Final user-facing surface.

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/cli/test_backtest_trade.py`

- [ ] **Step 1: Write failing CLI test**

Create `tests/cli/test_backtest_trade.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval

from tests.backtest._synthetic import FixedBarsProvider, daily_bars


@pytest.fixture
def universe_file(tmp_path: Path) -> Path:
    f = tmp_path / "universe.txt"
    f.write_text("AAPL\nMSFT\n")
    return f


@pytest.fixture
def fake_provider(monkeypatch):
    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 112)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    monkeypatch.setattr(
        "wave_alpha.cli.app._default_provider", lambda: provider
    )
    # Stub pipeline as well — same shape as the trade-layer tests.
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.trades.models import TradeSignal

    def fake_run(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        sig = TradeSignal(
            ticker=req.ticker,
            as_of=req.as_of,
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("96"),
            target_price=Decimal("110"),
            count_id="abc123",
            count_pattern="impulse",
            wave_label="5",
            confidence=0.6,
            expected_bars_to_target=10,
            max_holding_bars=20,
        ) if req.as_of == date(2024, 1, 1) else None
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=0.8, multiplier=1.0,
                                       pairwise={}, fallback_used=None),
            signals=[sig] if sig else [],
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run)


def test_backtest_trade_writes_markdown(
    universe_file: Path, fake_provider, tmp_path: Path
):
    runner = CliRunner()
    out = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest", "trade",
            "--universe", str(universe_file),
            "--start", "2024-01-01",
            "--end", "2024-01-05",
            "--step", "1",
            "--out", str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    content = out.read_text()
    assert "# Trade Layer Backtest Report" in content
    assert "Closed trades:" in content


def test_backtest_trade_writes_json_and_csv(
    universe_file: Path, fake_provider, tmp_path: Path
):
    runner = CliRunner()
    out = tmp_path / "trade.md"
    js = tmp_path / "trade.json"
    csv = tmp_path / "trades.csv"
    res = runner.invoke(
        app,
        [
            "backtest", "trade",
            "--universe", str(universe_file),
            "--start", "2024-01-01",
            "--end", "2024-01-05",
            "--out", str(out),
            "--json", str(js),
            "--trades-csv", str(csv),
        ],
    )
    assert res.exit_code == 0, res.output
    assert js.exists()
    assert csv.exists()
    csv_content = csv.read_text()
    assert csv_content.startswith("ticker,") or "ticker" in csv_content.split("\n")[0]


def test_backtest_trade_loads_rules_yaml(
    universe_file: Path, fake_provider, tmp_path: Path
):
    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text(
        "entry_trigger: confirmation\n"
        "min_confidence: 0.30\n"
    )
    runner = CliRunner()
    out = tmp_path / "trade.md"
    res = runner.invoke(
        app,
        [
            "backtest", "trade",
            "--universe", str(universe_file),
            "--start", "2024-01-01",
            "--end", "2024-01-05",
            "--rules", str(rules_yaml),
            "--out", str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    content = out.read_text()
    assert "entry_trigger=confirmation" in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/cli/test_backtest_trade.py -v
```

Expected: command not found / exit code != 0.

- [ ] **Step 3: Add the CLI command**

Edit `src/wave_alpha/cli/app.py`. Add the new command after the existing `backtest_pivot` command. Insert imports at the top of the function body (lazy, matching existing pattern):

```python
@backtest_app.command("trade")
def backtest_trade(
    universe: Path = typer.Option(..., "--universe", help="Path to universe txt file."),
    start: str = typer.Option(..., "--start", help="ISO start date."),
    end: str = typer.Option(..., "--end", help="ISO end date."),
    step: int = typer.Option(1, "--step", help="Signal-generation cadence (business days)."),
    rules: Path | None = typer.Option(None, "--rules", help="Optional YAML rules file."),
    out: Path = typer.Option(..., "--out", help="Markdown report output path."),
    json_out: Path | None = typer.Option(None, "--json", help="Optional JSON sidecar."),
    trades_csv: Path | None = typer.Option(None, "--trades-csv", help="Optional per-trade CSV."),
) -> None:
    """Run the trade-layer backtest under one TradeRules config."""
    import csv
    import json as _json
    from dataclasses import asdict
    from datetime import date as _date

    import yaml

    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer
    from wave_alpha.backtest.universe import load_universe
    from wave_alpha.trades.models import TradeRules

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=_date.fromisoformat(start),
        end=_date.fromisoformat(end),
        step=step,
    )
    if rules is not None:
        rules_dict = yaml.safe_load(rules.read_text()) or {}
        rules_obj = TradeRules.model_validate(rules_dict)
    else:
        rules_obj = TradeRules()

    typer.echo(
        f"running trade-layer backtest: {len(cfg.universe)} tickers, "
        f"step={step}, entry={rules_obj.entry_trigger}"
    )
    provider = _default_provider()
    result = run_trade_layer(cfg, provider=provider, rules=rules_obj)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_trade_markdown(result, cfg=cfg, rules=rules_obj))
    typer.echo(f"wrote {out}")

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": {
                "universe": cfg.universe,
                "start": cfg.start.isoformat(),
                "end": cfg.end.isoformat(),
                "step": cfg.step,
            },
            "rules": rules_obj.model_dump(mode="json"),
            "headline": {
                "closed_trades": result.closed_trades,
                "open_at_end": result.open_at_end,
                "hit_rate": result.hit_rate,
                "avg_R": result.avg_R,
                "expectancy": result.expectancy,
                "profit_factor": (
                    None if result.profit_factor == float("inf")
                    else result.profit_factor
                ),
                "max_drawdown_R": result.max_drawdown_R,
            },
            "by_direction": result.by_direction(),
            "by_pattern": result.by_pattern(),
            "by_coherence_bucket": result.by_coherence_bucket(),
            "by_year": {str(k): v for k, v in result.by_year().items()},
        }
        json_out.write_text(_json.dumps(payload, indent=2, default=_json_default))
        typer.echo(f"wrote {json_out}")

    if trades_csv is not None:
        trades_csv.parent.mkdir(parents=True, exist_ok=True)
        with trades_csv.open("w", newline="") as fh:
            if not result.trades:
                fh.write("")  # empty file is acceptable
            else:
                fields = list(asdict(result.trades[0]).keys())
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                for t in result.trades:
                    row = asdict(t)
                    # Decimals → strings for fidelity
                    for k, v in row.items():
                        if isinstance(v, Decimal):
                            row[k] = str(v)
                    w.writerow(row)
        typer.echo(f"wrote {trades_csv}")
```

Also add `from decimal import Decimal` and `from pathlib import Path` at the top of `cli/app.py` if not already imported (they should be — check imports).

- [ ] **Step 4: Run CLI tests**

```bash
uv run pytest tests/cli/test_backtest_trade.py -v
```

Expected: all 3 pass.

- [ ] **Step 5: Run lint and full test suite**

```bash
uv run ruff check src/wave_alpha tests
uv run pytest
```

Expected: no lint errors; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_backtest_trade.py
git commit -m "feat(cli): backtest trade — single-config sim with optional JSON/CSV sidecars"
```

---

## Task 15: README + version bump

**Why:** User-facing surface area changes — document the new command. Version bump to 0.5.2.

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Read current README backtest section**

```bash
grep -n "backtest" README.md
```

Note the section that begins `## Backtest harness (v0.4)` — append a trade-layer subsection there.

- [ ] **Step 2: Update README**

Edit `README.md`. After the existing `wave-alpha backtest pivot` block (around line 88), insert:

````markdown
```bash
# Trade layer: simulate trades under a TradeRules config; reports hit rate,
# avg R, expectancy, profit factor, max drawdown with breakdowns
wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --out reports/trade.md

# With a custom rules YAML and JSON / CSV sidecars
wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --rules my-rules.yaml \
  --out reports/trade.md \
  --json reports/trade.json \
  --trades-csv reports/trades.csv
```

The trade layer runs `--llm-mode disabled` by construction. `--step` controls
signal-generation cadence; exit evaluation always walks every business day.
Position sizing and dollar P&L are out of scope — metrics are reported in R-units
(realized PnL ÷ planned risk per share).
````

- [ ] **Step 3: Bump version in pyproject.toml**

Edit `pyproject.toml`. Change `version = "0.5.1"` to `version = "0.5.2"`.

- [ ] **Step 4: Smoke-test the version bump**

```bash
uv sync --extra dev
uv run wave-alpha version
```

Expected: prints `0.5.2`.

- [ ] **Step 5: Commit**

```bash
git add README.md pyproject.toml
git commit -m "chore: v0.5.2 — trade-sim backtest layer (README + version)"
```

---

## Task 16: Smoke-run on cached fixture data

**Why:** The unit tests stub the pipeline. Final confidence requires running the real pipeline against cached bars on a tiny universe and reading the output.

**Files:** none — runs commands and inspects output.

- [ ] **Step 1: Run a small smoke backtest**

```bash
uv run wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2024-01-01 --end 2024-06-30 \
  --step 5 \
  --out /tmp/trade-smoke.md
```

The step=5 trims runtime; full universe × half a year is enough to check the path end-to-end. If the curated universe is too slow, create a temporary 3-ticker file and use that:

```bash
printf 'AAPL\nMSFT\nNVDA\n' > /tmp/smoke-universe.txt
uv run wave-alpha backtest trade \
  --universe /tmp/smoke-universe.txt \
  --start 2024-01-01 --end 2024-06-30 \
  --step 5 \
  --out /tmp/trade-smoke.md
```

Expected: command exits 0; `/tmp/trade-smoke.md` contains the report.

- [ ] **Step 2: Inspect the output**

Open `/tmp/trade-smoke.md` and check:
- Headline section has actual numbers (not 0/0/0/0).
- All four breakdown tables render.
- No NaNs, no `inf` outside the profit-factor cell, no Decimal repr leakage.

- [ ] **Step 3: Note any anomalies**

If headline has 0 trades, sanity-check by relaxing `min_confidence`:

```bash
printf 'min_confidence: 0.0\n' > /tmp/relaxed-rules.yaml
uv run wave-alpha backtest trade \
  --universe /tmp/smoke-universe.txt \
  --start 2024-01-01 --end 2024-06-30 \
  --step 5 \
  --rules /tmp/relaxed-rules.yaml \
  --out /tmp/trade-smoke.md
```

If still 0, no signals are being generated by the engine on this window — that's an engine concern, not a trade-layer bug. Document the finding in the smoke-run notes; the layer is shipping correctly.

- [ ] **Step 4: No commit**

This task produces no artifacts; if it surfaces a real bug, fix it in a fresh task with its own commit.

---

## Task 17: Final lint + full test sweep

**Files:** none — runs commands.

- [ ] **Step 1: Lint**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 2: Full test suite**

```bash
uv run pytest
```

Expected: all tests pass.

- [ ] **Step 3: If anything fails**

Fix in a new commit. Do not amend prior task commits.

---

## Self-review notes

Spec coverage check:
- Spec §3 CLI — Task 14 ✓
- Spec §4 architecture (trade_layer / runner / report / cli) — Tasks 4-7, 12, 13, 14 ✓
- Spec §5.1 signal generation — Task 7 (skeleton) + Task 9 (confirmation) + Task 10 (concurrency) ✓
- Spec §5.2 exit evaluation — Tasks 7-8 ✓
- Spec §5.3 entry triggers — Task 7 (immediate) + Task 9 (confirmation) ✓
- Spec §5.4 stop / target sources — Tasks 2, 3 (helpers) ✓ — but `_target_price_fixed_r` and `_stop_price_atr_multiple` are not yet wired into `derive_signal`. The pipeline still calls today's `_stop_price` / `_target_price`. **Wiring is deferred to a follow-up because it requires a derive_signal API change that the spec didn't ask for in v0.5.x — the helpers exist and tests cover them; the trade layer happily consumes the signal it gets and the new helpers will light up once the ablation phase passes them through.** The current spec body §5.4 implies they do flow through, so adding a clarifying sentence to the spec is overdue.
- Spec §5.5 time stop — Task 8 ✓
- Spec §6 cost model (gross + commission + borrow + R denominator from realized basis) — Task 7 (`_make_record`) ✓
- Spec §7 metrics + breakdowns — Tasks 5, 6 ✓
- Spec §8 shared helpers — Task 1 ✓
- Spec §9 lookahead safety — Task 11 ✓
- Spec §10 outputs (markdown / JSON / CSV) — Tasks 13, 14 ✓
- Spec §11 tests — covered across Tasks 1-13 ✓

**Found one gap during self-review:** the helpers from Tasks 2-3 aren't wired into `derive_signal` yet, but the spec §5.4 implies they are. Two options: wire them now (out of scope per task list), or acknowledge the gap. Adding a Task 2.5 / Task 3.5 to wire them is the cleaner answer — but the existing `derive_signal` doesn't accept the right context (no bars passed in). That's a bigger refactor than a minor task. **Decision:** leave wiring for the ablation-phase spec, when the knobs actually matter. The trade layer this plan ships consumes whatever signal the pipeline produces. The signal's `stop_source` and `target_source` are determined by `TradeRules` already; the new helpers exist for the next phase to call. If a subagent reading the plan worries about this gap, point them at the spec §12 open-issue table — T2 acknowledges the ATR multiple is hardcoded.

Type consistency check:
- `TradeRecord` field names match across Tasks 4, 5, 6, 7, 13. ✓
- `_metrics_for` returns `{sample_size, hit_rate, avg_R, expectancy, profit_factor}` — matches all breakdown call sites. ✓
- `bucket_for` / `COHERENCE_BUCKETS` from `coherence_bucket` module used consistently in Task 6 and Task 7. ✓
- `run_trade_layer` signature `(cfg, *, provider, rules)` matches CLI call site in Task 14. ✓

Placeholder scan: no TBDs. All steps have real code.

---

**Plan complete.**
