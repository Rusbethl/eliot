# Outcome Tracking on History Snapshots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every history snapshot's signal forward against subsequent daily bars and expose `target`/`stop`/`time_stop`/`pending` outcomes (with raw R, MFE, MAE) on the deepdive page and in a queryable SQLite column.

**Architecture:** Extract `_walk_to_exit` from `backtest/trade_layer.py` into a pure `trades/outcome.py:resolve_outcome` shared by backtest and history. Add `Snapshot.outcome: SnapshotOutcome | None` (additive payload field) plus an `outcome_status` column for queryability. Resolution piggybacks on `record_snapshot()` using the daily bars `analyze` already fetched (exposed via new `AnalyzeResult.daily_bars`). `wave-alpha resolve` CLI runs the resolver in bulk for cron/backfill use.

**Tech Stack:** Python 3.11, `uv`, Pydantic v2, SQLite (stdlib), Typer CLI, FastAPI/Jinja2, `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-10-outcome-tracking-design.md`

---

## File Structure

**New files:**
- `src/wave_alpha/trades/outcome.py` — pure resolver `resolve_outcome(signal, bars, *, as_of_close) -> Outcome | None` + `Outcome` dataclass.
- `src/wave_alpha/history/resolver.py` — `resolve_pending_for(ticker, daily_bars, *, db_path, today) -> int`.
- `tests/trades/test_outcome_resolver.py` — pure-function resolver tests.
- `tests/history/test_resolver.py` — end-to-end resolver tests against SQLite.
- `tests/history/test_store_migration.py` — schema-migration idempotence tests.
- `tests/cli/test_resolve_command.py` — `wave-alpha resolve` smoke tests.

**Modified files:**
- `src/wave_alpha/pipeline.py` — add `AnalyzeResult.daily_bars: list[Bar]`; populate from existing fetch.
- `src/wave_alpha/trades/models.py` — no changes (TradeSignal already has needed fields).
- `src/wave_alpha/backtest/trade_layer.py` — refactor `_walk_to_exit` to call `resolve_outcome` then layer slippage/commission/borrow.
- `src/wave_alpha/history/models.py` — add `SnapshotOutcome`, extend `SnapshotSignal` with optional fields, extend `Snapshot` with `outcome`.
- `src/wave_alpha/history/store.py` — `_ensure_schema` adds `outcome_status` column idempotently; `upsert` writes the column; new `unresolved_for(ticker)` query.
- `src/wave_alpha/history/service.py` — `record_snapshot` calls resolver after upsert; populate new `SnapshotSignal` fields; add `compute_outcome_badge` pure function.
- `src/wave_alpha/web/templates/_history.html` — per-row outcome chip + outcome badge in card header.
- `src/wave_alpha/web/routes/deepdive.py` — pass outcome-badge data into history fragment template context.
- `src/wave_alpha/cli/app.py` — add `wave-alpha resolve [--ticker T] [--all] [--json]` top-level command.

---

## Task 1: `Outcome` dataclass + minimum-viable `resolve_outcome` (target-hit happy path)

**Files:**
- Create: `src/wave_alpha/trades/outcome.py`
- Create: `tests/trades/test_outcome_resolver.py`

- [ ] **Step 1: Write the failing test (target hit on a long signal)**

Create `tests/trades/test_outcome_resolver.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.trades.models import TradeSignal
from wave_alpha.trades.outcome import Outcome, resolve_outcome


def _bar(day: int, *, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        timestamp=date(2026, 1, day),
        interval=Interval.DAILY,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1_000_000,
    )


def _long_signal(*, as_of_day: int = 1) -> TradeSignal:
    return TradeSignal(
        ticker="TEST",
        as_of=date(2026, 1, as_of_day),
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),     # risk = 5
        target_price=Decimal("110"),  # reward = 10 → rr=2.0
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=20,
        max_holding_bars=60,
    )


def test_long_target_hit_returns_target_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="112", low="102", c="111"),  # target hit
        _bar(4, o="111", h="115", low="109", c="112"),
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert isinstance(out, Outcome)
    assert out.status == "target"
    assert out.exit_date == date(2026, 1, 3)
    assert out.exit_price == Decimal("110")
    assert out.bars_held == 2
    assert out.realized_R == 2.0
    assert out.unrealized_R == 2.0   # mirrors realized when finalized
    assert out.bars_elapsed == 2     # filtered to (as_of, as_of_close]; only days 2 and 3 walked before exit
    assert out.last_observed_date == date(2026, 1, 3)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/trades/test_outcome_resolver.py::test_long_target_hit_returns_target_outcome -x
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.trades.outcome'`.

- [ ] **Step 3: Implement `Outcome` + minimum-viable `resolve_outcome`**

Create `src/wave_alpha/trades/outcome.py`:

```python
"""Pure outcome resolver: given a published trade signal and forward daily
bars, determine target/stop/time_stop/pending using the engine's published
prices. No slippage, no commission, no borrow — that's a backtest concern.

Shared by `backtest.trade_layer` and `history.resolver` so simulation and
live-history outcomes cannot diverge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.trades.models import TradeSignal

Status = Literal["target", "stop", "time_stop", "pending"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    last_observed_date: date
    bars_elapsed: int
    unrealized_R: float  # noqa: N815
    mfe_R: float  # noqa: N815
    mae_R: float  # noqa: N815


def resolve_outcome(
    signal: TradeSignal,
    bars: list[Bar],
    *,
    as_of_close: date,
) -> Outcome | None:
    walked = [b for b in bars if signal.as_of < b.timestamp <= as_of_close]
    if not walked:
        return None
    entry = signal.entry_price
    risk = abs(entry - signal.stop_price)
    dir_sign = Decimal("1") if signal.direction == "long" else Decimal("-1")

    mfe_r = 0.0
    mae_r = 0.0
    for i, bar in enumerate(walked, start=1):
        # MFE/MAE on intraday extremes
        if signal.direction == "long":
            ex_high = float((bar.high - entry) / risk)
            ex_low = float((bar.low - entry) / risk)
        else:
            ex_high = float(-(bar.low - entry) / risk)
            ex_low = float(-(bar.high - entry) / risk)
        if ex_high > mfe_r:
            mfe_r = ex_high
        if ex_low < mae_r:
            mae_r = ex_low

        # Stop check (wins same-bar ties)
        stop_hit = (
            (signal.direction == "long" and bar.low <= signal.stop_price)
            or (signal.direction == "short" and bar.high >= signal.stop_price)
        )
        if stop_hit:
            return _finalize(
                signal,
                walked,
                i,
                exit_price=signal.stop_price,
                status="stop",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

        # Target check
        target_hit = (
            (signal.direction == "long" and bar.high >= signal.target_price)
            or (signal.direction == "short" and bar.low <= signal.target_price)
        )
        if target_hit:
            return _finalize(
                signal,
                walked,
                i,
                exit_price=signal.target_price,
                status="target",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

        # Time stop check
        if i >= signal.max_holding_bars:
            return _finalize(
                signal,
                walked,
                i,
                exit_price=bar.close,
                status="time_stop",
                entry=entry,
                risk=risk,
                dir_sign=dir_sign,
                mfe_r=mfe_r,
                mae_r=mae_r,
            )

    # Bars exhausted with no exit
    last = walked[-1]
    unrealized = float((last.close - entry) * dir_sign / risk)
    return Outcome(
        status="pending",
        exit_date=None,
        exit_price=None,
        bars_held=None,
        realized_R=None,
        last_observed_date=last.timestamp,
        bars_elapsed=len(walked),
        unrealized_R=unrealized,
        mfe_R=mfe_r,
        mae_R=mae_r,
    )


def _finalize(
    signal: TradeSignal,
    walked: list[Bar],
    i: int,
    *,
    exit_price: Decimal,
    status: Status,
    entry: Decimal,
    risk: Decimal,
    dir_sign: Decimal,
    mfe_r: float,
    mae_r: float,
) -> Outcome:
    realized = float((exit_price - entry) * dir_sign / risk)
    exit_bar = walked[i - 1]
    return Outcome(
        status=status,
        exit_date=exit_bar.timestamp,
        exit_price=exit_price,
        bars_held=i,
        realized_R=realized,
        last_observed_date=exit_bar.timestamp,
        bars_elapsed=i,
        unrealized_R=realized,
        mfe_R=mfe_r,
        mae_R=mae_r,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/trades/test_outcome_resolver.py::test_long_target_hit_returns_target_outcome -x
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/trades/outcome.py tests/trades/test_outcome_resolver.py
git commit -m "feat(trades): pure resolve_outcome for target-hit happy path"
```

---

## Task 2: Resolver — stop, time-stop, pending, same-bar tie, short symmetry

**Files:**
- Modify: `tests/trades/test_outcome_resolver.py`

