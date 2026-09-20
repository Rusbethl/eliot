---
title: 4h cache strategy under yfinance's 730-day intraday limit — design
date: 2026-05-04
status: approved (brainstorm complete; pending implementation plan)
author: Alex Chen
related:
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §7 coherence, §8 backtest
  - docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md
---

# 4h cache strategy under yfinance's 730-day intraday limit — design

A small but consequential fix. The provider/cache pair currently burns one
yfinance call per backtest as_of for the 4h timeframe whenever `end` falls
outside yfinance's ~730-day intraday history window — a configuration that
applies to **every** as_of date in any backtest reaching back more than
~24 months. The result, on the curated 50-ticker × 5-year window, is a
backtest that would take ≥ 18 hours and rate-limit the upstream long
before it finished.

This spec fixes the cache mechanics so old-window 4h fetches stop wasting
upstream calls, and adds two small surfaces of transparency so the
resulting partial-coherence backtests remain interpretable.

## 1. Motivation

Today's flow for `pipeline.run_analysis(as_of=2020-01-09)` on the 4h
timeframe:

1. `CachedProvider.fetch(ticker, HOURLY_4, end=2020-01-09)` → cache.get
   filters by `ts ≤ 2020-01-09` → `[]` (the 4h cache only contains a
   sliver of recent data).
2. `if cached and _tail_fresh_enough(end)` is False because `cached` is
   empty → falls to upstream.
3. `YahooProvider` fetches `period="730d"` → returns ~480 bars in the
   range 2024-05 → 2026-05.
4. `YahooProvider:84` filters `b.timestamp ≤ 2020-01-09` → returns `[]`.
5. `CachedProvider:54`'s `if bars: self._cache.put(...)` is skipped
   because `bars` is empty.
6. Returns `[]`. Cache is unchanged. Next call repeats steps 1–6.

For a 5-year backtest at step=1, this loops 50 tickers × ~1260 business
days = ~63,000 wasted upstream calls. yfinance latency is ~0.5s per call
plus rate-limit risk.

The downstream coherence math already degrades correctly when 4h is
empty (`coherence/score.py:78` returns
`fallback_used="no_4h_history"` and uses W↔D pairwise score). But the
cache layer keeps trying upstream forever, and nothing surfaces the
fact that 4h was missing for most of the backtest window.

## 2. Goals and non-goals

**Goals**

- Eliminate redundant upstream calls when 4h data does not exist for an
  old `end` date — at most one upstream attempt per `(ticker, interval)`
  per backtest run.
- Preserve the existing live-mode path: an analyzer call at
  `as_of=today` with a warm cache must still serve cached bars and only
  go to upstream when the cache tail is stale.
- Surface 4h coverage in trade-layer reports so ablation analysis can
  account for partial coherence across the backtest window.
- Warn users when invoking a backtest with a window that crosses the 4h
  availability boundary, so the partial-coverage result isn't a
  surprise.

**Non-goals**

- Introduce a new intraday data source. Cap at yfinance for now; a
  multi-year 4h source is its own spec (deferred Option B from the
  brainstorm).
- Recalibrate coherence bucket thresholds for 2-TF vs 3-TF agreement.
  The existing thresholds (full / partial / disagreement at 0.7 / 0.4)
  are kept as-is; the spec only surfaces tf_count alongside, so the
  ablation analyst can decide.
- Backfill historical 4h via daily-bar resampling. Daily-bar resampling
  is not 4h, and conflating them would corrupt comparability with
  warm-window 4h. Out of scope.
- Add a negative-cache table that records "no data exists pre-X" per
  ticker. The fix below makes that unnecessary: once the cache holds
  any 4h bars, the freshness check short-circuits.

## 3. S1 — Cache mechanics fix

### 3.1 Root-cause summary

Two coupled bugs in the provider/cache contract:

- **Upstream end-filter prevents cache population.**
  `YahooProvider.fetch:84-85` filters bars to `b.timestamp <= end`
  before returning. When `end` is older than yfinance's 730-day
  intraday window, every 4h fetch returns `[]` even though yfinance
  successfully returned 480 recent bars.
