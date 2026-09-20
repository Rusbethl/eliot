---
title: trade-sim backtest layer — design
date: 2026-05-04
status: approved (brainstorm complete; pending implementation plan)
author: Alex Chen
related:
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §8, §9
---

# Trade-sim backtest layer — design

A third layer for the backtest harness, alongside the count layer and pivot
layer, that takes a `TradeRules` config and produces the §8 trade-economics
table: hit rate, avg R, expectancy, profit factor, max drawdown, sample size,
plus breakdowns by direction, pattern, coherence bucket, and year.

Scope: **single-config sim only.** The §8 ablation grid sweep (2×2×3×4 = 48
cells) is a thin orchestration layer over this sim and lands as a separate
spec once this one is shipped and metrics on the curated universe look
plausible.

## 1. Motivation

`trades/derive.py` produces `TradeSignal`s with entry / stop / target / RR,
and the deepdive + scan UIs surface them — but no harness exercises them
end-to-end. The current backtest reports cover **count direction hit rate**
(top-1 / top-3 over a fixed forward window) and **pivot confirmation Brier**.
Neither answers the trade-economics question: *if a user followed the
signals as printed, what would the realized R distribution look like?*

Without trade-economics numbers:

- Every default in `TradeRules` (immediate vs confirmation entry,
  count-invalidation vs ATR stop, count target vs fib extension,
  `min_confidence=0.45`) is unvalidated. Spec open questions O2, O4, O5
  remain open.
- Future feedback-loop work (v0.4: outcome capture + personalization) has no
  baseline to compare local realized R against.
- The "expectancy in R" claim that appears in the §8 sample output isn't
  reproducible.

This layer closes that gap. It's deliberately small in scope so the harness
is correct before the ablation grid amplifies any sim bug 48×.

## 2. Goals and non-goals

**Goals**

- Run a deterministic, lookahead-safe trade simulation over `(universe,
  start, end)` under a single `TradeRules` config.
- Report the §8 trade-layer metrics (hit rate, avg R, expectancy, profit
  factor, max drawdown, open-at-end, sample size) with breakdowns by
  direction, pattern, coherence bucket, year.
- Mirror the count_layer / pivot_layer shape so the CLI surface is
  predictable: `wave-alpha backtest trade`, markdown report, optional JSON
  and CSV sidecars.
- Reuse `pipeline.run_analysis(llm_mode=DISABLED)` for signal generation
  (no separate enumerator path) so engine improvements flow into the sim
  for free.
- Honor every implemented `TradeRules` knob: `direction_modes`,
  `entry_trigger`, `stop_source`, `target_source`, `target_R`,
  `time_stop_mode`, `time_stop_bars`, `slippage_bps`,
  `commission_per_share`, `short_borrow_annual_pct`, `min_confidence`,
  `max_concurrent_signals_per_ticker`.

**Non-goals**

- Ablation grid sweep (next spec).
- Position sizing / dollar P&L. R-units only.
- Portfolio-level concurrency caps, correlation filters, sector caps. Per-
  ticker concurrency only.
- Re-entry blackout windows beyond `max_concurrent_signals_per_ticker=1`.
- Partial fills, liquidity caps, market-on-open vs limit modeling.
- Earnings / event filters.
- LLM-blended sims. `LLMMode.DISABLED` is hardcoded — no API key, no
  network. Same convention as count_layer / pivot_layer.
- Multiple-alternate signals per ticker (only top-1 ranked count's signal
  is taken; alts are still surfaced in the UI but not simulated).

## 3. CLI

```bash
wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 1 \
  --rules path/to/rules.yaml         # optional; defaults to TradeRules()
  --out reports/trade.md \
  --json reports/trade.json \         # optional machine-readable result
  --trades-csv reports/trades.csv     # optional per-trade ledger
```

- `--step` controls **signal-generation cadence** only (business-day step).
  Exit-evaluation always walks every business day at full bar resolution.
- `--rules` accepts a YAML mapping over `TradeRules` field names. If
  omitted, `TradeRules()` defaults are used. Unknown keys raise.
- `--out` is required and writes the markdown report. JSON / CSV sidecars
  are optional.
- LLM mode is **not exposed** as a flag — hardcoded to `DISABLED`. Matches
  the count_layer / pivot_layer convention.

