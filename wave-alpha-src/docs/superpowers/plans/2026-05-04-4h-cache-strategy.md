# 4h Cache Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the provider/cache pair from re-fetching the 4h timeframe upstream on every backtest as_of when `end < today − 730d`, surface 4h coverage in trade-layer reports, and warn users when a backtest window crosses yfinance's 4h availability boundary.

**Architecture:** Move the `end`-filter responsibility from `YahooProvider` into `CachedProvider`'s exit path; relax `CachedProvider`'s hit predicate to `_tail_fresh_enough(end)` so that an empty-but-fresh cache slice doesn't fall through to upstream. Add `tf_count` plumbing through `TradeRecord` and a `coverage_4h` aggregate on `TradeLayerResult`. Add a one-shot stderr warning helper to all three `wave-alpha backtest` subcommands.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `typer`, `decimal.Decimal` for prices.

**Spec:** `docs/superpowers/specs/2026-05-04-4h-cache-strategy-design.md`

---

## File structure

**Modified — production:**
- `src/wave_alpha/data/yahoo.py` — drop the post-fetch `end`-filter (lines 84-85).
- `src/wave_alpha/data/cached.py` — add `_filter_window`; simplify hit predicate; ensure all return paths are filtered.
- `src/wave_alpha/backtest/trade_layer.py` — add `tf_count: Literal[2, 3]` to `TradeRecord`; stash `_last_tf_count` in `_signal_at` alongside the existing `_last_coherence_score`; populate in `_make_record` and `_make_open_at_end_record`. Add `coverage_4h` property and `by_tf_count` method on `TradeLayerResult`.
- `src/wave_alpha/backtest/report.py` — render the `**4h coverage:**` header line.
- `src/wave_alpha/cli/app.py` — add `_warn_if_4h_window_partial`; call it from `backtest_count`, `backtest_pivot`, `backtest_trade`. Add `coverage_4h` and `by_tf_count` to the JSON sidecar payload (the CSV `tf_count` column comes free from `asdict(record)` once the field exists).

**Modified — tests:**
- `tests/data/test_cached.py` — new tests per spec §7.1.
- `tests/data/test_yahoo.py` — new test per spec §7.2.
- `tests/backtest/test_trade_layer.py` — new tests per spec §7.3.
- `tests/backtest/test_report.py` — new tests per spec §7.4.
- `tests/cli/test_backtest_trade.py` — new tests per spec §7.5 (covers all three backtest subcommands).

**Modified — docs/build:**
- `README.md` — 4h-history subsection in the v0.5 backtest area.
- `pyproject.toml` — bump to `version = "0.5.3"`.

---

## Phase A — Cache mechanics

### Task A1: Move `end`-filter into `CachedProvider`; relax hit predicate

**Files:**
- Modify: `src/wave_alpha/data/cached.py`
- Test: `tests/data/test_cached.py`

- [ ] **Step 1: Read the current cached.py and existing test patterns**

```bash
uv run cat src/wave_alpha/data/cached.py
uv run cat tests/data/test_cached.py
uv run cat tests/data/test_cached_staleness.py
```

Make sure you understand: the existing `_CallCountingProvider` test fixture, the freshness tolerance constant, and how the staleness path is already covered.

- [ ] **Step 2: Write failing tests**

Append to `tests/data/test_cached.py`:

```python
def test_cached_provider_old_end_after_one_upstream_call_no_more_upstreams(tmp_path) -> None:
    """Backtest pattern: many sequential old `end` dates must trigger only ONE
    upstream call (the first one populates the cache; subsequent calls hit
    the freshness short-circuit even though their filtered slice is empty)."""
    upstream_bars = [_bar(date(2024, 5, d)) for d in (1, 2, 3, 4, 5)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    out_a = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 9))
    out_b = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 10))
    out_c = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 11))

    assert upstream.calls == 1, f"expected 1 upstream call, got {upstream.calls}"
    assert out_a == [], "old end → empty slice"
    assert out_b == [], "old end → empty slice"
    assert out_c == [], "old end → empty slice"


def test_cached_provider_first_call_populates_cache_with_full_upstream_window(tmp_path) -> None:
    """The full upstream payload must be persisted, not just the end-filtered subset."""
    upstream_bars = [_bar(date(2024, 5, d)) for d in (1, 2, 3, 4, 5)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 9))
    # Cache should now hold the full 5 bars even though caller asked for end=2020.
    assert cached._cache.max_ts("AAPL", Interval.HOURLY_4) == date(2024, 5, 5)


def test_cached_provider_filter_window_respects_start_and_end(tmp_path) -> None:
    """When the cache holds many bars, fetch must filter by start AND end."""
    upstream_bars = [_bar(date(2024, 1, d)) for d in (10, 11, 12, 13, 14)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.DAILY)  # populate cache

    out = cached.fetch(
        "AAPL", Interval.DAILY, start=date(2024, 1, 11), end=date(2024, 1, 13)
    )
    assert [b.timestamp for b in out] == [date(2024, 1, 11), date(2024, 1, 12), date(2024, 1, 13)]
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/data/test_cached.py::test_cached_provider_old_end_after_one_upstream_call_no_more_upstreams -v
```