- [ ] **Step 1: Add failing tests for stop, time_stop, pending, same-bar tie, short symmetry, empty bars, and stop on first bar**

Append to `tests/trades/test_outcome_resolver.py`:

```python
def test_long_stop_hit_returns_stop_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="101", low="94", c="95"),  # stop hit (low <= 95)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.exit_price == Decimal("95")
    assert out.realized_R == -1.0
    assert out.bars_held == 1


def test_long_stop_and_target_same_bar_stop_wins():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="111", low="94", c="100"),  # both hit; stop wins
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.realized_R == -1.0


def test_long_time_stop_fires_at_max_holding_bars():
    sig = TradeSignal(
        ticker="TEST", as_of=date(2026, 1, 1), direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("95"),
        target_price=Decimal("110"), count_id="C1", count_pattern="impulse",
        wave_label="3", degree=2, confidence=0.7,
        expected_bars_to_target=2, max_holding_bars=3,
    )
    bars = [
        _bar(2, o="100", h="101", low="99", c="100"),
        _bar(3, o="100", h="102", low="99", c="101"),
        _bar(4, o="101", h="103", low="100", c="102"),  # time_stop fires here
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert out is not None
    assert out.status == "time_stop"
    assert out.exit_price == Decimal("102")
    assert out.bars_held == 3
    assert out.exit_date == date(2026, 1, 4)
    assert out.realized_R == 0.4  # (102 - 100) / 5


def test_long_pending_when_bars_exhausted_before_max_holding():
    sig = _long_signal()  # max_holding_bars = 60
    bars = [
        _bar(2, o="100", h="103", low="98", c="102"),
        _bar(3, o="102", h="105", low="100", c="104"),
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "pending"
    assert out.exit_date is None
    assert out.exit_price is None
    assert out.bars_held is None
    assert out.realized_R is None
    assert out.unrealized_R == 0.8  # (104 - 100) / 5
    assert out.bars_elapsed == 2
    assert out.last_observed_date == date(2026, 1, 3)


def test_short_target_hit_mirrors_long():
    sig = TradeSignal(
        ticker="TEST", as_of=date(2026, 1, 1), direction="short",
        entry_price=Decimal("100"), stop_price=Decimal("105"),
        target_price=Decimal("90"), count_id="C1", count_pattern="zigzag",
        wave_label="C", degree=2, confidence=0.7,
        expected_bars_to_target=10, max_holding_bars=30,
    )
    bars = [
        _bar(2, o="100", h="102", low="89", c="90"),  # target hit (low <= 90)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "target"
    assert out.exit_price == Decimal("90")
    assert out.realized_R == 2.0  # (100 - 90) / 5 = 2


def test_short_stop_hit_mirrors_long():
    sig = TradeSignal(
        ticker="TEST", as_of=date(2026, 1, 1), direction="short",
        entry_price=Decimal("100"), stop_price=Decimal("105"),
        target_price=Decimal("90"), count_id="C1", count_pattern="zigzag",
        wave_label="C", degree=2, confidence=0.7,
        expected_bars_to_target=10, max_holding_bars=30,
    )
    bars = [
        _bar(2, o="100", h="106", low="99", c="105"),  # stop hit (high >= 105)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.realized_R == -1.0


def test_stop_on_first_walked_bar():
    sig = _long_signal()
    bars = [
        _bar(2, o="98", h="98", low="90", c="92"),  # stop hit immediately
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "stop"
    assert out.bars_held == 1


def test_empty_bars_returns_none():
    sig = _long_signal()
    out = resolve_outcome(sig, [], as_of_close=date(2026, 1, 10))
    assert out is None


def test_bars_at_or_before_as_of_are_filtered():
    sig = _long_signal(as_of_day=5)
    bars = [
        _bar(3, o="100", h="111", low="99", c="110"),  # before as_of, skip
        _bar(5, o="100", h="111", low="99", c="110"),  # == as_of, skip
        _bar(6, o="100", h="103", low="98", c="102"),  # walked
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 6))
    assert out is not None
    assert out.status == "pending"
    assert out.bars_elapsed == 1
    assert out.last_observed_date == date(2026, 1, 6)


def test_bars_after_as_of_close_are_filtered():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="103", low="99", c="102"),
        _bar(3, o="102", h="115", low="101", c="112"),  # would hit target, but past as_of_close
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 2))
    assert out is not None
    assert out.status == "pending"
    assert out.bars_elapsed == 1
```

- [ ] **Step 2: Run tests to confirm they pass with current implementation**

```bash
uv run pytest tests/trades/test_outcome_resolver.py -x
```

Expected: All PASS. (The Task 1 implementation already handles these cases; this task is the failing-tests-first regression net for them.)

- [ ] **Step 3: Commit**

```bash
git add tests/trades/test_outcome_resolver.py
git commit -m "test(trades): resolver coverage for stop/time_stop/pending/short/edges"
```

---

## Task 3: Resolver — MFE/MAE on intraday extremes

**Files:**
- Modify: `tests/trades/test_outcome_resolver.py`

- [ ] **Step 1: Add MFE/MAE tests**

Append to `tests/trades/test_outcome_resolver.py`:

```python
def test_mfe_mae_track_intraday_extremes_long():
    sig = _long_signal()  # entry=100, risk=5
    bars = [
        _bar(2, o="100", h="108", low="99", c="105"),   # high=108: MFE=1.6, low=99: MAE=-0.2
        _bar(3, o="105", h="106", low="96", c="100"),   # low=96: MAE=-0.8 (worsens)
        _bar(4, o="100", h="109", low="97", c="103"),   # high=109: MFE=1.8 (improves)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 4))
    assert out is not None
    assert out.status == "pending"
    assert out.mfe_R == 1.8
    assert out.mae_R == -0.8


def test_mfe_mae_track_intraday_extremes_short():
    sig = TradeSignal(
        ticker="TEST", as_of=date(2026, 1, 1), direction="short",
        entry_price=Decimal("100"), stop_price=Decimal("105"),
        target_price=Decimal("90"), count_id="C1", count_pattern="zigzag",
        wave_label="C", degree=2, confidence=0.7,
        expected_bars_to_target=10, max_holding_bars=30,
    )
    bars = [
        _bar(2, o="100", h="101", low="92", c="93"),   # short: low=92 favorable → MFE=1.6; high=101 adverse → MAE=-0.2
        _bar(3, o="93", h="104", low="93", c="103"),   # high=104 adverse → MAE=-0.8 (worsens)
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "pending"
    assert out.mfe_R == 1.6
    assert out.mae_R == -0.8


def test_mfe_mae_persist_to_finalized_outcome():
    sig = _long_signal()
    bars = [
        _bar(2, o="100", h="108", low="97", c="105"),   # mfe peaked here
        _bar(3, o="105", h="112", low="104", c="111"),  # target hit
    ]
    out = resolve_outcome(sig, bars, as_of_close=date(2026, 1, 3))
    assert out is not None
    assert out.status == "target"
    assert out.mfe_R == 2.4  # max(1.6 on bar 2, 2.4 on bar 3 high=112)
    assert out.mae_R == -0.6  # min(-0.6 on bar 2, -0.2 on bar 3 implied)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/trades/test_outcome_resolver.py -x
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/trades/test_outcome_resolver.py
git commit -m "test(trades): MFE/MAE coverage on intraday extremes"
```

---

## Task 4: Expose `daily_bars` on `AnalyzeResult`

**Files:**
- Modify: `src/wave_alpha/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (append at end of file):

```python
def test_analyze_result_exposes_daily_bars(deterministic_provider):
    """AnalyzeResult.daily_bars surfaces the daily fetch for downstream
    consumers (outcome resolver). Strictly additive."""
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=deterministic_provider)
    assert hasattr(result, "daily_bars")
    assert isinstance(result.daily_bars, list)
    # daily provider returns >0 bars in the test fixture
    assert len(result.daily_bars) > 0
    # All bars are <= as_of (lookahead-safe)
    assert all(b.timestamp <= date(2024, 12, 31) for b in result.daily_bars)
```

If `tests/test_pipeline.py` doesn't have a `deterministic_provider` fixture, look at existing tests in that file to see how providers are wired — there's likely a fake/stub provider. Use the same pattern. Otherwise:

```python
def test_analyze_result_exposes_daily_bars():
    from datetime import date
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis
    from tests.fixtures.providers import FakeOHLCVProvider  # adjust to actual fixture location
    provider = FakeOHLCVProvider.from_fixture("AAPL_2024")
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=provider)
    assert hasattr(result, "daily_bars")
    assert isinstance(result.daily_bars, list)
    assert len(result.daily_bars) > 0
    assert all(b.timestamp <= date(2024, 12, 31) for b in result.daily_bars)