## 4. Architecture

Mirrors the count_layer / pivot_layer split.

```
backtest/
  trade_layer.py   ← TradeRecord, TradeLayerResult, TradeLayerEvaluator
  runner.py        ← run_trade_layer(cfg, *, provider, rules) -> TradeLayerResult
  report.py        ← render_trade_markdown(result) -> str
cli/app.py         ← backtest_trade command
```

**`TradeRecord` (frozen dataclass)** — one closed (or open-at-end) trade:

```python
@dataclass(frozen=True)
class TradeRecord:
    ticker: str
    signal_as_of: date          # T: when pipeline produced the signal
    fill_date: date             # T+1 (immediate) or T+2 (confirmation)
    fill_price: Decimal         # post-slippage entry
    exit_date: date | None      # None iff status == "open_at_end"
    exit_price: Decimal | None  # post-slippage exit
    exit_reason: Literal["target", "stop", "time_stop", "open_at_end"]
    direction: Literal["long", "short"]
    pattern: str                # impulse / zigzag / ...
    wave_label: str             # "5", "C", ...
    coherence_score: float      # at signal time
    coherence_bucket: Literal["full", "partial", "disagreement"]
    bars_held: int
    risk_per_share: Decimal     # abs(fill_price - stop_price), realized basis
    realized_pnl_per_share: Decimal   # post-cost
    realized_R: float           # post-cost PnL / risk_per_share
    confidence: float           # signal.confidence at signal time
```

**`TradeLayerResult`** — aggregator with the same flavor as `CountLayerResult`:

```python
@dataclass
class TradeLayerResult:
    trades: list[TradeRecord] = field(default_factory=list)

    @property
    def closed_trades(self) -> int: ...
    @property
    def open_at_end(self) -> int: ...
    @property
    def hit_rate(self) -> float: ...           # over closed only
    @property
    def avg_R(self) -> float: ...              # over closed only
    @property
    def profit_factor(self) -> float: ...
    @property
    def max_drawdown_R(self) -> float: ...
    def by_direction(self) -> dict[str, dict[str, float]]: ...
    def by_pattern(self) -> dict[str, dict[str, float]]: ...
    def by_coherence_bucket(self) -> dict[str, dict[str, float]]: ...
    def by_year(self) -> dict[int, dict[str, float]]: ...
```

`expectancy` is a synonym for `avg_R`; the report prints it under the
`expectancy` label per spec §8 to match the published example, but the
implementation has one source of truth.

**`TradeLayerEvaluator`** — per-ticker walk; aggregator on top:

```python
@dataclass
class TradeLayerEvaluator:
    provider: OHLCVProvider
    rules: TradeRules

    def evaluate(self, ticker: str, *, start: date, end: date,
                 step: int) -> TradeLayerResult:
        ...
```

`run_trade_layer(cfg, *, provider, rules)` calls `evaluator.evaluate` for
every ticker in `cfg.universe` and concatenates the records.

## 5. Sim model — two-cadence walk

The simulation runs two interleaved cadences per ticker:

| Cadence | Step | Cost per step |
|---|---|---|
| Signal generation | `--step` business days (default 1) | full pipeline run (pivots + enumeration + coherence) — **expensive** |
| Exit evaluation | every business day | high/low compares against fixed levels — **cheap** |

The state machine has two states per ticker: `flat` and `in_position`.

### 5.1 Signal generation (cadence: --step)

For each as_of T in `iter_business_days(start, end, step)`:

1. If `in_position`, skip — no new signals while a position is open.
2. Run `pipeline.run_analysis(AnalyzeRequest(ticker=ticker, as_of=T,
   rules=rules), provider=provider, llm_mode=DISABLED)`. The
   `rules` instance flows into `derive_signal`, which **already enforces**
   `min_confidence` and `direction_modes` filtering — the trade layer does
   not re-apply them.
3. If `result.signals` is empty, continue.
4. Take the **top-1 signal** from `result.signals` (pipeline orders signals
   by ranked-count `final_score` descending; with `llm_mode=DISABLED`,
   `final_score = coh_multiplier × engine_score`).
5. Apply the entry trigger (§5.3) to determine `fill_date` and
   `fill_price`. If the trigger fails, drop the signal — the next signal-
   generation pass at T + step may re-fire.