Expected: FAIL — second and third calls hit upstream because the empty `cached` slice triggers the upstream branch.

- [ ] **Step 4: Implement the fix in `cached.py`**

Replace the body of `CachedProvider.fetch` and add `_filter_window`:

```python
def fetch(
    self,
    ticker: str,
    interval: Interval,
    start: date | None = None,
    end: date | None = None,
) -> list[Bar]:
    if self._tail_fresh_enough(ticker, interval, end):
        cached = self._cache.get(ticker, interval, start=start, end=end)
        logger.debug(
            "cache hit ticker={} interval={} bars={}", ticker, interval.value, len(cached)
        )
        return cached
    logger.debug(
        "cache miss or stale ticker={} interval={} end={}; calling upstream",
        ticker,
        interval.value,
        end,
    )
    bars = self._upstream.fetch(ticker, interval, start=start, end=end)
    if bars:
        self._cache.put(ticker, interval, bars)
        return self._filter_window(bars, start=start, end=end)
    # Upstream returned nothing — return whatever the (possibly empty) cache holds.
    return self._cache.get(ticker, interval, start=start, end=end)

@staticmethod
def _filter_window(
    bars: list[Bar], *, start: date | None, end: date | None
) -> list[Bar]:
    if start is not None:
        bars = [b for b in bars if b.timestamp >= start]
    if end is not None:
        bars = [b for b in bars if b.timestamp <= end]
    return bars
```

The hit-predicate change is the dropped `cached and …` conjunction — `_tail_fresh_enough` is now the sole gate. The upstream success path now filters its return value (since after Task A2, upstream will return unfiltered bars).

- [ ] **Step 5: Run all data tests to verify**

```bash
uv run pytest tests/data/ -v
```

Expected: PASS for all new tests; PASS for all existing tests (including `test_cached_provider_does_not_persist_empty_results` and the staleness suite).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/data/cached.py tests/data/test_cached.py
git commit -m "$(cat <<'EOF'
fix(data): cache hit predicate ignores empty filtered slice

Old-end backtest fetches now short-circuit on cache freshness alone,
returning the (often empty) filtered slice without re-hitting upstream.
Eliminates O(N) wasted yfinance calls on backtests reaching back past
yfinance's 730-day intraday window.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Drop the post-fetch `end`-filter from `YahooProvider`

**Files:**
- Modify: `src/wave_alpha/data/yahoo.py:84-85`
- Test: `tests/data/test_yahoo.py`

- [ ] **Step 1: Read the current yahoo.py and existing tests**

```bash
uv run cat src/wave_alpha/data/yahoo.py
uv run cat tests/data/test_yahoo.py
```

- [ ] **Step 2: Write a failing test**

Append to `tests/data/test_yahoo.py`:

```python
def test_yahoo_returns_unfiltered_window(monkeypatch) -> None:
    """YahooProvider must return whatever yfinance gave it — caller filters."""
    import pandas as pd

    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [1000, 1000],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )

    def fake_download(*args, **kwargs):
        return df

    monkeypatch.setattr("wave_alpha.data.yahoo.yf.download", fake_download)

    bars = YahooProvider().fetch("AAPL", Interval.DAILY, end=date(2020, 1, 1))

    # The end-filter is now a CachedProvider responsibility — YahooProvider
    # returns the full upstream payload regardless of `end`.
    assert len(bars) == 2
    assert bars[0].timestamp == date(2024, 1, 1)
    assert bars[1].timestamp == date(2024, 1, 2)
```