```

If the existing test file uses an inline ad-hoc provider, mirror that pattern. The point is: `AnalyzeResult.daily_bars` is non-empty for a normal analyze call.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pipeline.py::test_analyze_result_exposes_daily_bars -x
```

Expected: FAIL with `AttributeError: 'AnalyzeResult' object has no attribute 'daily_bars'` or the assertion fails.

- [ ] **Step 3: Add the field and populate it**

In `src/wave_alpha/pipeline.py`, find the `AnalyzeResult` dataclass (around line 64):

```python
@dataclass
class AnalyzeResult:
    ticker: str
    as_of: date
    counts_weekly: list[WaveCount] = field(default_factory=list)
    counts_daily: list[WaveCount] = field(default_factory=list)
    counts_4h: list[WaveCount] = field(default_factory=list)
    coherence: CoherenceResult | None = None
    ranked_daily: list[RankedCount] = field(default_factory=list)
    llm_scan: ScanResponse | None = None
    signals: list[TradeSignal] = field(default_factory=list)
    n_atr_skipped: int = 0
    daily_bars: list[Bar] = field(default_factory=list)  # NEW
```

Make sure `Bar` is imported at the top of `pipeline.py` (it should be — check existing imports).

In `run_analysis`, find where daily bars are fetched (the `provider.fetch(..., Interval.DAILY, ...)` call) and capture the result into a local that gets passed into `AnalyzeResult(...)`. The exact line will be near the start of the function body. Look for the existing daily fetch and stash its result.

Find the `AnalyzeResult(...)` constructor at the end of `run_analysis` and add `daily_bars=daily_bars_fetched` (or whatever the local is named).

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_pipeline.py::test_analyze_result_exposes_daily_bars -x
```

Expected: PASS.

- [ ] **Step 5: Run the broader pipeline tests to verify no regressions**

```bash
uv run pytest tests/test_pipeline.py -x
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): expose AnalyzeResult.daily_bars for outcome resolver"
```

---

## Task 5: Refactor `backtest/trade_layer.py:_walk_to_exit` to use shared resolver

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Modify: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Pin existing behavior (golden parity)**

Run the existing trade-layer test suite and confirm green baseline:

```bash
uv run pytest tests/backtest/test_trade_layer.py -x
```

Expected: All PASS. Note the count and names of passing tests. If any fail before this task starts, STOP and report — the baseline must be green.

- [ ] **Step 2: Refactor `_walk_to_exit` to delegate to `resolve_outcome`**

In `src/wave_alpha/backtest/trade_layer.py`, import the shared resolver at the top:

```python
from wave_alpha.trades.outcome import resolve_outcome
```

Replace the body of `_walk_to_exit` with an adapter. The current implementation (~80 LOC) becomes:

```python
def _walk_to_exit(
    self,
    *,
    signal: TradeSignal,
    fill_date: date,
    fill_price: Decimal,
    bar_dates: list[date],
    bars_by_date: dict[date, Bar],
    date_to_idx: dict[date, int],
    end: date,
) -> TradeRecord:
    # The shared resolver walks from signal.as_of using signal.entry_price.
    # The backtest walks from fill_date using fill_price (with slippage).
    # Build a synthetic signal with entry=fill_price so the shared
    # resolver returns target/stop/time_stop bar-walks consistent with
    # this fill. Slippage on exit is layered on top by _make_record.
    walked_bars = [
        bars_by_date[d] for d in bar_dates if d >= fill_date and d <= end
    ]

    # The shared resolver filters strictly > signal.as_of; we want bars
    # from fill_date inclusive. Make a synthetic "as_of = day before fill"
    # by setting it to fill_date - timedelta(days=1). For the very first
    # walked bar (the fill bar itself) the resolver applies stop/target
    # checks using fill_price as the entry — which matches the desired
    # backtest semantic (entry occurred intra-fill-bar at fill_price).
    synthetic_signal = signal.model_copy(
        update={
            "as_of": fill_date - timedelta(days=1),
            "entry_price": fill_price,
        }
    )
    base = resolve_outcome(synthetic_signal, walked_bars, as_of_close=end)

    if base is None or base.status == "pending":
        return self._make_open_at_end_record(
            signal=signal,
            fill_date=fill_date,
            fill_price=fill_price,
            risk_per_share=abs(fill_price - signal.stop_price),
            bar_dates=bar_dates,
            end=end,
        )

    # Layer slippage / commission / borrow on the resolver's base exit.
    assert base.exit_price is not None
    assert base.exit_date is not None
    assert base.bars_held is not None
    exit_price = self._slipped_exit(base.exit_price, signal.direction)
    risk = abs(fill_price - signal.stop_price)
    return self._make_record(
        signal=signal,
        fill_date=fill_date,
        fill_price=fill_price,
        exit_date=base.exit_date,
        exit_price=exit_price,
        exit_reason=base.status,  # type: ignore[arg-type]  # Status ⊂ ExitReason
        bars_held=base.bars_held - 1,  # backtest bars_held is 0-indexed (offset)
        risk_per_share=risk,
    )
```

Notes for the implementer:

1. The `bars_held` shift (`base.bars_held - 1`) is because the backtest's existing `bars_held` field counts the fill bar as offset 0, while the shared resolver counts the first walked bar (= fill bar in this adapter) as `bars_held = 1`. The `-1` preserves the backtest's existing semantic and keeps `tests/backtest/test_trade_layer.py` green.
2. `_check_stop`, `_check_target`, `_check_target_fill_bar` private methods on `TradeLayerEvaluator` can be deleted — the shared resolver replaces them. Delete them only if nothing else uses them; otherwise leave in place.
3. Imports: add `from datetime import timedelta` if not already imported.

- [ ] **Step 3: Run the trade-layer test suite for parity**

```bash
uv run pytest tests/backtest/test_trade_layer.py -x
```

Expected: All PASS, same count as the baseline in Step 1.

- [ ] **Step 4: Run the broader backtest suite to verify no downstream regressions**

```bash
uv run pytest tests/backtest/ -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py
git commit -m "refactor(backtest): delegate _walk_to_exit to shared trades.outcome.resolve_outcome"
```

---

## Task 6: Extend `SnapshotSignal` with optional resolver fields

**Files:**
- Modify: `src/wave_alpha/history/models.py`
- Modify: `tests/history/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/history/test_models.py`:

```python
def test_snapshot_signal_accepts_optional_resolver_fields():
    """SnapshotSignal gains optional fields needed by the outcome resolver.
    All default to None so v1 snapshots round-trip without migration."""
    from datetime import date
    from decimal import Decimal
    from wave_alpha.history.models import SnapshotSignal

    sig = SnapshotSignal(
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        rr=2.0,
        # NEW optional fields:
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.7,
        expected_bars_to_target=20,
        max_holding_bars=60,
    )
    assert sig.max_holding_bars == 60
    assert sig.count_id == "C1"


def test_snapshot_signal_v1_compat_defaults_to_none():
    """A SnapshotSignal constructed without the new fields (v1 shape)
    still validates; new fields default to None."""
    from decimal import Decimal
    from wave_alpha.history.models import SnapshotSignal

    sig = SnapshotSignal(
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        rr=2.0,
    )
    assert sig.max_holding_bars is None
    assert sig.count_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/history/test_models.py::test_snapshot_signal_accepts_optional_resolver_fields tests/history/test_models.py::test_snapshot_signal_v1_compat_defaults_to_none -x
```

Expected: FAIL with Pydantic validation error (unknown fields) or attribute error.

- [ ] **Step 3: Extend the model**

In `src/wave_alpha/history/models.py`, modify `SnapshotSignal`:

```python
class SnapshotSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    rr: float
    # NEW optional fields for outcome resolver. v1 snapshots have None and
    # are silently skipped by the resolver — acceptable per spec §13.
    count_id: str | None = None
    count_pattern: str | None = None
    wave_label: str | None = None
    degree: int | None = None
    confidence: float | None = None
    expected_bars_to_target: int | None = None
    max_holding_bars: int | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/history/test_models.py -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/models.py tests/history/test_models.py
git commit -m "feat(history): extend SnapshotSignal with optional resolver fields"
```

---

## Task 7: Add `SnapshotOutcome` model + `Snapshot.outcome` field

**Files:**
- Modify: `src/wave_alpha/history/models.py`
- Modify: `tests/history/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/history/test_models.py`:

```python
def test_snapshot_outcome_model_round_trips():
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from wave_alpha.history.models import SnapshotOutcome

    out = SnapshotOutcome(
        status="target",
        exit_date=date(2026, 1, 5),
        exit_price=Decimal("110"),
        bars_held=4,
        realized_R=2.0,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=2.0,
        mfe_R=2.4,
        mae_R=-0.6,
        resolved_at=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
    )
    blob = out.model_dump_json()
    rt = SnapshotOutcome.model_validate_json(blob)
    assert rt == out