6. Capture coherence at signal time (`result.coherence.score` and bucket
   via the shared `_bucket()` helper extracted in §8).
7. Transition to `in_position`. Record `signal_as_of=T`, planned
   `fill_date`, `stop_price`, `target_price`, `max_holding_bars`. The
   stop and target levels are **fixed at signal time** and do not
   re-anchor to `fill_price` even if the fill happens 1-2 bars later
   (matters for `atr_multiple` stops; `count_invalidation` is structural
   and independent of any anchor).

### 5.2 Exit evaluation (cadence: every business day)

Once `in_position`, walk every daily bar `B` starting at the **fill bar**
(`B.timestamp == fill_date`). The fill bar can itself trigger an exit
(same-bar stop or target). `bars_held` is 0 on the fill bar and
increments by 1 each bar advanced.

On each bar, evaluate exits in this order — first match wins:

1. **Stop:**
   - Long: `B.low ≤ stop_price` → exit at `min(B.open, stop_price)` (gap
     fills adversely at the open if the gap is below stop). On the fill
     bar specifically, use `B.open = fill_price` already — so exit at
     `min(fill_price, stop_price)`, which equals `fill_price` when the
     fill bar gapped past the stop and equals `stop_price` otherwise.
   - Short: `B.high ≥ stop_price` → exit at `max(B.open, stop_price)`,
     same fill-bar rule with sign reversed.
2. **Target:**
   - Long: `B.high ≥ target_price` → exit at `target_price` (favorable
     gaps fill at target, not at the better-than-target open; conservative)
   - Short: `B.low ≤ target_price` → exit at `target_price`
3. **Same-bar stop AND target:** stop wins. Documented assumption — the
   intra-bar path is unobservable and the conservative read avoids
   inflating P&L.
4. **Time stop:** if `bars_held ≥ signal.max_holding_bars` (or
   `rules.time_stop_bars` when `time_stop_mode == "fixed"`), exit at
   `B.close`.
5. None of the above: increment `bars_held`, advance to next bar.

If the bar walk reaches `end` without an exit, mark
`exit_reason="open_at_end"`, leave `exit_date = exit_price = None`. Open-at-
end trades **are excluded from headline metrics** but counted in the
`open_at_end` field of the report.

After exit on day E, transition to `flat`. The next eligible signal-
generation as_of is `T ≥ E + 1` (per `--step`).

### 5.3 Entry trigger semantics

| Trigger | Behavior |
|---|---|
| `immediate` | Signal at close of T. Fill at **open of T+1** with directional slippage. |
| `confirmation` | Signal at close of T. **Wait one bar.** If close of T+1 is in the signal direction (`> open` for long, `< open` for short), fill at **open of T+2** with directional slippage. Otherwise drop the signal; the next signal-generation pass at T + step may re-fire. |

Slippage application:
- Long entry: `fill_price = bar_open × (1 + slippage_bps/10000)`
- Short entry: `fill_price = bar_open × (1 - slippage_bps/10000)`
- Long exit (stop or target or time-stop): `exit_price = base × (1 - slippage_bps/10000)`
- Short exit: `exit_price = base × (1 + slippage_bps/10000)`

All slippage math in `Decimal`. `slippage_bps` is per-fill, not round-trip.

### 5.4 Stop and target sources

`stop_source`:
- `count_invalidation` — `_stop_price(count, ...)` already implements this.
- `atr_multiple` — new helper. Computes `ATR(14)` at signal time
  (`as_of=T` close; uses bars ≤ T) and places the stop at
  `entry_anchor ± 2 × ATR` on the loss side. The multiple is fixed at 2;
  if the ablation grid later wants a different multiple, that's a new
  TradeRules field added in the next spec, not now.

`target_source`:
- `count_target` — uses the count's pinned price target if available;
  falls back to fib 1.618 extension of W1 from the entry anchor
  otherwise. **In v0.5.x, no template defines a pinned target**, so this
  branch always falls through to fib 1.618 — functionally equivalent to
  `fib_extension`. The two are kept distinct in the API so a future
  template change (pinned target on a specific pattern, e.g., wave-5
  projection) flows through without needing a TradeRules schema change.
  Spec main-design §9 motivates this split.
- `fib_extension` — always uses fib 1.618 extension of W1 from the entry
  anchor. Today's `_target_price` behavior.