(If `YahooProvider` is not already imported in the test file, add `from wave_alpha.data.yahoo import YahooProvider` and `from wave_alpha.domain import Interval` at the top.)

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/data/test_yahoo.py::test_yahoo_returns_unfiltered_window -v
```

Expected: FAIL — `YahooProvider` currently filters to `b.timestamp <= end` and returns `[]`.

- [ ] **Step 4: Drop the `end`-filter from `yahoo.py`**

Remove lines 84-85 of `src/wave_alpha/data/yahoo.py`:

```python
        bars = bars_from_dataframe(df, interval=interval)
        if end:
            bars = [b for b in bars if b.timestamp <= end]
        return bars
```

becomes:

```python
        return bars_from_dataframe(df, interval=interval)
```

Update the docstring/comment block above to reflect that `end` is honored only on the wire (when paired with `start`); post-filtering is now the cache layer's job.

- [ ] **Step 5: Run all data tests**

```bash
uv run pytest tests/data/ -v
```

Expected: PASS — including the new test, the existing tests, AND the Phase A tests (which depended on this behavior).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/data/yahoo.py tests/data/test_yahoo.py
git commit -m "$(cat <<'EOF'
fix(data): yahoo provider returns unfiltered window

End-filtering is now a CachedProvider responsibility, applied uniformly
to both cache-hit and cache-miss return paths. yfinance's 730-day
intraday payload now lands in the cache verbatim.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase A Review Boundary

Before continuing, run the full suite once:

```bash
uv run pytest -x
uv run ruff check .
```

Expected: PASS, no lint warnings on changed files.

---

## Phase B — Trade-layer surfacing

### Task B1: Add `tf_count` to `TradeRecord` and populate it

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Test: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Read the current trade_layer.py and existing tests**

```bash
uv run cat src/wave_alpha/backtest/trade_layer.py | head -50
uv run grep -n "tf_count\|fallback_used\|_last_coherence" src/wave_alpha/backtest/trade_layer.py
```

- [ ] **Step 2: Write failing tests**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_trade_record_tf_count_3_when_4h_present() -> None:
    """When the analysis has 3-TF coherence (no fallback), record.tf_count == 3."""
    # Use the existing synthetic-provider helpers in this test module.
    # The helper that drives a successful trade through a target-hit must,
    # by default, supply non-empty 4h bars so the coherence module returns
    # fallback_used=None.
    rules = TradeRules(min_confidence=0.0)
    evaluator = TradeLayerEvaluator(provider=_provider_with_3tf(), rules=rules)
    result = evaluator.evaluate("TEST", start=_START, end=_END, step=1)

    assert result.trades, "fixture must produce at least one trade"
    assert all(t.tf_count == 3 for t in result.trades)


def test_trade_record_tf_count_2_when_4h_missing() -> None:
    """When 4h bars are empty (post-fix backtest behavior for old as_ofs),
    coherence falls back to W↔D and record.tf_count == 2."""
    rules = TradeRules(min_confidence=0.0)
    evaluator = TradeLayerEvaluator(provider=_provider_with_no_4h(), rules=rules)
    result = evaluator.evaluate("TEST", start=_START, end=_END, step=1)

    assert result.trades
    assert all(t.tf_count == 2 for t in result.trades)
```

The `FixedBarsProvider` in `tests/backtest/_synthetic.py` already keys bars by `Interval`. The "3-TF" provider populates `bars_by_interval` for all three intervals; the "no-4h" provider populates only weekly + daily and omits `Interval.HOURLY_4` (a missing key returns `[]` via the existing `.get(interval, [])`). Concrete factories:

```python
def _provider_with_3tf() -> FixedBarsProvider:
    daily = daily_bars([...])  # use the same OHLC sequence the existing trade-layer
    weekly = daily               # tests use to drive a clean target-hit (copy from
    hourly_4 = daily             # the closest existing test in this file)
    return FixedBarsProvider(bars_by_interval={
        Interval.WEEKLY: weekly,
        Interval.DAILY: daily,
        Interval.HOURLY_4: hourly_4,
    })


def _provider_with_no_4h() -> FixedBarsProvider:
    daily = daily_bars([...])  # same daily series as above
    weekly = daily
    return FixedBarsProvider(bars_by_interval={
        Interval.WEEKLY: weekly,
        Interval.DAILY: daily,
        # 4h intentionally omitted — coherence will set fallback_used="no_4h_history"
    })
```