- **Cache-hit predicate requires non-empty `cached`.**
  `CachedProvider.fetch:42`'s `if cached and _tail_fresh_enough(end)`
  treats an empty cache slice as a miss. After the fix above, the
  cache will (correctly) hold 480 bars from 2024-05 onward — but for an
  `end` far before that range, `cache.get(end=2020-01-09)` still
  filters down to `[]`. Without the predicate change, we'd loop right
  back to upstream.

### 3.2 Fix

Move the `end`-filter responsibility from `YahooProvider` to
`CachedProvider`, and relax the cache-hit predicate.

**`YahooProvider.fetch`:**

- Remove the post-fetch `bars = [b for b in bars if b.timestamp <= end]`
  filter. Return whatever yfinance returned (empty if yfinance returned
  empty; otherwise the full window).
- Keep the existing `start`/`end`/`period` argument-shaping logic
  unchanged. yfinance's own start/end clamps when both are supplied.

**`CachedProvider.fetch`:**

- Always end-filter on the way out, whether the bars came from the
  cache or from upstream:

  ```python
  def _filter_window(self, bars: list[Bar],
                     start: date | None, end: date | None) -> list[Bar]:
      if start is not None:
          bars = [b for b in bars if b.timestamp >= start]
      if end is not None:
          bars = [b for b in bars if b.timestamp <= end]
      return bars
  ```

- Hit predicate: `if self._tail_fresh_enough(ticker, interval, end):`
  — drop the `cached` truthiness check. After upstream has been
  consulted at least once, `max_ts` is set; subsequent calls hit the
  fast path and return the appropriately-filtered slice (which may be
  empty for old `end`s).
- On upstream call: pass `start`/`end` through to upstream as today
  (yfinance ignores `end` in period-mode anyway), then run results
  through `_filter_window` before returning to the caller. Cache.put
  receives the **unfiltered** upstream result so the cache is populated
  with as much as yfinance gave us.

After fix, the call sequence for the same backtest:

1. First call `fetch(ticker, 4h, end=2020-01-09)`: cache empty,
   `max_ts is None`, predicate False → upstream returns ~480 recent
   bars → cache.put stores all 480 → `_filter_window(end=2020-01-09)`
   returns `[]`.
2. Second call `fetch(ticker, 4h, end=2020-01-10)`: `max_ts=2026-05-04`,
   `(end - max_ts) ≈ -2300 days ≤ 1 day` → predicate True → cache.get
   filtered to `ts ≤ 2020-01-10` → `[]` returned, no upstream call.
3. All subsequent old-window calls hit the cache path. Total upstream
   calls per ticker per backtest run: **1**, down from ~1260.

### 3.3 Live-mode preservation

For a live call `fetch(ticker, 4h, end=today)`:

- Cold cache: predicate False (`max_ts is None`) → upstream → cache
  populated → return filtered slice ending at today. Same as today.
- Warm cache, fresh tail (`max_ts ≥ today − 1d`): predicate True →
  cache.get returns tail-included slice. Same as today.
- Warm cache, stale tail (`max_ts < today − 1d`): predicate False →
  upstream refresh. Same as today.

No behavioral change for live mode.

### 3.4 Edge case: upstream returns `[]`

If upstream returns `[]` (transient outage, ticker delisted), we still
do not call `cache.put` — `OHLCVCache.put:39` already early-returns on
empty input. `max_ts` stays `None`. The next call retries upstream.
This matches today's resilience semantics; not a regression.

### 3.5 Edge case: ticker has no 4h data ever

For tickers where yfinance refuses 4h data entirely (delisted, OTC,
etc.), upstream returns `[]` indefinitely. There's no negative-cache
table — every backtest as_of will retry upstream once per ticker.
This is the same as the live-mode outage case above; the budget is one
yfinance call per ticker per backtest run, not per as_of, so the cost
is bounded at ~50 calls (the universe size). Acceptable.

## 4. S2 — Report transparency

### 4.1 New field on `TradeRecord`

```python
tf_count: Literal[2, 3]   # 2 if coherence.fallback_used == "no_4h_history" else 3
```