- `fixed_R` — anchor + (`rules.target_R` × `risk_per_share`) on the
  signal-direction side. Raises `ValueError` if `target_R is None` and
  `target_source == "fixed_R"`. `risk_per_share` here is computed as
  `abs(entry_anchor - stop_price)` at signal time (the planning basis,
  not the realized basis from §6 — `target_R=2` means "target = 2× the
  signal-time planned risk").

These extensions live in `trades/derive.py` next to the existing helpers.
The "entry anchor" used by all three sources is the signal's
`current_price` (T's close) — the same anchor `derive_signal` uses today.

### 5.5 Time stop

`time_stop_mode`:
- `degree_based` — uses `signal.max_holding_bars` (already populated by
  `derive_signal` from `_DEFAULT_BARS_PER_DEGREE × 1.5`).
- `fixed` — uses `rules.time_stop_bars`. Raises if `time_stop_bars is
  None` and mode is `fixed`.

## 6. Cost model

Per closed trade:

```
gross_pnl = (exit_price - fill_price) × dir_sign       # dir_sign = +1 long, -1 short
commission = 2 × commission_per_share
borrow = short_borrow_charge if direction == "short" else 0
realized_pnl_per_share = gross_pnl - commission - borrow
realized_R = realized_pnl_per_share / risk_per_share
```

Slippage is **already baked into** `fill_price` and `exit_price` by the
fill model in §5.3. No double-counting.

**R denominator (`risk_per_share`):** `signal.risk_per_share` is computed
at signal time (T close anchor) and is the *planning* risk. After fill
slippage is applied, the actual cash-at-risk is
`abs(fill_price - stop_price)`. The trade record's `risk_per_share` field
stores the **realized** value: `abs(fill_price - stop_price)`. This is
the denominator for `realized_R`. The signal-time planning risk is not
persisted on the `TradeRecord` — it's already in the original
`TradeSignal` for callers that want it.

**Short borrow charge:**

```
borrow = (fill_price × short_borrow_annual_pct × bars_held) / 252
```

Daily proration. `bars_held` is the number of business days between
`fill_date` (inclusive) and `exit_date` (exclusive). For a same-bar
stop-or-target on the fill bar, `bars_held = 0` and `borrow = 0`.

## 7. Metrics

### 7.1 Headline (closed trades only)

| Metric | Formula |
|---|---|
| `closed_trades` | `len([t for t in trades if exit_reason != "open_at_end"])` |
| `open_at_end` | `len([t for t in trades if exit_reason == "open_at_end"])` |
| `hit_rate` | `wins / closed_trades`, where `wins = sum(t.realized_R > 0)` |
| `avg_R` (= `expectancy`) | `mean(t.realized_R)` over closed |
| `profit_factor` | `sum(R for R in realized_R if R > 0) / abs(sum(R for R in realized_R if R < 0))`. If denominator is zero, returns `float("inf")`. |
| `max_drawdown_R` | Walk closed trades sorted by `exit_date`, accumulate cumulative R, track running peak. `max_drawdown_R = max(peak - cumulative)`. Returns `0.0` if no losses. |

`expectancy` and `avg_R` share an implementation; the report renders the
value under both labels to match spec §8.

### 7.2 Breakdowns

Each breakdown returns a `dict[<key>, dict[str, float]]` with the same
inner shape: `{sample_size, hit_rate, avg_R, expectancy, profit_factor}`.

- `by_direction` — keys: `"long"`, `"short"`.
- `by_pattern` — keys: distinct `pattern` values seen in trades.
- `by_coherence_bucket` — keys: `"full"`, `"partial"`, `"disagreement"`.
- `by_year` — keys: `int` year of `signal_as_of`.

`max_drawdown_R` is **not** computed per-breakdown — it requires a
chronological walk and the bucket grouping breaks chronology.

## 8. Shared helpers (cleanup tasks)

To avoid duplication:

- Extract `_bucket(coh: float) -> Literal["full", "partial", "disagreement"]`
  and `_COHERENCE_BUCKETS` from `backtest/count_layer.py` into
  `backtest/coherence_bucket.py`. Re-import from both layers. Targeted
  improvement, in scope because both layers consume the same buckets and
  duplicating the constant is the kind of drift this spec should not
  create.
- `iter_business_days` already lives in `walk_forward.py` — reuse.

## 9. Lookahead safety

Two surfaces to guard:

1. **Signal generation** — `pipeline.run_analysis(as_of=T)` already routes
   every bar fetch through `PointInTimeView`. No new exposure.
2. **Exit-loop bar access** — `TradeLayerEvaluator` fetches all daily bars
   once per ticker (same pattern as `CountLayerEvaluator`), then walks
   them by index from `fill_date` forward. The walk only ever advances;
   it never reads bars at index `> bar_walk_position`. Indexes are
   computed from `bar.timestamp` matches, not from offsets relative to
   `end`.

A new test `tests/backtest/test_trade_layer_lookahead.py` plants a future-
dated bar in the daily series and verifies the assertion fires (mirrors
the existing `tests/backtest/test_lookahead_guard.py`).

## 10. Output

### 10.1 Markdown report (`--out`)

Mirrors `render_count_markdown` / `render_pivot_markdown` style.

```
# Trade Layer Backtest Report
_generated 2026-05-04T18:31:22+00:00_

**Universe:** 50 tickers
**Window:** 2020-01-01 .. 2024-12-31
**Step:** 1
**Rules:** TradeRules(entry_trigger=immediate, stop_source=count_invalidation,
            target_source=count_target, min_confidence=0.45, ...)

**Closed trades:** 412
**Open at end:** 7

## Headline

- hit rate:        **0.55** (n=412)
- avg R:           **0.31**
- expectancy:      **0.31 R**
- profit factor:   **1.42**
- max drawdown:   **-0.18 R**

## by direction
| direction | n | hit | avg R | expectancy | PF |
| ... |

## by pattern
| ... |

## by coherence bucket
| bucket | n | hit | avg R | expectancy | PF |
| full | ... |
| partial | ... |
| disagreement | ... |

## by year
| year | n | hit | avg R | expectancy | PF |
| 2020 | ... |
...
```

### 10.2 JSON sidecar (`--json`, optional)

`{"headline": {...}, "by_direction": {...}, "by_pattern": {...},
"by_coherence_bucket": {...}, "by_year": {...}, "config": {...}, "rules":
{...}}`. Decimals serialize as JSON numbers (existing `_json_default`
pattern in `cli/app.py`).

### 10.3 CSV ledger (`--trades-csv`, optional)

Per-trade rows with all `TradeRecord` fields. Rules are not duplicated per-row
— they're already captured in the markdown report header and the JSON sidecar
alongside this CSV. Header row is the field names. Decimals as decimal strings
(no float coercion).

## 11. Tests

### 11.1 Unit — sim mechanics

Synthetic provider, hand-crafted ~30-bar series, tiny universe of 1
ticker. Each test isolates one mechanic:

- `test_target_hit_long` — bars walk up; assert `exit_reason="target"`,
  `realized_R ≈ rr` minus slippage and commissions.
- `test_stop_out_short_with_gap` — short signal, next-day open above the
  stop; assert exit at `max(open, stop)`, R is more negative than -1
  due to gap.
- `test_time_stop_long` — bars drift sideways past `max_holding_bars`;
  assert `exit_reason="time_stop"`, exit at close.
- `test_open_at_end` — signal late in window; bars don't trigger any exit
  by `end`; assert excluded from headline metrics, counted in
  `open_at_end`.
- `test_same_bar_stop_and_target` — single bar's high ≥ target AND low ≤
  stop; assert stop wins.
- `test_confirmation_drops_signal` — `entry_trigger="confirmation"`,
  next-bar close against direction; assert no trade recorded.
- `test_short_borrow_deduction` — short trade held N days; assert
  realized_R reduced by exactly the prorated borrow.
- `test_min_confidence_filter` — signal with confidence below
  `min_confidence`; assert dropped.
- `test_direction_mode_filter` — `direction_modes=["long"]`, short
  signal; assert dropped.
- `test_max_concurrent_blocks_reentry` — same signal fires while in
  position; assert no second trade.

### 11.2 Unit — metrics math

- `test_hit_rate_zero_trades` returns 0.0 (consistent with count_layer).
- `test_profit_factor_no_losses` returns `float("inf")`.
- `test_max_drawdown_simple` — known sequence
  `[+1, +0.5, -0.8, -0.5, +1.2]`; assert peak = 1.5 after trade 2,
  trough = 0.2 after trade 4, `max_drawdown_R = 1.3`.
- `test_breakdown_key_ordering` — `by_year` keys are sorted ascending
  (matches `CountLayerResult.by_year`); `by_coherence_bucket` keys are
  insertion-order from the static `("full", "partial", "disagreement")`
  tuple (matches `CountLayerResult.by_coherence_bucket`); `by_direction`
  and `by_pattern` keys are insertion-order of first appearance.

### 11.3 Lookahead guard

`test_trade_layer_lookahead_guard` — plant a bar with `timestamp >
as_of` in the daily series passed to the evaluator; assert
`LookaheadError` propagates.

### 11.4 Integration

`test_trade_layer_smoke` — 2 tickers (AAPL + MSFT), 2024 calendar year,
fixture bars from the existing `tests/fixtures/` cache. Assert:

- `result.closed_trades > 0`
- `render_trade_markdown(result)` returns a non-empty string containing
  "Trade Layer Backtest Report"
- The markdown round-trips through `render_trade_markdown`
  deterministically (same input → same output)

No assertion on absolute hit-rate values — those are empirical and will
shift as the engine evolves; the smoke test is structural.

## 12. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| T1 | Should `confirmation` use the right-edge model probability instead of bar-direction close? | Ablation phase | Bar-direction close is the textbook version; right-edge probability is one possible knob extension. Ship the simpler one first; ablation phase will reveal whether the extra knob is worth adding. |
| T2 | `atr_multiple_2` is hardcoded to 2× ATR. Is the multiple a separate ablation knob? | Ablation phase | If yes, it's a new `TradeRules` field. Keep the surface minimal here. |
| T3 | Should `same-bar stop wins` be configurable? | Post-launch | Will revisit if numbers look distorted. Standard backtester convention is sufficient for v1. |
| T4 | Portfolio-level concurrency caps and correlation filters | Post-v0.5 | Per-ticker is enough to validate signal economics; portfolio construction is a layer above this. |
| T5 | Should LLM-blended sims be supported (parametrized α)? | Post-ablation grid | Requires either a live API key or a populated cache for every (ticker, as_of). Ablation grid sweep is itself 48× cost; multiplying by α grid is out of budget for now. |
| T6 | Should the sim treat alternate (top-2, top-3) signals per ticker? | Post-launch | `max_concurrent_signals_per_ticker=1` in the rules already constrains this; the spec just consumes top-1. |
| T7 | `expected_bars_to_target` "× 1.5" for max_holding_bars — empirical fit? | v0.5 trade-derived analytics, after first sim run | Spec §9 says "degree-derived from historical median × 1.5" — the empirical median itself isn't computed yet. First sim run produces the data needed to fit it. |

## 13. Roadmap impact

This spec lands as v0.5.x. The ablation grid sweep that completes v0.5 per
spec §8 is a separate spec keyed on this layer's `run_trade_layer` API.

After v0.5 ships:

- Open question O2 (entry trigger default) — answered by ablation results.
- Open question O4 (LLM blend α) — addressable once a sim variant supports
  α as a knob.
- Open question O5 (coherence multiplier shape) — orthogonal to this spec
  but the by-coherence-bucket breakdown surfaces the data needed for it.

## 14. File-touch summary

**New:**
- `src/wave_alpha/backtest/trade_layer.py`
- `src/wave_alpha/backtest/coherence_bucket.py` (extracted from count_layer)
- `tests/backtest/test_trade_layer.py`
- `tests/backtest/test_trade_layer_lookahead.py`

**Modified:**
- `src/wave_alpha/backtest/runner.py` — add `run_trade_layer`
- `src/wave_alpha/backtest/report.py` — add `render_trade_markdown`
- `src/wave_alpha/backtest/count_layer.py` — re-import bucket from new module
- `src/wave_alpha/trades/derive.py` — add `atr_multiple` stop, `fixed_R`
  target helpers; keep public surface backwards-compatible
- `src/wave_alpha/cli/app.py` — add `backtest_trade` command
- `README.md` — extend the v0.5 section with the new command
- `pyproject.toml` — bump to v0.5.2 on merge

---

**Status: brainstorm complete; design approved. Next step: implementation
plan via `superpowers:writing-plans`.**