Pick the `daily_bars(...)` argument that an existing test in this file uses to drive a successful target-hit signal (look for the simplest passing test that produces a `target` exit and copy its OHLC tuples).

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_trade_layer.py -k "tf_count" -v
```

Expected: FAIL — `TradeRecord` has no `tf_count` field; `AttributeError`.

- [ ] **Step 4: Add `tf_count` to `TradeRecord` and side-channel through `_signal_at`**

In `src/wave_alpha/backtest/trade_layer.py`:

a) Add field to the dataclass (after `confidence: float` on line 65):

```python
@dataclass(frozen=True)
class TradeRecord:
    # ... existing fields ...
    confidence: float
    tf_count: Literal[2, 3]
```

b) In `_signal_at` (around line 218-221), stash `_last_tf_count`:

```python
def _signal_at(self, ticker: str, as_of: date) -> TradeSignal | None:
    analysis = run_analysis(
        AnalyzeRequest(ticker=ticker, as_of=as_of, rules=self.rules),
        provider=self.provider,
        llm_mode=LLMMode.DISABLED,
    )
    if not analysis.signals:
        return None
    coh_score = analysis.coherence.score if analysis.coherence else 0.0
    fallback = analysis.coherence.fallback_used if analysis.coherence else "no_4h_history"
    self._last_coherence_score = coh_score
    self._last_tf_count = 2 if fallback == "no_4h_history" else 3
    return analysis.signals[0]
```

The default `fallback="no_4h_history"` for the (defensive) `coherence is None` case keeps tf_count=2 conservative — there's no 4h to attribute to a missing coherence object.

c) In `_make_record` (around line 374-410) and `_make_open_at_end_record` (around line 410-450), pass `tf_count=self._last_tf_count` into the `TradeRecord(...)` constructor call alongside the existing `coherence_score=` / `coherence_bucket=` lines.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/backtest/test_trade_layer.py -k "tf_count" -v
uv run pytest tests/backtest/ -v
```

Expected: PASS for new tests; PASS for all pre-existing trade-layer tests (the new `tf_count` field carries the default Literal value through every existing test path).

If a pre-existing test breaks because `TradeRecord(...)` was constructed by hand with positional args or a strict shape check, update those construction sites to include `tf_count=3` (the default for "all 3 TFs present" — consistent with how the existing fixtures work).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "$(cat <<'EOF'
feat(backtest): TradeRecord carries tf_count for coverage analysis

tf_count is 3 when coherence had W+D+4h, 2 when 4h was missing
(coherence.fallback_used == "no_4h_history"). Threaded through the
trade-layer evaluator's existing _last_coherence_score side-channel.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: `coverage_4h` and `by_tf_count` on `TradeLayerResult`

**Files:**
- Modify: `src/wave_alpha/backtest/trade_layer.py`
- Test: `tests/backtest/test_trade_layer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/backtest/test_trade_layer.py`:

```python
def test_coverage_4h_with_mixed_records() -> None:
    """coverage_4h = fraction of CLOSED trades with tf_count == 3."""
    rules = TradeRules(min_confidence=0.0)
    # Hand-construct a result with a known mix.
    result = TradeLayerResult(trades=[
        _record(tf_count=3, exit_reason="target"),
        _record(tf_count=3, exit_reason="stop"),
        _record(tf_count=2, exit_reason="target"),
        _record(tf_count=3, exit_reason="open_at_end"),  # excluded
    ])
    assert result.coverage_4h == pytest.approx(2 / 3)


def test_coverage_4h_no_closed_trades_returns_zero() -> None:
    result = TradeLayerResult(trades=[])
    assert result.coverage_4h == 0.0


def test_by_tf_count_breakdown_shape_and_keys() -> None:
    """Mirrors by_direction shape: dict[int, dict[str, float]] with the
    standard inner keys; sorted ascending by key."""
    result = TradeLayerResult(trades=[
        _record(tf_count=2, exit_reason="target", realized_R=1.0),
        _record(tf_count=2, exit_reason="stop", realized_R=-1.0),
        _record(tf_count=3, exit_reason="target", realized_R=2.0),
    ])
    breakdown = result.by_tf_count()
    assert list(breakdown.keys()) == [2, 3]
    expected_inner = {"sample_size", "hit_rate", "avg_R", "expectancy", "profit_factor"}
    assert set(breakdown[2].keys()) == expected_inner
    assert breakdown[2]["sample_size"] == 2
    assert breakdown[3]["sample_size"] == 1
```