Derived in `TradeLayerEvaluator._open_position` (or wherever the trade
record is constructed) from `result.coherence.fallback_used`. The
existing `coherence_score` and `coherence_bucket` fields are computed
from whatever score the coherence module produced — 2-TF or 3-TF — and
are kept unchanged.

### 4.2 New `TradeLayerResult` API

```python
@property
def coverage_4h(self) -> float:
    """Fraction of closed trades whose signal had 4h coherence available.
    Returns 0.0 when there are no closed trades.
    """

def by_tf_count(self) -> dict[int, dict[str, float]]:
    """Same inner shape as the other breakdowns:
    {sample_size, hit_rate, avg_R, expectancy, profit_factor}.
    Keys: int 2 and 3, sorted ascending. Bucket missing if no trades."""
```

`coverage_4h` is computed over closed trades only (consistent with
headline metrics). `by_tf_count` participates in the JSON sidecar; not
rendered in markdown by default.

### 4.3 Markdown report changes

A new line in the header block:

```
**Closed trades:** 412
**Open at end:** 7
**4h coverage:** 18% (76 / 412 closed trades had 4h coherence)
```

If `closed_trades == 0`, render `**4h coverage:** n/a`. The existing
sections (`Headline`, `by direction`, `by pattern`,
`by coherence bucket`, `by year`) are unchanged.

### 4.4 JSON sidecar changes

Add `"coverage_4h": <float>` to the headline block and `"by_tf_count":
{...}` as a new top-level key alongside the other breakdowns.

### 4.5 CSV ledger changes

Add `tf_count` as a new column. Existing columns and ordering are
unchanged; new column appears at the end of the row to keep diff noise
minimal in any external tooling that already consumes the CSV.

### 4.6 Why no per-bucket split in markdown

Adding a `by_coherence_bucket × tf_count` cross-tab to the markdown
report pushes the report into "stat-spreadsheet" territory and obscures
the headline numbers. Power users who want that view can pivot the CSV
or the JSON sidecar's `by_tf_count`. The markdown stays scannable.

## 5. S3 — CLI warning + docs

### 5.1 Warning helper

New module-private helper in `cli/app.py`:

```python
_YF_4H_HISTORY_DAYS = 730  # yfinance 1h-resolution intraday limit

def _warn_if_4h_window_partial(start: date) -> None:
    cutoff = date.today() - timedelta(days=_YF_4H_HISTORY_DAYS)
    if start < cutoff:
        typer.echo(
            f"[warn] 4h timeframe data is unavailable from yfinance before "
            f"~{cutoff.isoformat()}. Coherence will degrade to 2-timeframe "
            f"(weekly + daily) for as_of dates before that cutoff. "
            f"See docs/backtest-4h-cache.md for details.",
            err=True,
        )
```

The cutoff is computed at invocation time (no hardcoded date). One
warning per CLI invocation, printed to stderr so it doesn't interleave
with markdown output on stdout.

### 5.2 Where it fires

Called at the top of:

- `backtest_count` — count layer's coherence-bucket breakdown is
  affected.
- `backtest_pivot` — pivot layer doesn't compute coherence, but its
  `interval` flag accepts `4h` and would trip the same yfinance
  history limit. Fire the warning unconditionally; users running pivot
  on weekly/daily can ignore it.
- `backtest_trade` — trade-layer coverage is affected.