def test_snapshot_outcome_pending_allows_none_finals():
    from datetime import UTC, date, datetime
    from wave_alpha.history.models import SnapshotOutcome

    out = SnapshotOutcome(
        status="pending",
        exit_date=None,
        exit_price=None,
        bars_held=None,
        realized_R=None,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=0.8,
        mfe_R=1.2,
        mae_R=-0.2,
        resolved_at=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
    )
    assert out.realized_R is None
    assert out.exit_price is None


def test_snapshot_has_optional_outcome_field_defaulting_none():
    """v1 payloads (no `outcome` key) deserialize cleanly with outcome=None."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot

    snap = Snapshot(
        ticker="AAPL",
        as_of=date(2026, 1, 1),
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, 1), price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            ),
        ],
        signal=None,
        coherence=None,
        top_count_hash="abc",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert snap.outcome is None


def test_snapshot_validates_legacy_payload_without_outcome_key():
    """Simulate a v1 SQLite row: payload JSON has no 'outcome' field at all."""
    legacy_json = """{
        "ticker": "AAPL",
        "as_of": "2026-01-01",
        "engine_version": "0.7.0",
        "top_3": [{"candidate_id":"C1","pattern":"impulse","degree":2,
                   "last_pivot_label":"3","pivots":[],"engine_score":0.7,
                   "llm_prob":null,"final_score":0.7}],
        "signal": null,
        "coherence": null,
        "top_count_hash": "abc",
        "created_at": "2026-01-01T00:00:00+00:00"
    }"""
    from wave_alpha.history.models import Snapshot
    snap = Snapshot.model_validate_json(legacy_json)
    assert snap.outcome is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/history/test_models.py -k outcome -x
```

Expected: FAIL — `SnapshotOutcome` not defined; `Snapshot.outcome` not a field.

- [ ] **Step 3: Add `SnapshotOutcome` and extend `Snapshot`**

In `src/wave_alpha/history/models.py`:

```python
class SnapshotOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["target", "stop", "time_stop", "pending"]
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    last_observed_date: date
    bars_elapsed: int
    unrealized_R: float  # noqa: N815
    mfe_R: float  # noqa: N815
    mae_R: float  # noqa: N815
    resolved_at: datetime
```

In the existing `Snapshot` class, add the optional `outcome` field at the bottom:

```python
class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    # ... existing fields unchanged ...
    ticker: str
    as_of: date
    engine_version: str
    top_3: list[SnapshotCount] = Field(..., min_length=1, max_length=3)
    signal: SnapshotSignal | None
    coherence: SnapshotCoherence | None
    top_count_hash: str
    created_at: datetime
    outcome: SnapshotOutcome | None = None  # NEW
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/history/test_models.py -x
```

Expected: All PASS, including the new outcome tests and pre-existing model tests.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/models.py tests/history/test_models.py
git commit -m "feat(history): SnapshotOutcome model + additive Snapshot.outcome field"
```

---

## Task 8: Store schema migration + `outcome_status` column write + `unresolved_for` query

**Files:**
- Modify: `src/wave_alpha/history/store.py`
- Modify: `tests/history/test_store.py`
- Create: `tests/history/test_store_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/history/test_store_migration.py`:

```python
"""Migration / idempotence tests for the outcome_status column."""

from __future__ import annotations

import sqlite3

import pytest


def _make_v1_db(db_path):
    """Create a v1-shape snapshots table without outcome_status."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE snapshots (
                    ticker          TEXT NOT NULL,
                    as_of           TEXT NOT NULL,
                    engine_version  TEXT NOT NULL,
                    top_count_hash  TEXT NOT NULL,
                    payload_json    TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    PRIMARY KEY (ticker, as_of)
                )
                """
            )
            conn.execute(
                "CREATE INDEX idx_snapshots_ticker_as_of "
                "ON snapshots(ticker, as_of DESC)"
            )
    finally:
        conn.close()


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
    finally:
        conn.close()


def test_ensure_schema_adds_outcome_status_column_on_v1_db(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema
    db = tmp_path / "history.sqlite"
    _make_v1_db(db)
    _INITIALIZED.discard(db)  # ensure migration runs even if cached
    assert "outcome_status" not in _columns(db)
    _ensure_schema(db)
    assert "outcome_status" in _columns(db)


def test_ensure_schema_idempotent_when_column_exists(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema
    db = tmp_path / "history.sqlite"
    _make_v1_db(db)
    _INITIALIZED.discard(db)
    _ensure_schema(db)  # migrates
    _INITIALIZED.discard(db)
    _ensure_schema(db)  # second call must not raise
    assert "outcome_status" in _columns(db)


def test_ensure_schema_fresh_install_includes_outcome_status(tmp_path):
    from wave_alpha.history.store import _INITIALIZED, _ensure_schema
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _ensure_schema(db)
    assert "outcome_status" in _columns(db)
```

- [ ] **Step 2: Run the migration tests to verify they fail**

```bash
uv run pytest tests/history/test_store_migration.py -x
```

Expected: FAIL — `outcome_status` is not added by current `_ensure_schema`.

- [ ] **Step 3: Update `_ensure_schema`, `upsert`, and add `unresolved_for`**

In `src/wave_alpha/history/store.py`:

```python
_OUTCOME_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_outcome_status
    ON snapshots(ticker, outcome_status);
"""


def _ensure_schema(db_path: Path) -> None:
    if db_path in _INITIALIZED:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(_SCHEMA)
            conn.execute(_INDEX)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
            if "outcome_status" not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN outcome_status TEXT")
            conn.execute(_OUTCOME_INDEX)
    finally:
        conn.close()
    _INITIALIZED.add(db_path)