The `_record(...)` helper is whatever lightweight factory the file already uses for hand-building `TradeRecord` instances (look near the top of `test_trade_layer.py` for a `def _record(`); if none exists, add one that fills in sensible defaults for every field including `tf_count` and `realized_R`. Keep it lean — just enough to drive the tests.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_trade_layer.py -k "coverage_4h or by_tf_count" -v
```

Expected: FAIL — `coverage_4h` and `by_tf_count` don't exist yet.

- [ ] **Step 3: Implement `coverage_4h` and `by_tf_count`**

Add to `TradeLayerResult` (in `src/wave_alpha/backtest/trade_layer.py`, near the other breakdown methods around line 145):

```python
@property
def coverage_4h(self) -> float:
    """Fraction of closed trades whose signal had 4h coherence available."""
    closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
    if not closed:
        return 0.0
    return sum(1 for t in closed if t.tf_count == 3) / len(closed)

def by_tf_count(self) -> dict[int, dict[str, float]]:
    groups: dict[int, list[TradeRecord]] = {}
    for t in self.trades:
        groups.setdefault(t.tf_count, []).append(t)
    return {tf: _metrics_for(v) for tf, v in sorted(groups.items())}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/backtest/test_trade_layer.py -v
```

Expected: PASS for new tests and all existing trade-layer tests.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/backtest/trade_layer.py tests/backtest/test_trade_layer.py
git commit -m "$(cat <<'EOF'
feat(backtest): coverage_4h property and by_tf_count breakdown

coverage_4h reports the fraction of closed trades whose signal-time
coherence used all three timeframes; by_tf_count slices the standard
metrics block by 2-TF vs 3-TF for ablation analysis.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: Markdown report — `**4h coverage:**` header line

**Files:**
- Modify: `src/wave_alpha/backtest/report.py:75-104`
- Test: `tests/backtest/test_report.py`

- [ ] **Step 1: Read the current report.py and report tests**

```bash
uv run cat src/wave_alpha/backtest/report.py
uv run cat tests/backtest/test_report.py
```

Locate the header block of `render_trade_markdown` (the lines that emit `**Closed trades:**` and `**Open at end:**`).

- [ ] **Step 2: Write failing tests**

Append to `tests/backtest/test_report.py`:

```python
def test_render_trade_markdown_includes_4h_coverage_when_trades_exist() -> None:
    """`**4h coverage:**` line is rendered with a percentage."""
    cfg = BacktestConfig(universe=["TEST"], start=date(2024, 1, 1), end=date(2024, 12, 31))
    rules = TradeRules()
    result = TradeLayerResult(trades=[
        _record(tf_count=3, exit_reason="target"),
        _record(tf_count=2, exit_reason="stop"),
    ])
    md = render_trade_markdown(result, cfg=cfg, rules=rules)
    assert "**4h coverage:**" in md
    assert "50% (1 / 2 closed trades had 4h coherence)" in md


def test_render_trade_markdown_4h_coverage_na_when_no_closed() -> None:
    cfg = BacktestConfig(universe=["TEST"], start=date(2024, 1, 1), end=date(2024, 12, 31))
    rules = TradeRules()
    result = TradeLayerResult(trades=[])
    md = render_trade_markdown(result, cfg=cfg, rules=rules)
    assert "**4h coverage:** n/a" in md
```

`_trade_record` and any imports needed (`BacktestConfig`, `TradeLayerResult`, `TradeRules`, `render_trade_markdown`) should be reused from existing patterns in `test_report.py`.

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/backtest/test_report.py -k "4h_coverage" -v
```

Expected: FAIL — neither line exists in the rendered output.

- [ ] **Step 4: Add the coverage line in `render_trade_markdown`**

In `src/wave_alpha/backtest/report.py`, immediately after the `**Open at end:**` line:

```python
lines.append(f"**Closed trades:** {result.closed_trades}")
lines.append(f"**Open at end:** {result.open_at_end}")
if result.closed_trades > 0:
    cov_pct = int(round(result.coverage_4h * 100))
    n_3tf = sum(1 for t in result.trades if t.exit_reason != "open_at_end" and t.tf_count == 3)
    lines.append(
        f"**4h coverage:** {cov_pct}% ({n_3tf} / {result.closed_trades} "
        f"closed trades had 4h coherence)"
    )
else:
    lines.append("**4h coverage:** n/a")
lines.append("")
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/backtest/test_report.py -v
```