`analyze` and `scan` are not gated. They run at `as_of=today` (or the
user's explicit `--as-of`) and we trust the user knows what they
asked for; spamming a warning on every analyze call is too noisy.

### 5.3 README addition

A short subsection in the v0.5 backtest section of `README.md` titled
"4h timeframe history" that explains the yfinance 730-day limit, what
the warning means, and how to interpret the `4h coverage:` line in
trade-layer reports. ~15 lines.

### 5.4 Docstring updates

`OHLCVProvider.fetch` and `CachedProvider.fetch` get a one-paragraph
docstring note about the end-filter responsibility shift. No behavioral
change to document beyond what already lives in those classes.

## 6. Lookahead safety

This spec touches the data-fetching layer, which is upstream of
`PointInTimeView`. The PointInTimeView contract is unchanged: it still
hard-asserts `bar.timestamp <= as_of` and refuses to return future bars.

Two new failure modes to verify don't leak:

- **The cache might now hold 4h bars with `timestamp > as_of`** (fresh
  yfinance data fetched during a different live-mode call earlier).
  This was already true before this spec (the 1d and 1wk caches
  routinely contain bars far past any individual backtest as_of).
  PointInTimeView's filter handles it. No new exposure.
- **`_filter_window` in CachedProvider does not bypass PointInTimeView.**
  All call sites still wrap returned bars in PointInTimeView before any
  pivot/analysis work. The end-filter is a convenience to keep returned
  payloads small; correctness still rides on PointInTimeView.

The existing `tests/backtest/test_lookahead_guard.py` synthetic-leak
test continues to apply unchanged.

## 7. Tests

### 7.1 Cache-mechanics — unit

In `tests/data/test_cached.py`:

- `test_old_end_after_one_upstream_call_no_more_upstreams` — instantiate
  `CachedProvider` with a fake `OHLCVProvider` whose upstream returns
  480 bars in 2024-05 → 2026-05. Call `fetch(ticker, HOURLY_4,
  end=2020-01-09)` then `fetch(end=2020-01-10)` then
  `fetch(end=2020-01-11)`. Assert upstream was called exactly once,
  all three calls returned `[]`, and the cache holds 480 bars
  internally.
- `test_first_call_populates_cache_with_full_upstream_window` — same
  setup; after first call, assert `cache.max_ts(ticker, HOURLY_4) ==
  date(2026, 5, 4)`.
- `test_filter_window_respects_start_and_end` — cache pre-loaded with
  bars at dates spanning 2024-2026; assert `fetch(start=2025-01-01,
  end=2025-12-31)` returns exactly the bars in that range.
- `test_live_mode_unchanged_warm_cache_fresh_tail` — cache pre-loaded
  with tail `max_ts=today`; call `fetch(end=today)`; assert no upstream
  call, returns full cached set up to `today`.
- `test_live_mode_warm_cache_stale_tail_refreshes` — cache pre-loaded
  with tail `max_ts=today-3d`; call `fetch(end=today)`; assert one
  upstream call.

### 7.2 YahooProvider — unit

In `tests/data/test_yahoo.py` (extending the existing file):

- `test_yahoo_returns_unfiltered_window` — fake yf.download returning
  bars at 2024 and 2026; call `YahooProvider().fetch(ticker, HOURLY_4,
  end=2020-01-09)`; assert ALL bars are returned (the responsibility
  for the end-filter has moved out).

### 7.3 Trade-layer — `tf_count` derivation

In `tests/backtest/test_trade_layer.py`:

- `test_trade_record_tf_count_3_when_4h_present` — synthetic provider
  returns non-empty 4h bars; assert `record.tf_count == 3`.
- `test_trade_record_tf_count_2_when_4h_missing` — synthetic provider
  returns empty 4h bars (mimicking the post-fix behavior for old
  `end`s); assert `record.tf_count == 2` and
  `result.coverage_4h == 0.0`.
- `test_by_tf_count_breakdown` — mixed records (some tf_count=2, some
  tf_count=3); assert `result.by_tf_count()` returns the expected
  shape and per-bucket metrics.
- `test_coverage_4h_no_closed_trades_returns_zero` — sanity for the
  edge case.

### 7.4 Report rendering

In `tests/backtest/test_report.py` (extending):

- `test_render_includes_4h_coverage_line` — assert the rendered markdown
  contains `"**4h coverage:** "` followed by either a `%` or `n/a`.
- `test_json_sidecar_includes_coverage_4h_and_by_tf_count` — assert
  both keys present.

In `tests/cli/test_backtest_trade.py` (extending):

- `test_csv_ledger_includes_tf_count_column` — assert header row ends
  with `tf_count` and rows carry the integer value.

### 7.5 CLI warning

In `tests/cli/test_backtest_trade.py` and a new
`tests/cli/test_backtest_count_pivot.py` (or extending the existing
backtest-cli tests if a single file already covers all three subcommands):

- `test_warning_fires_when_start_old` — invoke `backtest count` with
  `--start 2020-01-01 --end 2024-12-31`; assert stderr contains
  `"4h timeframe data is unavailable"`.
- `test_no_warning_when_start_within_4h_window` — invoke with
  `--start <today−500d>`; assert stderr is silent on 4h.
- Same two tests for `backtest pivot` and `backtest trade`.

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| C1 | Should we add a multi-year 4h data source (Polygon, Alpaca, Tiingo, IBKR)? | Post-v0.5 ablation results | If the 18%-coverage backtest produces a stable signal, we don't need it. If by-tf_count splits show 3-TF cases dominate the alpha and 2-TF cases don't, we have a budget argument for paid intraday history. |
| C2 | Should `by_coherence_bucket` be cross-tabbed with `tf_count` in markdown? | Post-launch | Power users have CSV/JSON. Markdown report stays scannable. |
| C3 | Should the cache record per-ticker "earliest available date" so we never call upstream for impossibly-old data? | Not needed | The 1-call-per-ticker-per-run budget after this fix is ~50 calls. No upside to optimizing further. |
| C4 | Should weekly fetched via `period="max"` also cap at some date so the cache doesn't grow unbounded? | Post-launch | Weekly data is small (~2k rows per ticker). Daily is the bulk. Not pressing. |
| C5 | Should a `--no-4h` flag on backtest commands force coherence to 2-TF deterministically? | Post-launch | Useful for ablation isolation. Easy add but no current demand. |
| C6 | Bucket-threshold recalibration for 2-TF coherence | Post-v0.5 | The 3-TF thresholds (0.7 / 0.4) are not obviously right for 2-TF, but recalibrating requires the very ablation data this spec helps produce. Chicken-and-egg; revisit after the first ablation grid. |

## 9. Roadmap impact

This spec lands as v0.5.x and **unblocks the trade-sim sanity run on
the curated universe** (the gating step before the ablation grid spec
per `2026-05-04-trade-sim-layer-design.md` §13).

After this ships:

- The trade-sim sanity run on `--start 2020-01-01 --end 2024-12-31` is
  feasible (one yfinance call per ticker for 4h, then cache hits).
- Reports surface 4h coverage so the result is honestly interpretable.
- The ablation grid spec can proceed with a documented
  partial-coverage caveat in its by-bucket section.

Open question O5 from the main design (coherence multiplier shape) is
unaffected — this spec does not change any coherence math.

## 10. File-touch summary

All target test files exist in the repo today; this spec extends them
rather than introducing new test files (with the optional exception of
a `tests/cli/test_backtest_count_pivot.py` if the count/pivot warning
tests don't fit the existing `test_backtest_trade.py`).

**Modified:**
- `src/wave_alpha/data/yahoo.py` — drop the post-fetch `end`-filter.
- `src/wave_alpha/data/cached.py` — add `_filter_window` helper,
  simplify hit predicate to `_tail_fresh_enough(end)`, run `start`/`end`
  filter on every return path.
- `src/wave_alpha/backtest/trade_layer.py` — add `tf_count` to
  `TradeRecord`; populate from `result.coherence.fallback_used`; add
  `coverage_4h` property and `by_tf_count` method to
  `TradeLayerResult`.
- `src/wave_alpha/backtest/report.py` — render the `4h coverage:`
  header line; include `coverage_4h` and `by_tf_count` in JSON sidecar.
- `src/wave_alpha/cli/app.py` — extend the trades CSV writer in
  `backtest_trade` with the `tf_count` column; add
  `_warn_if_4h_window_partial` and call it from `backtest_count`,
  `backtest_pivot`, and `backtest_trade`.
- `tests/data/test_cached.py` — new tests per §7.1.
- `tests/data/test_yahoo.py` — new test per §7.2.
- `tests/backtest/test_trade_layer.py` — new tests per §7.3.
- `tests/backtest/test_report.py` — new tests per §7.4.
- `tests/cli/test_backtest_trade.py` (and possibly a new
  `tests/cli/test_backtest_count_pivot.py`) — new tests per §7.5.
- `README.md` — 4h-history subsection in the v0.5 backtest area.
- `pyproject.toml` — bump to v0.5.3 on merge.

---

**Status: brainstorm complete; design approved. Next step: implementation
plan via `superpowers:writing-plans`.**
