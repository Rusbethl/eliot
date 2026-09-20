---
related:
  - docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md  # §9 S5
  - docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md  # §5.1
---

# `atr_multiple` stop_source wiring — design

## 1. Motivation

The 2026-05-05 trade-sim sanity run discovered (S5) that
`TradeRules.stop_source = "atr_multiple"` is not wired into
`derive_signal`. The schema accepts the value, but `_stop_price` only
handles the `count_invalidation` branch — every other value falls
through to `last_pivot`. The unit `_stop_price_atr_multiple` is
implemented and tested in isolation but never called from the
production path.

The result: anyone setting `stop_source="atr_multiple"` (centroid,
backtest, live analysis, web UI) gets `last_pivot` stops without any
warning. For our centroid this produced extremely tight stops —
combined with the (now-fixed) target-direction bug, the first sanity
sim run reported 0% hit rate across 12,245 trades.

The trade-sim sanity run shipped a fix for the target-direction bug
and switched the centroid to `count_invalidation` to unblock the
verdict. The §10.1 GO verdict explicitly named this wiring as a
follow-up that gates the ablation grid: the grid wants to sweep both
stop sources, and one of them not actually working is a non-starter.

This spec ships the wiring. It is deliberately narrow: no new
`TradeRules` fields, no behavior change to `count_invalidation`, no
re-running of the sanity sim, no grid-related work.

## 2. Goals and non-goals

**Goals**

- `derive_signal` produces an ATR-based stop when
  `rules.stop_source == "atr_multiple"`, on the loss side of the
  entry anchor, with multiple = 2 and period = 14.
- The schema-stated option matches the runtime behavior. No silent
  fallback.
- When ATR cannot be computed (no bars or fewer than `period`
  bars), `derive_signal` raises `ValueError`. The pipeline catches at
  the per-candidate call site so one bad candidate does not lose its
  ranked siblings.
- All existing call paths that use `count_invalidation` (or any other
  setting) keep working unchanged. `bars` is an opt-in kwarg.

**Non-goals**