Expected: PASS for new tests; PASS for the existing `test_render_trade_markdown` deterministic round-trip if it asserted on an exact substring around the changed lines, you may need to update its expected fragment.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/backtest/report.py tests/backtest/test_report.py
git commit -m "$(cat <<'EOF'
feat(backtest): trade-layer report shows 4h coverage in header

Surfaces the fraction of closed trades whose signal-time coherence had
all three timeframes available, so backtests reaching past yfinance's
730-day 4h limit are honestly interpretable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B4: JSON sidecar + CSV ledger get the new fields

**Files:**
- Modify: `src/wave_alpha/cli/app.py:244-289`
- Test: `tests/cli/test_backtest_trade.py`

- [ ] **Step 1: Read the existing CLI test for `backtest trade`**

```bash
uv run cat tests/cli/test_backtest_trade.py
```

Identify the CLI invocation pattern and any existing JSON / CSV assertions.

- [ ] **Step 2: Write failing tests**

Append to `tests/cli/test_backtest_trade.py`:

```python
def test_backtest_trade_json_sidecar_includes_coverage_4h_and_by_tf_count(tmp_path) -> None:
    """JSON sidecar carries the new coverage_4h and by_tf_count keys."""
    # Reuse the existing fixture / runner pattern in this file.
    json_path = tmp_path / "trade.json"
    md_path = tmp_path / "trade.md"
    _invoke_backtest_trade(  # whatever invocation helper this file already uses
        json_out=json_path, out=md_path, ...,
    )
    payload = json.loads(json_path.read_text())
    assert "coverage_4h" in payload["headline"]
    assert "by_tf_count" in payload
    assert isinstance(payload["headline"]["coverage_4h"], float)


def test_backtest_trade_csv_ledger_includes_tf_count_column(tmp_path) -> None:
    """CSV ledger header ends with tf_count; rows carry the integer value."""
    csv_path = tmp_path / "trades.csv"
    md_path = tmp_path / "trade.md"
    _invoke_backtest_trade(trades_csv=csv_path, out=md_path, ...)
    rows = list(csv.DictReader(csv_path.open()))
    if rows:  # the smoke fixture should produce trades; skip-if-empty guards regressions in fixtures
        assert "tf_count" in rows[0]
        assert rows[0]["tf_count"] in {"2", "3"}
```

If the existing test file uses pytest fixtures or helper functions to invoke the CLI, mirror them. Otherwise, instantiate the test runner inline:

```python
from typer.testing import CliRunner
from wave_alpha.cli.app import app

runner = CliRunner()
result = runner.invoke(app, [
    "backtest", "trade",
    "--universe", str(universe_path),
    "--start", "2024-06-01",
    "--end", "2024-12-31",
    "--out", str(md_path),
    "--json", str(json_path),
    "--trades-csv", str(csv_path),
])
assert result.exit_code == 0, result.output
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/cli/test_backtest_trade.py -k "coverage_4h or tf_count" -v
```

Expected: FAIL — `coverage_4h` not in payload; `tf_count` not in CSV header (the latter actually passes for free once `TradeRecord.tf_count` exists, since CSV fields come from `asdict(record).keys()`; verify that empirically).

- [ ] **Step 4: Update the JSON sidecar payload in `cli/app.py`**

In `backtest_trade` (around line 244-269), inside the `headline` dict and at the top level of `payload`:

```python
"headline": {
    "closed_trades": result.closed_trades,
    "open_at_end": result.open_at_end,
    "hit_rate": result.hit_rate,
    "avg_R": result.avg_R,
    "expectancy": result.expectancy,
    "profit_factor": (
        None if result.profit_factor == float("inf") else result.profit_factor
    ),
    "max_drawdown_R": result.max_drawdown_R,
    "coverage_4h": result.coverage_4h,
},
"by_direction": result.by_direction(),
"by_pattern": result.by_pattern(),
"by_coherence_bucket": result.by_coherence_bucket(),
"by_tf_count": {str(k): v for k, v in result.by_tf_count().items()},
"by_year": {str(k): v for k, v in result.by_year().items()},
```

The CSV writer needs no change — `asdict(t).keys()` will pick up the new `tf_count` field automatically since dataclass fields are ordered.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/cli/test_backtest_trade.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_backtest_trade.py
git commit -m "$(cat <<'EOF'
feat(cli): backtest trade sidecars expose 4h coverage and tf_count

JSON sidecar gains coverage_4h on headline and a top-level by_tf_count
breakdown. CSV ledger picks up tf_count as the trailing column via the
existing asdict(record).keys() flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B Review Boundary