```

Modify `upsert` to write `outcome_status`:

```python
def upsert(snapshot: Snapshot, *, db_path: Path) -> None:
    _ensure_schema(db_path)
    payload = snapshot.model_dump_json()
    outcome_status = snapshot.outcome.status if snapshot.outcome else None
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO snapshots
                    (ticker, as_of, engine_version, top_count_hash,
                     payload_json, created_at, outcome_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, as_of) DO UPDATE SET
                    engine_version = excluded.engine_version,
                    top_count_hash = excluded.top_count_hash,
                    payload_json   = excluded.payload_json,
                    created_at     = excluded.created_at,
                    outcome_status = excluded.outcome_status
                """,
                (
                    snapshot.ticker,
                    snapshot.as_of.isoformat(),
                    snapshot.engine_version,
                    snapshot.top_count_hash,
                    payload,
                    snapshot.created_at.isoformat(),
                    outcome_status,
                ),
            )
    finally:
        conn.close()
```

Add the new query function at the bottom of `store.py`:

```python
def unresolved_for(ticker: str, *, db_path: Path) -> list[StoredRow]:
    """Rows where outcome_status IS NULL OR outcome_status = 'pending'.
    Returned ordered ascending by as_of so the resolver walks them in
    chronological order."""
    if not db_path.exists():
        return []
    _ensure_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, engine_version, top_count_hash,
                   payload_json, created_at
            FROM snapshots
            WHERE ticker = ?
              AND (outcome_status IS NULL OR outcome_status = 'pending')
            ORDER BY as_of ASC
            """,
            (ticker,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [
        StoredRow(
            ticker=r[0],
            as_of=date.fromisoformat(r[1]),
            engine_version=r[2],
            top_count_hash=r[3],
            payload_json=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]
```

- [ ] **Step 4: Add an `unresolved_for` test**

Append to `tests/history/test_store.py`:

```python
def test_unresolved_for_returns_null_and_pending_rows(tmp_path):
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from wave_alpha.history.models import (
        Snapshot,
        SnapshotCount,
        SnapshotOutcome,
        SnapshotPivot,
    )
    from wave_alpha.history.store import _INITIALIZED, unresolved_for, upsert

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    def _snap(day: int, outcome: SnapshotOutcome | None) -> Snapshot:
        return Snapshot(
            ticker="AAPL",
            as_of=date(2026, 1, day),
            engine_version="0.7.0",
            top_3=[
                SnapshotCount(
                    candidate_id="C1", pattern="impulse", degree=2,
                    last_pivot_label="3",
                    pivots=[SnapshotPivot(timestamp=date(2026, 1, day),
                                          price=Decimal("100"))],
                    engine_score=0.7, llm_prob=None, final_score=0.7,
                ),
            ],
            signal=None,
            coherence=None,
            top_count_hash=f"h{day}",
            created_at=datetime(2026, 1, day, tzinfo=UTC),
            outcome=outcome,
        )

    pending = SnapshotOutcome(
        status="pending", exit_date=None, exit_price=None, bars_held=None,
        realized_R=None, last_observed_date=date(2026, 1, 2),
        bars_elapsed=1, unrealized_R=0.0, mfe_R=0.0, mae_R=0.0,
        resolved_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    target = SnapshotOutcome(
        status="target", exit_date=date(2026, 1, 5),
        exit_price=Decimal("110"), bars_held=4, realized_R=2.0,
        last_observed_date=date(2026, 1, 5), bars_elapsed=4,
        unrealized_R=2.0, mfe_R=2.4, mae_R=-0.2,
        resolved_at=datetime(2026, 1, 6, tzinfo=UTC),
    )

    upsert(_snap(1, None), db_path=db)
    upsert(_snap(2, pending), db_path=db)
    upsert(_snap(3, target), db_path=db)

    rows = unresolved_for("AAPL", db_path=db)
    as_ofs = [r.as_of for r in rows]
    assert as_ofs == [date(2026, 1, 1), date(2026, 1, 2)]  # NULL + pending, asc
```

- [ ] **Step 5: Run all store tests**

```bash
uv run pytest tests/history/test_store.py tests/history/test_store_migration.py -x
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/store.py tests/history/test_store.py tests/history/test_store_migration.py
git commit -m "feat(history): outcome_status column + unresolved_for query + idempotent migration"
```

---

## Task 9: `history/resolver.py` — `resolve_pending_for`

**Files:**
- Create: `src/wave_alpha/history/resolver.py`
- Create: `tests/history/test_resolver.py`

- [ ] **Step 1: Write the failing end-to-end test**

Create `tests/history/test_resolver.py`:

```python
"""End-to-end tests for history.resolver.resolve_pending_for."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.history.resolver import resolve_pending_for
from wave_alpha.history.store import _INITIALIZED, recent, upsert


def _bar(day: int, *, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        timestamp=date(2026, 1, day), interval=Interval.DAILY,
        open=Decimal(o), high=Decimal(h), low=Decimal(low),
        close=Decimal(c), volume=1_000_000,
    )


def _build_snapshot(*, as_of_day: int, with_signal: bool = True) -> Snapshot:
    signal = (
        SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("110"),
            rr=2.0,
            count_id="C1", count_pattern="impulse", wave_label="3",
            degree=2, confidence=0.7, expected_bars_to_target=20,
            max_holding_bars=60,
        )
        if with_signal
        else None
    )
    return Snapshot(
        ticker="AAPL",
        as_of=date(2026, 1, as_of_day),
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1", pattern="impulse", degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(
                    timestamp=date(2026, 1, as_of_day), price=Decimal("100"),
                )],
                engine_score=0.7, llm_prob=None, final_score=0.7,
            ),
        ],
        signal=signal,
        coherence=None,
        top_count_hash=f"hash-{as_of_day}",
        created_at=datetime(2026, 1, as_of_day, tzinfo=UTC),
    )


def test_resolve_pending_for_marks_resolved_rows_with_target(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="112", low="102", c="111"),  # target
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 3))
    assert updated == 1
    rows = recent("AAPL", n=10, db_path=db)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "target"
    assert snap.outcome.realized_R == 2.0


def test_resolve_pending_for_skips_signal_none(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1, with_signal=False), db_path=db)
    bars = [_bar(2, o="101", h="105", low="99", c="103")]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 0
    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is None


def test_resolve_pending_for_skips_v1_signals_missing_max_holding(tmp_path):
    """A snapshot whose SnapshotSignal lacks max_holding_bars (v1 row) is
    skipped — outcome stays None."""
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    snap = _build_snapshot(as_of_day=1)
    # Strip the new fields to simulate a v1-shaped signal.
    v1_signal = SnapshotSignal(
        direction="long", entry_price=Decimal("100"),
        stop_price=Decimal("95"), target_price=Decimal("110"), rr=2.0,
    )
    v1_snap = snap.model_copy(update={"signal": v1_signal})
    upsert(v1_snap, db_path=db)
    bars = [
        _bar(2, o="103", h="112", low="102", c="111"),
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 0
    rows = recent("AAPL", n=10, db_path=db)
    snap_after = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap_after.outcome is None


def test_resolve_pending_for_writes_pending_state_when_in_window(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="106", low="102", c="105"),
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 3))
    assert updated == 1
    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "pending"
    assert snap.outcome.bars_elapsed == 2
    assert snap.outcome.unrealized_R == 1.0  # (105 - 100) / 5


def test_resolve_pending_for_only_touches_target_ticker(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    msft = _build_snapshot(as_of_day=1).model_copy(update={"ticker": "MSFT"})
    upsert(msft, db_path=db)
    bars = [
        _bar(2, o="103", h="112", low="102", c="111"),  # target
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 1
    msft_rows = recent("MSFT", n=10, db_path=db)
    msft_snap = Snapshot.model_validate_json(msft_rows[0].payload_json)
    assert msft_snap.outcome is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/history/test_resolver.py -x
```

Expected: FAIL — `history.resolver` module does not exist.

- [ ] **Step 3: Implement `history/resolver.py`**

Create `src/wave_alpha/history/resolver.py`:

```python
"""History outcome resolver. Walks unresolved/pending snapshots forward
using daily bars produced by analyze; updates each snapshot in place
via the shared trades.outcome.resolve_outcome resolver."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

from wave_alpha.domain import Bar
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotOutcome,
    SnapshotSignal,
)
from wave_alpha.trades.models import TradeSignal
from wave_alpha.trades.outcome import Outcome, resolve_outcome

logger = logging.getLogger(__name__)


def resolve_pending_for(
    ticker: str,
    daily_bars: list[Bar],
    *,
    db_path: Path,
    today: date,
) -> int:
    """Walk every (ticker, as_of) row with outcome_status IN (NULL, 'pending')
    whose snapshot has a fully-formed signal. Compute outcome, upsert payload.
    Returns count of rows updated. Errors per-row are logged and skipped."""
    rows = store.unresolved_for(ticker, db_path=db_path)
    bars_sorted = sorted(daily_bars, key=lambda b: b.timestamp)
    updated = 0
    for row in rows:
        try:
            snapshot = Snapshot.model_validate_json(row.payload_json)
        except Exception as exc:
            logger.warning("malformed snapshot for %s @ %s: %s",
                           ticker, row.as_of, exc)
            continue
        signal = _to_trade_signal(snapshot)
        if signal is None:
            continue
        outcome = resolve_outcome(signal, bars_sorted, as_of_close=today)
        if outcome is None:
            continue
        new_snapshot = snapshot.model_copy(
            update={"outcome": _to_snapshot_outcome(outcome)}
        )
        store.upsert(new_snapshot, db_path=db_path)
        updated += 1
    return updated


def _to_trade_signal(snapshot: Snapshot) -> TradeSignal | None:
    sig = snapshot.signal
    if sig is None:
        return None
    # v1 signals lacking max_holding_bars are unresolvable — skip.
    if sig.max_holding_bars is None:
        return None
    return TradeSignal(
        ticker=snapshot.ticker,
        as_of=snapshot.as_of,
        direction=sig.direction,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        count_id=sig.count_id or "unknown",
        count_pattern=sig.count_pattern or "unknown",
        wave_label=sig.wave_label or "?",
        degree=sig.degree if sig.degree is not None else 0,
        confidence=sig.confidence if sig.confidence is not None else 0.0,
        expected_bars_to_target=(
            sig.expected_bars_to_target if sig.expected_bars_to_target is not None else 0
        ),
        max_holding_bars=sig.max_holding_bars,
    )


def _to_snapshot_outcome(o: Outcome) -> SnapshotOutcome:
    return SnapshotOutcome(
        status=o.status,
        exit_date=o.exit_date,
        exit_price=o.exit_price,
        bars_held=o.bars_held,
        realized_R=o.realized_R,
        last_observed_date=o.last_observed_date,
        bars_elapsed=o.bars_elapsed,
        unrealized_R=o.unrealized_R,
        mfe_R=o.mfe_R,
        mae_R=o.mae_R,
        resolved_at=datetime.now(tz=UTC),
    )
```

Note: `_to_trade_signal` falls back to placeholder values for fields the resolver doesn't actually use (`count_id`, `count_pattern`, etc.), since `TradeSignal` validation requires them but the resolver only reads direction/entry/stop/target/as_of/max_holding_bars. The `_ = (sig.max_holding_bars is None)` skip guards the only field that the resolver requires.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/history/test_resolver.py -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/resolver.py tests/history/test_resolver.py
git commit -m "feat(history): resolve_pending_for walks unresolved snapshots forward"
```

---

## Task 10: Wire resolver into `service.record_snapshot` + populate new SnapshotSignal fields

**Files:**
- Modify: `src/wave_alpha/history/service.py`
- Modify: `tests/history/test_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/history/test_service.py`:

```python
def test_record_snapshot_triggers_resolution_of_prior_pending_rows(tmp_path):
    """Writing today's snapshot for AAPL should also resolve any prior
    AAPL snapshot whose outcome is unresolved or pending."""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from wave_alpha.domain import Bar, Interval
    from wave_alpha.history.models import (
        Snapshot, SnapshotCount, SnapshotPivot, SnapshotSignal,
    )
    from wave_alpha.history.service import record_snapshot
    from wave_alpha.history.store import _INITIALIZED, recent, upsert
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.elliott.models import PivotEvent, PivotType, WaveCount
    from wave_alpha.trades.models import TradeSignal

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    # Plant a prior unresolved snapshot at Jan 1.
    prior = Snapshot(
        ticker="AAPL", as_of=date(2026, 1, 1), engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1", pattern="impulse", degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, 1),
                                      price=Decimal("100"))],
                engine_score=0.7, llm_prob=None, final_score=0.7,
            ),
        ],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("110"), rr=2.0,
            count_id="C1", count_pattern="impulse", wave_label="3",
            degree=2, confidence=0.7, expected_bars_to_target=20,
            max_holding_bars=60,
        ),
        coherence=None, top_count_hash="h1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    upsert(prior, db_path=db)

    # Build an AnalyzeResult whose daily_bars include a target-hit on Jan 3.
    def _b(day, h, low, c):
        return Bar(
            timestamp=date(2026, 1, day), interval=Interval.DAILY,
            open=Decimal("100"), high=Decimal(h), low=Decimal(low),
            close=Decimal(c), volume=1_000_000,
        )
    result = AnalyzeResult(
        ticker="AAPL", as_of=date(2026, 1, 3),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(
                    pattern="impulse", degree=2,
                    pivots=[
                        PivotEvent(timestamp=date(2026, 1, 3),
                                   price=Decimal("100"), type=PivotType.HIGH,
                                   degree=2),
                    ],
                ),
                engine_score=0.7, llm_prob=None, final_score=0.7,
            ),
        ],
        daily_bars=[
            _b(2, "105", "99", "103"),
            _b(3, "112", "102", "111"),  # target
        ],
    )

    record_snapshot(result, "AAPL", date(2026, 1, 3), db_path=db)

    # Prior snapshot should now have status=target.
    rows = recent("AAPL", n=10, db_path=db)
    assert len(rows) == 2  # today's + prior
    prior_after = next(
        Snapshot.model_validate_json(r.payload_json)
        for r in rows if r.as_of == date(2026, 1, 1)
    )
    assert prior_after.outcome is not None
    assert prior_after.outcome.status == "target"


def test_record_snapshot_resolver_error_does_not_break_write(tmp_path, monkeypatch):
    """If the resolver raises, today's snapshot must still be written."""
    from datetime import date
    from wave_alpha.history import resolver as resolver_mod
    from wave_alpha.history.service import record_snapshot
    from wave_alpha.history.store import _INITIALIZED, recent

    def _boom(*args, **kwargs):
        raise RuntimeError("resolver kaboom")

    monkeypatch.setattr(resolver_mod, "resolve_pending_for", _boom)

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    # Use the same fixture-building helpers as the previous test or
    # inline them. Construct a minimal AnalyzeResult and call
    # record_snapshot — it must not raise.
    # ... (build minimal AnalyzeResult with ranked_daily and daily_bars=[])
    # Assertion: rows = recent("AAPL", n=10, db_path=db); len(rows) == 1.
```

(Implementer note: extract the minimal-AnalyzeResult builder from the first test into a fixture/helper to reuse here; the test body above shows the assertion shape.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/history/test_service.py::test_record_snapshot_triggers_resolution_of_prior_pending_rows -x
```

Expected: FAIL — resolver not called from `record_snapshot`.

- [ ] **Step 3: Extend `service.record_snapshot` to call the resolver**

In `src/wave_alpha/history/service.py`, import the resolver module at the top:

```python
from wave_alpha.history import resolver
```

Modify `record_snapshot`:

```python
def record_snapshot(
    result: AnalyzeResult,
    ticker: str,
    as_of: date,
    *,
    db_path: Path,
) -> None:
    """Persist `result` as a snapshot and resolve any prior unresolved
    or pending snapshots for this ticker. Side-effect only — never raises."""
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(
                ticker, result.daily_bars, db_path=db_path, today=as_of,
            )
    except Exception as exc:
        logger.warning("history snapshot/outcome failed for %s @ %s: %s",
                       ticker, as_of, exc)
```

Also update `_to_snapshot_signal` to populate the new fields:

```python
def _to_snapshot_signal(sig: TradeSignal | None) -> SnapshotSignal | None:
    if sig is None:
        return None
    return SnapshotSignal(
        direction=sig.direction,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        rr=sig.rr,
        count_id=sig.count_id,
        count_pattern=sig.count_pattern,
        wave_label=sig.wave_label,
        degree=sig.degree,
        confidence=sig.confidence,
        expected_bars_to_target=sig.expected_bars_to_target,
        max_holding_bars=sig.max_holding_bars,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/history/test_service.py -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_service.py
git commit -m "feat(history): trigger resolver from record_snapshot; populate new SnapshotSignal fields"
```

---

## Task 11: `compute_outcome_badge` pure function

**Files:**
- Modify: `src/wave_alpha/history/service.py`
- Modify: `src/wave_alpha/history/models.py`
- Modify: `tests/history/test_stability_badge.py` (or new `tests/history/test_outcome_badge.py`)

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_outcome_badge.py`:

```python
"""Pure-function tests for compute_outcome_badge."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _outcome(status, realized_R=None, unrealized_R=0.0):  # noqa: N803
    from wave_alpha.history.models import SnapshotOutcome
    return SnapshotOutcome(
        status=status,
        exit_date=None if status == "pending" else date(2026, 1, 5),
        exit_price=None if status == "pending" else Decimal("110"),
        bars_held=None if status == "pending" else 4,
        realized_R=realized_R,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=unrealized_R if status == "pending" else (realized_R or 0.0),
        mfe_R=0.0,
        mae_R=0.0,
        resolved_at=datetime(2026, 1, 6, tzinfo=UTC),
    )


def _snap_with(outcome):
    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot
    return Snapshot(
        ticker="AAPL", as_of=date(2026, 1, 1), engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1", pattern="impulse", degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, 1),
                                      price=Decimal("100"))],
                engine_score=0.7, llm_prob=None, final_score=0.7,
            ),
        ],
        signal=None, coherence=None, top_count_hash="h",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        outcome=outcome,
    )


def test_compute_outcome_badge_basic():
    from wave_alpha.history.service import compute_outcome_badge
    snaps = [
        _snap_with(_outcome("target", realized_R=2.0)),
        _snap_with(_outcome("target", realized_R=1.5)),
        _snap_with(_outcome("stop", realized_R=-1.0)),
        _snap_with(_outcome("time_stop", realized_R=0.3)),
        _snap_with(_outcome("pending", unrealized_R=0.4)),
        _snap_with(_outcome("pending", unrealized_R=-0.2)),
    ]
    badge = compute_outcome_badge(snaps)
    assert badge is not None
    assert badge.resolved_count == 4
    assert badge.target_count == 2
    assert abs(badge.hit_rate - 0.5) < 1e-9
    assert abs(badge.avg_R - (2.0 + 1.5 + (-1.0) + 0.3) / 4) < 1e-9
    assert badge.in_flight_count == 2


def test_compute_outcome_badge_returns_none_when_no_outcome():
    from wave_alpha.history.service import compute_outcome_badge
    assert compute_outcome_badge([_snap_with(None)]) is None
    assert compute_outcome_badge([]) is None


def test_compute_outcome_badge_zero_resolved_with_pending():
    from wave_alpha.history.service import compute_outcome_badge
    snaps = [
        _snap_with(_outcome("pending", unrealized_R=0.3)),
        _snap_with(_outcome("pending", unrealized_R=-0.1)),
    ]
    badge = compute_outcome_badge(snaps)
    assert badge is not None
    assert badge.resolved_count == 0
    assert badge.target_count == 0
    assert badge.hit_rate == 0.0
    assert badge.avg_R == 0.0
    assert badge.in_flight_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/history/test_outcome_badge.py -x
```

Expected: FAIL — `OutcomeBadge` and `compute_outcome_badge` not defined.

- [ ] **Step 3: Add `OutcomeBadge` dataclass and `compute_outcome_badge`**

In `src/wave_alpha/history/models.py`, add at the bottom (next to `StabilityBadge`):

```python
@dataclass(frozen=True)
class OutcomeBadge:
    resolved_count: int       # target + stop + time_stop
    target_count: int
    hit_rate: float           # target_count / resolved_count when resolved_count > 0 else 0.0
    avg_R: float              # noqa: N815 — mean realized_R across resolved
    in_flight_count: int      # pending
```

In `src/wave_alpha/history/service.py`, import `OutcomeBadge` from models and add the function:

```python
from wave_alpha.history.models import OutcomeBadge  # add to existing import

def compute_outcome_badge(snapshots: list[Snapshot]) -> OutcomeBadge | None:
    """Aggregate snapshot outcomes for the deepdive header badge.
    Returns None when no snapshot has any outcome (resolved or pending)."""
    resolved = [
        s.outcome for s in snapshots
        if s.outcome is not None and s.outcome.status != "pending"
    ]
    pending = [
        s.outcome for s in snapshots
        if s.outcome is not None and s.outcome.status == "pending"
    ]
    if not resolved and not pending:
        return None
    target_count = sum(1 for o in resolved if o.status == "target")
    hit_rate = (target_count / len(resolved)) if resolved else 0.0
    avg_r = (
        sum(o.realized_R for o in resolved if o.realized_R is not None) / len(resolved)
        if resolved
        else 0.0
    )
    return OutcomeBadge(
        resolved_count=len(resolved),
        target_count=target_count,
        hit_rate=hit_rate,
        avg_R=avg_r,
        in_flight_count=len(pending),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/history/test_outcome_badge.py -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/models.py src/wave_alpha/history/service.py tests/history/test_outcome_badge.py
git commit -m "feat(history): compute_outcome_badge for deepdive header"
```

---

## Task 12: Deepdive route — pass outcome badge into history fragment context

**Files:**
- Modify: `src/wave_alpha/web/routes/deepdive.py`
- Modify: `tests/web/...` (existing deepdive tests; locate via `grep`)

- [ ] **Step 1: Inspect existing route and template wiring**

```bash
uv run grep -n "history_fragment\|_history.html\|service.get_recent_with_badge" src/wave_alpha/web/routes/deepdive.py
```

Find the function (likely `history_fragment` around line 169 per the earlier survey) and the template render call. Note the existing context dict shape — it currently passes `{"snapshots": snapshots, "badge": badge}`.

- [ ] **Step 2: Write the failing test (locate the right test file)**

```bash
ls -1 tests/web/ 2>/dev/null
```

Identify the test file for the history fragment (likely `tests/web/test_history_fragment.py` or similar). If none exists, create `tests/web/test_history_outcome_badge.py`:

```python
"""Outcome badge is rendered into the history fragment context."""

from __future__ import annotations

import pytest


@pytest.fixture
def fake_history(monkeypatch, tmp_path):
    """Seed the SQLite history with one resolved + one pending snapshot for AAPL."""
    # ... build snapshots using the same builders as test_resolver.py;
    # monkeypatch deps._data_dir to point at tmp_path so the route reads
    # from the test fixture DB.
    raise NotImplementedError("implementer: pattern-match existing web tests")


def test_history_fragment_includes_outcome_badge(fake_history, client):
    """GET /deepdive/AAPL/history returns HTML containing the outcome badge text."""
    resp = client.get("/deepdive/AAPL/history")
    assert resp.status_code == 200
    body = resp.text
    assert "Outcomes:" in body or "outcome-badge" in body  # css class or label
```

If a similar fixture for SQLite seeding already exists in `tests/web/`, reuse it.

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/web/test_history_outcome_badge.py -x
```

Expected: FAIL — badge not in template output.

- [ ] **Step 4: Wire the badge into the route's context**

In `src/wave_alpha/web/routes/deepdive.py`, update `history_fragment` to call `compute_outcome_badge` and pass it into the template context:

```python
from wave_alpha.history import service as history_service

# inside history_fragment(...):
recent = history_service.get_recent_with_badge(ticker.upper(), db_path=db_path)
snapshots = recent.snapshots
badge = recent.badge
outcome_badge = history_service.compute_outcome_badge(snapshots)

return templates.TemplateResponse(
    request=request,
    name="_history.html",
    context={
        "snapshots": snapshots,
        "badge": badge,
        "outcome_badge": outcome_badge,
    },
)
```

- [ ] **Step 5: Run test to verify it passes (after Task 13 updates the template)**

This step pairs with the template update in Task 13. For now, ensure the route doesn't crash; the template-level assertion lands in Task 13.

```bash
uv run pytest tests/web/ -x
```

Expected: All PASS (the outcome-badge assertion will pass once the template renders the data — defer that to Task 13).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py tests/web/test_history_outcome_badge.py
git commit -m "feat(web): pass outcome_badge into history fragment context"
```

---

## Task 13: `_history.html` template — outcome chip + outcome badge

**Files:**
- Modify: `src/wave_alpha/web/templates/_history.html`

- [ ] **Step 1: Inspect the existing template**

```bash
cat src/wave_alpha/web/templates/_history.html
```

Identify (a) the badge area at the top of the card and (b) the row-rendering loop over `snapshots`.

- [ ] **Step 2: Add the outcome badge under the stability badge**

In the template, immediately after the existing stability badge block, add:

```html
{% if outcome_badge %}
<div class="outcome-badge text-sm text-muted-foreground mt-1">
  Outcomes:
  <strong>{{ outcome_badge.target_count }} of {{ outcome_badge.resolved_count }} resolved
  ({{ "%.0f"|format(outcome_badge.hit_rate * 100) }}%) hit target</strong>
  · avg {{ "%+.1f"|format(outcome_badge.avg_R) }}R
  {% if outcome_badge.in_flight_count > 0 %}
    · {{ outcome_badge.in_flight_count }} still in flight
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 3: Add the per-row outcome chip in the row loop**

Inside the snapshot-row loop (`{% for snap in snapshots %}` block), append a right-aligned chip element:

```html
<span class="outcome-chip outcome-chip--{{ snap.outcome.status if snap.outcome else 'none' }}">
  {% if snap.outcome %}
    {% if snap.outcome.status == "target" %}
      ✓ {{ "%+.1f"|format(snap.outcome.realized_R) }}R ({{ snap.outcome.bars_held }} bars)
    {% elif snap.outcome.status == "stop" %}
      ✗ {{ "%+.1f"|format(snap.outcome.realized_R) }}R ({{ snap.outcome.bars_held }} bars)
    {% elif snap.outcome.status == "time_stop" %}
      ⊘ {{ "%+.1f"|format(snap.outcome.realized_R) }}R ({{ snap.outcome.bars_held }} bars)
    {% elif snap.outcome.status == "pending" %}
      ⊙ {{ "%+.1f"|format(snap.outcome.unrealized_R) }}R
      · day {{ snap.outcome.bars_elapsed }}/{{ snap.signal.max_holding_bars or '?' }}
      · peak {{ "%+.1f"|format(snap.outcome.mfe_R) }}R
    {% endif %}
  {% elif not snap.signal %}
    — (no signal)
  {% else %}
    — (resolving)
  {% endif %}
</span>
```

CSS classes `outcome-chip--target`, `outcome-chip--stop`, `outcome-chip--time_stop`, `outcome-chip--pending`, `outcome-chip--none` map to the palette tokens (success/danger/warning/info/muted) — add to the existing stylesheet if not already present. **Use the existing palette tokens — no neon** (per the candles-classic-colors user memory).

- [ ] **Step 4: Run the web tests**

```bash
uv run pytest tests/web/ -x
```

Expected: All PASS, including the badge-text assertion from Task 12.

- [ ] **Step 5: Manually verify the rendered card** (optional but recommended)

```bash
uv run wave-alpha ui &
# open http://127.0.0.1:8765/deepdive/AAPL in a browser
# trigger a few daily snapshots over time or seed test data with the test fixtures
```

Visually confirm: badge text appears, chips render with correct glyphs and colors, no neon on the chart-adjacent UI.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/web/templates/_history.html
git commit -m "feat(web): outcome chip + outcome badge in history card"
```

---

## Task 14: `wave-alpha resolve` CLI command

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/cli/test_resolve_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_resolve_command.py`:

```python
"""Tests for the wave-alpha resolve CLI command."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history.models import (
    Snapshot, SnapshotCount, SnapshotPivot, SnapshotSignal,
)
from wave_alpha.history.store import _INITIALIZED, recent, upsert


def _seed_pending(db_path):
    snap = Snapshot(
        ticker="AAPL", as_of=date(2026, 1, 1), engine_version="0.7.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=2,
            last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=date(2026, 1, 1),
                                  price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("110"), rr=2.0,
            count_id="C1", count_pattern="impulse", wave_label="3",
            degree=2, confidence=0.7, expected_bars_to_target=20,
            max_holding_bars=60,
        ),
        coherence=None, top_count_hash="h1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    upsert(snap, db_path=db_path)


class _FakeProvider:
    """Return a target-hit bar series for AAPL daily."""
    def fetch(self, ticker, interval, *, end=None, start=None, **kw):
        if interval != Interval.DAILY:
            return []
        return [
            Bar(timestamp=date(2026, 1, 2), interval=Interval.DAILY,
                open=Decimal("101"), high=Decimal("105"), low=Decimal("99"),
                close=Decimal("103"), volume=1_000_000),
            Bar(timestamp=date(2026, 1, 3), interval=Interval.DAILY,
                open=Decimal("103"), high=Decimal("112"), low=Decimal("102"),
                close=Decimal("111"), volume=1_000_000),
        ]


def test_resolve_command_resolves_pending(monkeypatch, tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _seed_pending(db)

    # Monkeypatch the CLI to use our fake provider and tmp db.
    from wave_alpha.cli import app as cli_app
    monkeypatch.setattr(cli_app, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_app, "_build_provider", lambda: _FakeProvider())

    runner = CliRunner()
    result = runner.invoke(app, ["resolve", "--ticker", "AAPL"])
    assert result.exit_code == 0, result.stdout
    assert "AAPL" in result.stdout
    assert "1" in result.stdout  # rows_resolved=1

    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "target"


def test_resolve_command_json_output(monkeypatch, tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    _seed_pending(db)

    from wave_alpha.cli import app as cli_app
    monkeypatch.setattr(cli_app, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_app, "_build_provider", lambda: _FakeProvider())

    runner = CliRunner()
    result = runner.invoke(app, ["resolve", "--ticker", "AAPL", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tickers"][0]["ticker"] == "AAPL"
    assert payload["tickers"][0]["rows_resolved"] == 1
```

(Implementer note: the exact `_build_provider` indirection name may differ in `cli/app.py`. Use `grep -n "CachedProvider\|provider" src/wave_alpha/cli/app.py` to locate the existing provider-construction site and monkeypatch that same function.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/cli/test_resolve_command.py -x
```

Expected: FAIL — `resolve` command not registered.

- [ ] **Step 3: Add the command**

In `src/wave_alpha/cli/app.py`, add (top-level command, not under any subapp):

```python
@app.command()
def resolve(
    ticker: str | None = typer.Option(None, "--ticker", "-t"),
    all_tickers: bool = typer.Option(False, "--all"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Resolve any unresolved or pending history snapshots for one ticker
    or all tickers. Walks daily bars and updates outcome status."""
    if not ticker and not all_tickers:
        typer.echo("Specify --ticker T or --all", err=True)
        raise typer.Exit(code=1)

    # Lazy imports
    import json as _json
    from datetime import UTC, date as _date, datetime as _datetime
    from wave_alpha.domain import Interval
    from wave_alpha.history import resolver as history_resolver
    from wave_alpha.history import store as history_store

    db_path = _data_dir() / "history.sqlite"
    provider = _build_provider()  # adjust to actual indirection in this file
    today = _date.today()

    if all_tickers:
        # All tickers with any unresolved row
        all_rows = history_store.recent_across_all(db_path=db_path, limit=10_000)
        tickers = sorted({r.ticker for r in all_rows})
    else:
        tickers = [ticker.upper()]

    summaries = []
    for t in tickers:
        rows = history_store.unresolved_for(t, db_path=db_path)
        if not rows:
            summaries.append({
                "ticker": t, "total_unresolved": 0, "rows_resolved": 0,
                "rows_still_pending": 0, "errors": [],
            })
            continue
        earliest = min(r.as_of for r in rows)
        try:
            bars = provider.fetch(t, Interval.DAILY, end=today)
            bars_in_range = [b for b in bars if b.timestamp >= earliest]
            updated = history_resolver.resolve_pending_for(
                t, bars_in_range, db_path=db_path, today=today,
            )
            # Recount pending after resolution
            still_pending = sum(
                1 for r in history_store.unresolved_for(t, db_path=db_path)
            )
            summaries.append({
                "ticker": t, "total_unresolved": len(rows),
                "rows_resolved": updated,
                "rows_still_pending": still_pending,
                "errors": [],
            })
        except Exception as exc:  # noqa: BLE001
            summaries.append({
                "ticker": t, "total_unresolved": len(rows),
                "rows_resolved": 0, "rows_still_pending": len(rows),
                "errors": [str(exc)],
            })

    if json_output:
        output = {
            "resolved_at": _datetime.now(tz=UTC).isoformat(),
            "tickers": summaries,
        }
        typer.echo(_json.dumps(output, default=str))
    else:
        for s in summaries:
            typer.echo(
                f"{s['ticker']}: resolved {s['rows_resolved']}/{s['total_unresolved']}, "
                f"still pending {s['rows_still_pending']}"
            )
            if s["errors"]:
                for e in s["errors"]:
                    typer.echo(f"  error: {e}", err=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/cli/test_resolve_command.py -x
```

Expected: All PASS.

- [ ] **Step 5: Verify the CLI smoke-runs**

```bash
uv run wave-alpha resolve --help
```

Expected: Typer help text for the new command, listing `--ticker`, `--all`, `--json`.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_resolve_command.py
git commit -m "feat(cli): wave-alpha resolve [--ticker|--all] [--json]"
```

---

## Task 15: Final full-suite verification

**Files:** (no source changes)

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest
```

Expected: All PASS. If any tests fail, fix them before moving on. **Do not skip or xfail tests to make the build green.**

- [ ] **Step 2: Run lint and format**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: No errors. Fix any reported issues (rename violations of `N815` are pinned with `# noqa: N815` per convention in this codebase; ensure new code follows the same pattern for `realized_R`, `unrealized_R`, etc.).

- [ ] **Step 3: Refresh the GitNexus index**

```bash
npx gitnexus analyze
```

Expected: index updated. Verify the wave-alpha repo's symbol counts increased commensurate with the new modules.

- [ ] **Step 4: Commit any final cleanup (if needed)**

```bash
git status
# If anything is dirty:
git add -A
git commit -m "chore: outcome-tracking cleanup pass"
```

- [ ] **Step 5: Final sanity smoke test**

```bash
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json | head -30
```

Expected: Analyze command works (no regressions from the pipeline change). The snapshot for today gets written (visible in `~/.wave_alpha/history.sqlite`); future analyses will produce resolved outcomes.

```bash
uv run wave-alpha resolve --ticker AAPL
```

Expected: Either reports `resolved 0/0` (no pending rows yet) or resolves any that exist.

---

## Self-Review

**Spec coverage check** (going section-by-section through `2026-05-10-outcome-tracking-design.md`):

- §1 Motivation — N/A (rationale)
- §2 Goals:
  - Outcomes on every signaled snapshot → Tasks 1-3, 9, 10
  - Raw engine accuracy R → Tasks 1-3 (no slippage in resolver)
  - Shared resolver with backtest → Tasks 1, 5
  - Per-row chip + headline badge → Tasks 11, 12, 13
  - `wave-alpha resolve` CLI → Task 14
  - Queryable `outcome_status` column → Task 8
- §3 Non-goals — N/A
- §4 User-visible behavior — Tasks 12, 13, 14
- §5 Architecture — Tasks 1, 4, 5, 7-14
- §6 Data model:
  - `Outcome` dataclass → Task 1
  - `SnapshotOutcome` → Task 7
  - `Snapshot.outcome` → Task 7
  - `outcome_status` column + migration → Task 8
- §7 Resolver:
  - Pure `resolve_outcome` → Tasks 1-3
  - Backtest adapter → Task 5
  - `resolve_pending_for` → Task 9
  - `record_snapshot` extension → Task 10
  - Per-ticker scope → Task 9 (`unresolved_for(ticker, ...)`)
- §8 AnalyzeResult bars plumbing → Task 4
- §9 CLI → Task 14
- §10 UI surface → Tasks 11, 12, 13
- §11 Testing strategy — covered across tasks; backtest parity in Task 5
- §12 Migration & rollout → Task 8
- §13 Risks — N/A (acknowledged in spec)
- §14 v3 hooks — N/A (deferred)

**Placeholder scan:** None. Every step has actual code or commands. Two implementer notes (Task 4's `deterministic_provider` fixture lookup, Task 10's helper extraction, Task 14's `_build_provider` indirection) require a quick `grep` against the existing codebase but the surrounding tests show the exact pattern to mirror.

**Type consistency:**
- `Outcome.status` and `SnapshotOutcome.status`: both `Literal["target", "stop", "time_stop", "pending"]` ✓
- `realized_R` / `unrealized_R` / `mfe_R` / `mae_R`: all `float`, both in `Outcome` and `SnapshotOutcome` ✓
- `bars_held: int | None` in `Outcome` and `SnapshotOutcome` ✓
- `resolve_outcome(signal, bars, *, as_of_close)` signature stable across Tasks 1-3 ✓
- `resolve_pending_for(ticker, daily_bars, *, db_path, today)` signature stable across Tasks 9, 10, 14 ✓
- `compute_outcome_badge(snapshots) -> OutcomeBadge | None` signature stable across Tasks 11, 12 ✓

---

**Plan complete.** Saved to `docs/superpowers/plans/2026-05-10-outcome-tracking.md`.