- Adding `atr_stop_multiple` or `atr_period` fields to `TradeRules`.
  Hard-code multiple=2 and period=14 here; the ablation grid spec
  introduces sweepable knobs if it needs them. (Honors the trade-sim
  layer spec §5.1 commitment: *"the multiple is fixed at 2; if the
  ablation grid later wants a different multiple, that's a new
  TradeRules field added in the next spec, not now."*)
- Re-running the sanity sim with `atr_multiple` enabled. That is the
  ablation grid spec's job.
- Changing `_stop_price_atr_multiple` itself — it is unit-tested and
  direction-correct.
- Threading `bars` to web/CLI live analysis paths beyond
  `pipeline.run_analysis`. They consume `pipeline.run_analysis`
  already, so they inherit the wiring transparently.
- Removing the `last_pivot` fallback for unrecognized
  `stop_source` values. That fallback is dead code today (the
  `Literal` type narrows it) but removing it is out of scope.

## 3. Architecture

### 3.1 API changes

`derive_signal` (in `src/wave_alpha/trades/derive.py`):

```python
def derive_signal(
    *,
    count: WaveCount,
    ticker: str,
    as_of: date,
    current_price: Decimal,
    rules: TradeRules,
    bars: list[Bar] | None = None,  # NEW — required when stop_source="atr_multiple"
) -> TradeSignal | None:
    ...
```

The kwarg is the only signature change. Default `None` preserves
back-compat with all `count_invalidation` callers and tests.

`_stop_price` (in `src/wave_alpha/trades/derive.py`):

```python
def _stop_price(
    count: WaveCount,
    direction: str,
    rules: TradeRules,
    *,
    bars: list[Bar] | None = None,
    anchor: Decimal | None = None,
) -> Decimal:
    if rules.stop_source == "atr_multiple":
        if bars is None:
            raise ValueError("atr_multiple stop_source requires bars")
        if anchor is None:
            raise ValueError("atr_multiple stop_source requires anchor")
        return _stop_price_atr_multiple(
            bars=bars,
            anchor=anchor,
            direction=direction,  # type: ignore[arg-type]
            multiple=Decimal("2"),
        )
    # existing count_invalidation logic, unchanged
    ...
```

The existing `_stop_price_atr_multiple` raises its own `ValueError`
when fewer than `period` bars are available — that propagates
through unchanged.

### 3.2 Pipeline integration

`_bars(provider, ticker, interval, as_of)` already applies
`PointInTimeView(raw, as_of).visible()` before returning. The local
`daily` variable in `run_analysis` is therefore already post-view —
no second slicing required. Pass it directly:

```python
# src/wave_alpha/pipeline.py — within run_analysis
for rc in ranked[:3]:
    adjusted = rc.count.model_copy(update={"score": rc.final_score})
    try:
        sig = derive_signal(
            count=adjusted,
            ticker=req.ticker,
            as_of=req.as_of,
            current_price=latest_close,
            rules=req.rules,
            bars=daily,
        )
    except ValueError:
        # atr_multiple needed bars but they're insufficient; skip just
        # this candidate — don't drop the others in `ranked[:3]`.
        continue
    if sig is not None:
        signals.append(sig)
```

The `try/except ValueError` is *narrowly scoped* to the per-candidate
call. Other `ValueError`s in the pipeline (zero-risk degenerate
signal, etc.) are unaffected because `derive_signal` returns `None`
for those rather than raising.

### 3.3 Lookahead safety

`_stop_price_atr_multiple` uses `compute_atr(bars, period=14)` —
ATR's last value is computed from the last 14 bars in `bars`. As long
as `bars` is sliced through `PointInTimeView` (no bars later than
`as_of`), there is no lookahead leak. The pipeline already does this
slicing; the wiring just passes the result through.

The synthetic-leak guard at `tests/backtest/test_lookahead_guard.py`
covers the broader pipeline. Adding `bars` to the signal flow does
not introduce a new leak surface because the bars travel through the
existing point-in-time boundary.

## 4. Tests

Six new tests, each pinning one invariant. TDD — failing test first,
then implementation, then green.

### 4.1 `test_atr_multiple_long_stop_below_entry`

For a 4-segment W4-complete (long) impulse setup:

```python
sig = derive_signal(
    count=long_setup,
    ticker="TEST",
    as_of=date(...),
    current_price=Decimal("120"),
    rules=TradeRules(stop_source="atr_multiple"),
    bars=daily_bars_with_known_atr,
)
assert sig is not None
expected_atr = compute_atr(daily_bars_with_known_atr, period=14)[-1]
assert sig.stop_price == Decimal("120") - Decimal("2") * expected_atr
assert sig.stop_price < sig.entry_price
```

### 4.2 `test_atr_multiple_short_stop_above_entry`

Mirror of 4.1 for a 5-seg W5-complete (short) setup. Assert:

```python
assert sig.stop_price == Decimal("139") + Decimal("2") * expected_atr
assert sig.stop_price > sig.entry_price
```

### 4.3 `test_atr_multiple_raises_without_bars`

```python
with pytest.raises(ValueError, match="atr_multiple"):
    derive_signal(
        count=long_setup,
        ticker="TEST",
        as_of=date(...),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="atr_multiple"),
        # bars omitted (defaults to None)
    )
```

### 4.4 `test_atr_multiple_raises_with_insufficient_bars`

```python
with pytest.raises(ValueError, match="ATR unavailable"):
    derive_signal(
        count=long_setup,
        ...,
        rules=TradeRules(stop_source="atr_multiple"),
        bars=daily_bars_with_only_10_bars,  # < 14
    )
```

### 4.5 `test_count_invalidation_still_works_without_bars`

```python
sig = derive_signal(
    count=long_setup,
    ...,
    rules=TradeRules(stop_source="count_invalidation"),
    # bars omitted
)
assert sig is not None  # no exception, current behavior preserved
```

### 4.6 `test_pipeline_skips_bad_atr_candidate_keeps_others`

Integration test in `tests/test_pipeline.py`. Stage two ranked
candidates and a stub `derive_signal` (or monkeypatch
`_stop_price_atr_multiple`) that raises `ValueError` for the first
candidate but returns a valid stop for the second. Assert
`result.signals` has exactly 1 entry (the second candidate). This
pins the `try/except ValueError` boundary at the per-candidate
granularity from §3.2.

## 5. File structure

**Modified:**

- `src/wave_alpha/trades/derive.py` — `_stop_price` signature +
  dispatch, `derive_signal` signature.
- `src/wave_alpha/pipeline.py` — pass `bars` to `derive_signal`,
  per-candidate `try/except ValueError`.
- `tests/trades/test_derive.py` — tests 4.1–4.5.
- `tests/test_pipeline.py` — test 4.6.

**Not modified:**

- `src/wave_alpha/trades/models.py` — no schema change.
- `_stop_price_atr_multiple` — direction-correct already, has its
  own unit tests.
- Any test that calls `derive_signal` with `stop_source` other than
  `atr_multiple` — `bars=None` default keeps them green.

## 6. Lookahead and other invariants

- All `bars` passed into `derive_signal` from `pipeline.run_analysis`
  flow through `PointInTimeView`. No lookahead leak introduced.
- `_stop_price_atr_multiple` raises `ValueError("ATR unavailable:
  need 14+ bars, got N")` when fewer than `period` bars are
  available. This message is the contract the pipeline relies on for
  its skip behavior.
- The pipeline's `try/except ValueError` does *not* catch
  unrelated `ValueError`s — `derive_signal` only raises `ValueError`
  for the atr_multiple-bars problem; other failure modes (zero-risk
  signal, direction excluded by rules) return `None`. So the
  except is unambiguous in its intent even though it is broad in
  type.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A user of `derive_signal` outside the pipeline (test, web, CLI) sets `stop_source="atr_multiple"` without passing `bars` | The new `ValueError` will surface at runtime. This is by design — the schema previously misled them. The error message names the missing kwarg. |
| `bars` accidentally bypasses `PointInTimeView` and leaks future bars | Pipeline call sites slice through `PointInTimeView` already. Reviewers should reject any new call site that builds `bars` without point-in-time slicing. The existing synthetic-leak test (`test_lookahead_guard.py`) catches this at the integration boundary. |
| Backtest harnesses that import `derive_signal` directly (skipping `pipeline.run_analysis`) and used `atr_multiple` will start failing | This is the documented goal — they were silently broken. The fix is to either pass `bars` or switch to `count_invalidation`. Audit on merge. |
| ATR returns 0 (e.g., flat-line synthetic test data) → stop equals entry, signal becomes zero-risk | `derive_signal`'s existing `if stop == current_price: return None` path handles this. No change needed. |

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| W1 | Should `atr_stop_multiple` and `atr_period` become configurable knobs? | Ablation grid spec | Honors the trade-sim layer spec §5.1 commitment. Adding them now would bloat `TradeRules` for a single-axis grid sweep. |
| W2 | Should the pipeline log a warning when it skips an atr_multiple candidate? | Future polish | Logging would help diagnose silent skip rates but adds a project-wide logging convention question. The grid spec can introduce skip-rate tracking via a counter on `TradeLayerResult` if needed. |
| W3 | Should `_stop_price` raise a typed error class (e.g. `ATRUnavailableError`) instead of bare `ValueError`? | Future polish | Single-purpose exceptions are nice but `ValueError` is what `_stop_price_atr_multiple` raises today and the call site reads cleanly. |
| W4 | Should we also add an opt-in `atr_period` validation in `TradeRules.__post_init__` that warns if period > available history? | Out of scope | Validators don't know the data shape at config time. Runtime raise is the right place. |

## 9. Roadmap impact

This spec lands as v0.5.4 → v0.5.5 (minor bump iff observable
behavior changes — and it does, by design, for any prior user of
`stop_source="atr_multiple"`). Once merged, the ablation grid spec
can sweep both `count_invalidation` and `atr_multiple` cleanly.

The grid spec gets a tighter scope: no need to think about wiring,
only about which knobs to sweep and in what range. If it decides to
sweep the multiple, that's where the `atr_stop_multiple` field gets
introduced (W1).