```bash
uv run pytest -x
uv run ruff check .
```

Expected: PASS, no warnings.

---

## Phase C — CLI warning + docs

### Task C1: `_warn_if_4h_window_partial` helper, wired into the three subcommands

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Test: `tests/cli/test_backtest_trade.py`

- [ ] **Step 1: Read the relevant CLI command bodies**

```bash
uv run grep -n "^@backtest_app.command\|^def backtest_count\|^def backtest_pivot\|^def backtest_trade" src/wave_alpha/cli/app.py
```

- [ ] **Step 2: Write failing tests**

Append to `tests/cli/test_backtest_trade.py` (covers all three subcommands; rename the file later if scope grows):

```python
from datetime import date, timedelta

def test_backtest_trade_warns_when_start_outside_4h_window(tmp_path, capsys) -> None:
    """Stderr should contain the 4h-history warning when start < today − 730d."""
    md_path = tmp_path / "trade.md"
    runner = CliRunner()
    result = runner.invoke(app, [
        "backtest", "trade",
        "--universe", str(_universe_with_one_synthetic_ticker(tmp_path)),
        "--start", "2018-01-01",
        "--end", "2018-12-31",
        "--out", str(md_path),
    ], mix_stderr=False)
    assert "4h timeframe data is unavailable" in result.stderr


def test_backtest_trade_silent_when_start_inside_4h_window(tmp_path) -> None:
    md_path = tmp_path / "trade.md"
    runner = CliRunner()
    start = (date.today() - timedelta(days=400)).isoformat()
    end = (date.today() - timedelta(days=30)).isoformat()
    result = runner.invoke(app, [
        "backtest", "trade",
        "--universe", str(_universe_with_one_synthetic_ticker(tmp_path)),
        "--start", start,
        "--end", end,
        "--out", str(md_path),
    ], mix_stderr=False)
    assert "4h timeframe data is unavailable" not in result.stderr


def test_backtest_count_warns_when_start_outside_4h_window(tmp_path) -> None:
    out_path = tmp_path / "count.md"
    runner = CliRunner()
    result = runner.invoke(app, [
        "backtest", "count",
        "--universe", str(_universe_with_one_synthetic_ticker(tmp_path)),
        "--start", "2018-01-01",
        "--end", "2018-12-31",
        "--out", str(out_path),
    ], mix_stderr=False)
    assert "4h timeframe data is unavailable" in result.stderr


def test_backtest_pivot_warns_when_start_outside_4h_window(tmp_path) -> None:
    out_path = tmp_path / "pivot.md"
    runner = CliRunner()
    result = runner.invoke(app, [
        "backtest", "pivot",
        "--universe", str(_universe_with_one_synthetic_ticker(tmp_path)),
        "--initial", "2018-01-01",
        "--final", "2018-06-01",  # adjust flag names to match the actual signature of backtest_pivot
        "--out", str(out_path),
    ], mix_stderr=False)
    assert "4h timeframe data is unavailable" in result.stderr
```

For `backtest_pivot`, double-check the exact CLI flag names by reading `cli/app.py`'s `backtest_pivot` signature; the spec assumes the warning fires on the user-supplied start date. If `backtest_pivot` doesn't take an explicit `--start`, drive the warning off whichever earliest date it does take (`--initial` is likely).

The `_universe_with_one_synthetic_ticker(tmp_path)` helper writes a minimal 1-line universe file to `tmp_path`. If a similar helper already exists in `tests/cli/`, reuse it.

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/cli/test_backtest_trade.py -k "warns or silent" -v
```

Expected: FAIL — no warning emitted.

- [ ] **Step 4: Implement the warning helper and wire it**

In `src/wave_alpha/cli/app.py`, add at module scope (near the other module-private helpers):

```python
_YF_4H_HISTORY_DAYS = 730


def _warn_if_4h_window_partial(start: date) -> None:
    cutoff = date.today() - timedelta(days=_YF_4H_HISTORY_DAYS)
    if start < cutoff:
        typer.echo(
            f"[warn] 4h timeframe data is unavailable from yfinance before "
            f"~{cutoff.isoformat()}. Coherence will degrade to 2-timeframe "
            f"(weekly + daily) for as_of dates before that cutoff.",
            err=True,
        )
```

Make sure `from datetime import timedelta` is imported (it likely already is via `date`).

Call the helper at the top of each command body — once the parsed start date is available:

- `backtest_count`: after `start = date.fromisoformat(start)`, before invoking the runner.
- `backtest_pivot`: same idea — call against `initial` (or whichever flag is the earliest user-supplied date).
- `backtest_trade`: after `cfg = BacktestConfig(...)`, call `_warn_if_4h_window_partial(cfg.start)`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/cli/ -v
```

Expected: PASS for new tests; PASS for all existing CLI tests.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_backtest_trade.py
git commit -m "$(cat <<'EOF'
feat(cli): warn when backtest window crosses 4h availability boundary

Stderr warning fires from backtest_count, backtest_pivot, and
backtest_trade when start < today - 730d, naming the cutoff date and
explaining the 2-timeframe degradation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: README 4h-history subsection

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the v0.5 backtest section in README**

```bash
uv run grep -nE "^## |^### |backtest" README.md | head -40
```

- [ ] **Step 2: Add the subsection**

Find the trade-layer backtest section (or the general v0.5 backtest commands section) and add a subsection like:

```markdown
### 4h timeframe history

The 4h timeframe is resampled from yfinance's 1h-resolution data,
which is capped at ~730 days of history. Backtests reaching back
further than that will print a warning at start-up:

```
[warn] 4h timeframe data is unavailable from yfinance before
~2024-05-04. Coherence will degrade to 2-timeframe (weekly + daily)
for as_of dates before that cutoff.
```

This is informational, not an error. The pipeline degrades gracefully:
coherence is computed on weekly + daily alone for as_ofs before the
cutoff. The trade-layer report's `**4h coverage:**` header line
reports what fraction of closed trades had full 3-timeframe coherence.

To run a backtest with full 3-TF coherence on every as_of, supply
`--start` no earlier than ~2 years before today.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): explain 4h-history limit in backtest section

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C Review Boundary

```bash
uv run pytest -x
uv run ruff check .
```

Expected: PASS.

---

## Phase D — Version bump

### Task D1: Bump to v0.5.3 and refresh the lock

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Bump the version**

```bash
uv run grep -n "^version" pyproject.toml
```

Edit `pyproject.toml`:

```toml
version = "0.5.3"
```

- [ ] **Step 2: Refresh the lock**

```bash
uv lock
```

- [ ] **Step 3: Verify the bump**

```bash
uv run wave-alpha version
```

Expected: prints `0.5.3` (or whatever format `wave-alpha version` produces today; verify by running first against the current code).

- [ ] **Step 4: Final test pass**

```bash
uv run pytest -x
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.5.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D Review Boundary (final)

```bash
uv run pytest
uv run ruff check .
git log --oneline -10
```

Expected: full suite green, lint clean, ~6-8 commits in the new branch (cache fix × 2, trade-layer surfacing × 4, CLI warning + docs × 2, version bump).

---

## Self-Review Checklist (controller)

- [ ] **Spec coverage:** every spec section has a task — S1 (cache, §3) → Tasks A1+A2; S2 (report transparency, §4) → Tasks B1+B2+B3+B4; S3 (CLI warning + docs, §5) → Tasks C1+C2; tests §7 covered inline within each task; lookahead §6 has no implementation (the synthetic-leak test §7.5-equivalent is the existing `test_lookahead_guard.py` per spec); roadmap impact §9 is documentation-only; version bump §10 → Task D1.
- [ ] **Placeholder scan:** every step has actual code or commands. The `_invoke_backtest_trade(...)` placeholder in B4 is acknowledged with explicit guidance ("If the existing test file uses pytest fixtures…otherwise instantiate the runner inline:") and a complete inline alternative.
- [ ] **Type consistency:** `tf_count: Literal[2, 3]` in B1 matches the test asserts (`{"2", "3"}` after CSV string round-trip in B4). `coverage_4h` is a float property in B2, asserted as float in B4. `by_tf_count()` returns `dict[int, dict[str, float]]` in B2; the JSON sidecar coerces keys to str (`{str(k): v for k, v in result.by_tf_count().items()}` in B4) — same pattern as `by_year`, intentional.
- [ ] **No phantom symbols:** `_filter_window` defined in A1 and consumed in A1 only; `_last_tf_count` set in B1 and consumed in B1. `_warn_if_4h_window_partial` defined in C1 and consumed in C1.
- [ ] **TDD:** every code task is failing-test-first; commits at the green checkpoint.
- [ ] **Frequent commits:** ~8 commits across 4 phases, each self-contained and revertible.
