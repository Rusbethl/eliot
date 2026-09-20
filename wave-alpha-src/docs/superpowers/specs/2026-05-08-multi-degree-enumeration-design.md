---
related:
  - docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md  # §9 S6 — deferred origin
  - docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md  # degree-2 EBT context
  - docs/superpowers/specs/2026-05-08-target-r-sweep-design.md  # most recent prior spec
---

# Multi-degree enumeration — design

## 1. Motivation

The 2026-05-05 trade-sim sanity run deferred (S6) the question of
whether `enumerate_counts` should emit candidates at multiple degrees
or only at degree 2. Today, `pipeline.py:_DEFAULT_DEGREE_FOR_COUNT = 2`
gates the enumerator to a single degree per analyze request. The
multi-degree pivot detector (`pivots/multi_degree.py:detect_multi_degree`)
already produces pivots at degrees 0..N — they are computed and then
discarded for everything except degree 2.

The result: the engine only "sees" patterns at one time horizon. A
ticker with a clean degree-3 (longer-horizon) impulse and no degree-2
setup produces no candidates. The C5 invariant from the sanity-run
inspection script ("per-degree EBT diversity should be non-trivial")
has been degenerate-by-design — every signal carries the same degree,
the same EBT, and the same time-stop, masking any per-degree
calibration questions.

This spec ships multi-degree enumeration. The pivot detector and the
enumerator are already degree-parameterized; the change is at the
pipeline assembly layer. After this lands:

- `analyze` surfaces patterns at degrees 1, 2, and 3 — finer swings,
  the current sweet spot, and longer-horizon trends.
- The C5 inspection becomes meaningful.
- The follow-on empirical EBT refit (deferred per §9 W1) has data to
  fit against.

The scope is deliberately narrow: assembly only. No empirical
recalibration, no schema changes, no per-degree confidence thresholds.

## 2. Goals and non-goals

**Goals**

- `analyze` emits candidates spanning degrees 1, 2, and 3 in a single
  call, with up to 2 candidates per degree (6 max).
- Each candidate carries `count.degree` — already a `WaveCount` field;
  downstream `derive_signal`, EBT lookup, LLM rerank, and web UI all
  key on it without modification.
- The enumerator (`elliott/enumerator.py:enumerate_counts`) is
  unchanged — it is already degree-parameterized via its `degree`
  kwarg.
- Existing trade-layer concurrency rules (`max_concurrent_signals_per_ticker
  = 1`) hold; multi-degree candidates compete for the per-ticker slot
  via score.
- The C5 inspection invariant ("per-degree EBT diversity") becomes
  meaningful: degree-1 signals get heuristic EBT 10/15, degree-2 get
  empirical 17/29, degree-3 get heuristic 60/90.

**Non-goals**

- Empirical EBT refit for degrees 1 and 3. The current heuristic
  values are reasonable starting points; the first multi-degree
  backtest produces the data needed for a follow-up empirical fit
  spec (§9 W1). Shipping S6 with heuristics is intentional — the
  alternative (refit-before-ship) creates a chicken-and-egg loop
  since fitting requires multi-degree trade data.
- Adding degree-0 (microstructure noise — too many false positives)
  or degrees 4+ (rare on 5-year input; few have enough pivots to
  enumerate templates).
- Per-degree `min_confidence` thresholds (G3 from grid spec) — out of
  scope here; would interact with multi-degree results but is its own
  follow-up.
- Per-degree weighting of candidates against the LLM rerank — the
  existing `final = coh_multiplier × (alpha × engine_score + (1 -
  alpha) × llm_prob)` formula applies uniformly across degrees.
- Web UI changes to display degree prominently. `count.degree` is
  already in the response schema; UI rendering is its own follow-up.
- Coherence rework. Three-way coherence (W/D/4h) is multi-*interval*,
  distinct from multi-*degree*. The two concepts are orthogonal and
  remain orthogonal.
- Schema changes. `WaveCount.degree` already exists; `TradeSignal.degree`
  was added by the sanity-run B1; no new fields needed.

## 3. Architecture

### 3.1 Pipeline assembly change

The single change is at `pipeline.py:_enumerate_for` (currently lines
217-222). The function detects multi-degree pivots already; the
narrowing to a single degree happens inside it. Replace the
single-degree narrow + single call with a multi-degree loop:

**Before** (current `pipeline.py:217-222`):

```python
def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    pivots: list[PivotEvent] = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
    return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

**After**:

```python
# Replace `_DEFAULT_DEGREE_FOR_COUNT = 2` with these two:
_ENUMERATED_DEGREES: tuple[int, ...] = (1, 2, 3)
_TOP_K_PER_DEGREE: int = 2


def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    """Enumerate candidates across all configured degrees and pool the
    results.  Each degree contributes up to `_TOP_K_PER_DEGREE`
    candidates; degrees with no pivots contribute nothing.

    Per-degree top-K is preferred over a global top-K so that one
    degree's score distribution cannot crowd out the others (degree-2
    has a calibrated score baseline from the 2026-05-07 EBT fit;
    degrees 1 and 3 do not).
    """
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    candidates: list[WaveCount] = []
    for degree in _ENUMERATED_DEGREES:
        pivots = by_degree.get(degree, [])
        candidates.extend(
            enumerate_counts(pivots, degree=degree, top_k=_TOP_K_PER_DEGREE)
        )
    return candidates
```

Output cardinality changes from up to 5 (single-degree top-5) to up
to 6 (3 degrees × top-2). Existing consumers (the call sites of
`_enumerate_for` in `run_analysis` for weekly, daily, 4h) handle a
list of arbitrary size; they pass the candidates through
`three_way_coherence` and the optional LLM rerank, neither of which
cares whether the candidate list spans one degree or three. Each
candidate independently participates in the coherence multiplier
and the rerank scoring.

### 3.2 Trade-layer integration — no change

`derive_signal(count, ticker, as_of, current_price, rules, bars=…)`
already keys on `count.degree`:

- `_EBT_MEDIAN_BY_DEGREE.get(count.degree, 25)` — heuristic fallback
  for unfit degrees (degrees 1, 3 today).
- `_EBT_P75_BY_DEGREE.get(count.degree, int(expected * 1.5))` —
  similarly heuristic for unfit degrees.

Degree-1 candidates produce `expected_bars_to_target=10`,
`max_holding_bars=15`. Degree-3 produces `60` and `90`. Degree-2
keeps the empirical `17` and `29`.

`max_concurrent_signals_per_ticker = 1` (TradeRules default) is
enforced in the trade-layer harness (`run_trade_layer`), not in
`derive_signal`. Multi-degree candidates compete for the per-ticker
slot at the trade-layer; the highest-score signal wins. This matches
the existing pipeline's score-driven ranking — it just now considers
candidates from three degrees instead of one.

### 3.3 Lookahead — no new surface

`PointInTimeView` is the as-of slicing boundary. The pivot detector
(`detect_multi_degree`) already produces multi-degree pivots from
point-in-time-sliced bars. The change here is downstream of the
slicing: it consumes already-safe pivot lists. No new fetch sites,
no new bar paths.

The synthetic-leak guard at `tests/backtest/test_lookahead_guard.py`
remains green by construction — its assertion fires on bar-level
violations, not on enumerator behavior.

## 4. Tests

Three new tests, each pinning one invariant. TDD — failing test
first, then implementation, then green.

### 4.1 `test_pipeline_emits_multi_degree_candidates`

Use a synthetic bar series long enough that the ATR-zigzag detector
produces pivots at degrees 1, 2, and 3. After `run_analysis`, assert
that `result.counts_daily` (the post-enumeration, pre-rank list)
contains candidates spanning at least 2 distinct degrees from
{1, 2, 3}.

```python
def test_pipeline_emits_multi_degree_candidates() -> None:
    """Pipeline must surface candidates across the three configured
    degrees (1, 2, 3) when input pivots support each.  Pins the S6
    multi-degree enumeration: prior to this, only degree-2 candidates
    were emitted regardless of pivot input."""
    result = run_analysis(...)  # synthetic input rich enough for 3 degrees
    degrees = {c.degree for c in result.counts_daily}
    assert len(degrees) >= 2, (
        f"expected candidates from at least 2 degrees, got {degrees}"
    )
    assert degrees.issubset({1, 2, 3}), (
        f"candidates outside enumerated degrees: {degrees - {1, 2, 3}}"
    )
```

### 4.2 `test_pipeline_top_k_per_degree_cap`

```python
def test_pipeline_top_k_per_degree_cap() -> None:
    """No more than 2 candidates per degree are emitted (K=2 cap from
    pipeline._TOP_K_PER_DEGREE).  Pins the cap so that future
    enumerator changes don't accidentally inflate the output."""
    result = run_analysis(...)  # rich input that could produce many candidates
    by_deg: dict[int, int] = {}
    for c in result.counts_daily:
        by_deg[c.degree] = by_deg.get(c.degree, 0) + 1
    for degree, count in by_deg.items():
        assert count <= 2, f"degree {degree} produced {count} candidates (>2)"
```

### 4.3 `test_derive_signal_per_degree_ebt`

This test pins the C5 invariant — per-degree EBT diversity — at the
`derive_signal` unit level rather than through the full pipeline.
Reason: the pipeline's `signals` list only includes the top-3 ranked
candidates (`ranked[:3]`), so a multi-degree integration test would
be flaky (score distribution may not surface all 3 degrees).
Asserting at the `derive_signal` level is deterministic.

```python
def test_derive_signal_per_degree_ebt() -> None:
    """The C5 invariant from the sanity run: per-degree EBT diversity
    is non-trivial.  Degree-2 signals use empirical EBT (17/29);
    degree-1 use heuristic 10/15; degree-3 use heuristic 60/90.  Pins
    the wiring through derive_signal at the unit level."""
    base = _bullish_w5_setup()  # degree-2 fixture from existing tests

    sig_d1 = derive_signal(
        count=base.model_copy(update={"degree": 1}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d2 = derive_signal(
        count=base,  # degree=2
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d3 = derive_signal(
        count=base.model_copy(update={"degree": 3}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )

    assert sig_d1 is not None and sig_d1.expected_bars_to_target == 10
    assert sig_d1 is not None and sig_d1.max_holding_bars == 15
    assert sig_d2 is not None and sig_d2.expected_bars_to_target == 17
    assert sig_d2 is not None and sig_d2.max_holding_bars == 29
    assert sig_d3 is not None and sig_d3.expected_bars_to_target == 60
    assert sig_d3 is not None and sig_d3.max_holding_bars == 90
```

This test partially overlaps with the existing
`test_derive_signal_uses_empirical_ebt_for_degree_2` and
`test_derive_signal_falls_back_to_heuristic_for_unfit_degrees` tests
(which already pin degree-2 and degree-3 EBT). The new test adds
degree-1 explicitly and serves as the C5-invariant pin in one place.

### 4.4 Existing tests — regression sweep

The full pytest suite (506 tests at the time of writing) must stay
green. Particular attention to:

- `tests/elliott/test_enumerator.py` — passes through `degree` kwarg;
  no change needed.
- `tests/backtest/test_lookahead_guard.py` — synthetic-leak guard;
  unchanged.
- `tests/test_pipeline.py` — existing pipeline tests that previously
  asserted single-degree output need audit; any test that asserts
  `len(result.counts_daily) <= 5` (or similar on `counts_weekly` /
  `counts_4h`) needs a bump to `<= 6` (the new per-degree-K=2 ×
  3-degrees cap), or relaxation to a multi-degree-aware assertion.

## 5. File structure

**Modified:**

- `src/wave_alpha/pipeline.py` — replace `_DEFAULT_DEGREE_FOR_COUNT`
  constant + single-call with `_ENUMERATED_DEGREES = (1, 2, 3)`,
  `_TOP_K_PER_DEGREE = 2`, and the loop in `_enumerate`.
- `tests/test_pipeline.py` — three new tests (§4.1–4.3); audit any
  existing test asserting on top_counts length and update if needed.

**Not modified:**

- `src/wave_alpha/elliott/enumerator.py` — already degree-parameterized.
- `src/wave_alpha/trades/derive.py` — already keys on `count.degree`
  for EBT lookup.
- `src/wave_alpha/right_edge/assessment.py` — calls `enumerate_counts`
  with caller-supplied degree; unchanged.
- `src/wave_alpha/llm/candidates.py` — `serialize_candidate_set`
  serializes whatever candidates are passed in; degree is part of
  the existing schema.
- `src/wave_alpha/web/` — UI consumes `top_counts` and renders
  whatever is there; degree column may want a future enhancement
  (out of scope).

## 6. Lookahead and other invariants

- The pivot detector is already point-in-time-safe; multi-degree
  pivots flow through `PointInTimeView`. No new leak surface.
- LLM mode `disabled` (the default and the backtest-mandatory mode)
  receives multi-degree candidates the same way it received single-
  degree: as a list of `WaveCount` objects with `count.degree` set.
  No prompt change required.
- The coherence multiplier (`three_way_coherence`) is computed
  per-candidate against multi-interval pivot snapshots (W/D/4h).
  Multi-*degree* candidates within one interval each get their own
  coherence score independently — coherence is orthogonal to degree.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Backtest n_trades for the curated universe inflates from ~2,200 (single-degree, v0.5.9) to ~5–6k (multi-degree). Comparing post-S6 backtest results to pre-S6 baselines is no longer apples-to-apples. | Documented goal — multi-degree is the new world. Future grids re-baseline from multi-degree numbers. The v0.5.9 sanity baseline `+0.218R` becomes a historical reference, not a comparator. |
| Degree-1 and degree-3 signals use heuristic EBT, which may produce systematically too-tight or too-loose time-stops. The empirical winner-rate at those degrees is unknown. | Per §9 W1, ship and measure first. The first multi-degree backtest produces per-degree distributions that feed the EBT refit. Heuristic EBT errors do not affect enumeration correctness — only trade-outcome calibration. |
| The candidate fan-out (up to 6 per analyze) increases LLM prompt size by ~20%. Token cost goes up proportionally for `mode=live` users. | LLM is opt-in (`disabled` is default and mandatory for backtest comparability). Live users opting into LLM rerank already accept token costs; +20% is acceptable and the privacy contract is unchanged. Future-proof: if the cap matters, `_TOP_K_PER_DEGREE` can be lowered to 1 for LLM mode. |
| `max_concurrent_signals_per_ticker = 1` means a degree-1 signal and a degree-3 signal at the same ticker compete for the slot. The lower-degree (typically faster, higher-volume) may win on score and crowd out the higher-degree (typically slower, lower-volume but potentially more decisive). | Documented behavior — single-position per ticker is the project's discipline. If multi-degree results suggest score normalization is needed, that is a follow-up calibration spec, not part of this enumeration scope. |
| One of the existing pipeline tests may have implicit assumptions that `top_counts` contains only degree-2 entries. | The §4.4 audit catches this. Any test that asserts `count.degree == 2` should be updated to either (a) assert `count.degree in {1, 2, 3}` (loose) or (b) explicitly construct a single-degree input (preserves the test's original intent). |

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| W1 | Empirical EBT refit for degrees 1 and 3 | Post-S6 follow-up spec | Needs multi-degree trade data, which requires this spec to ship. The first backtest after S6 produces per-degree winner distributions; refit follows the same methodology as the 2026-05-07 degree-2 EBT spec. |
| W2 | Per-degree `min_confidence` thresholds (G3 from grid spec) | Post-S6 grid spec | Phase 2 hinted at score-distribution heterogeneity across cells; per-degree thresholds may benefit from multi-degree data. Out of scope for the enumeration ship. |
| W3 | Web UI degree filter / column | Future UI polish | The signal already carries `degree`. UI can render it later without a contract change. |
| W4 | Score normalization across degrees (e.g., per-degree z-score before pooling) | Future calibration spec, iff multi-degree backtest shows degree dominance | Per-degree top-K avoids crowd-out at the *enumeration* layer. At the *trade-layer* layer, score competition for the per-ticker slot is intentional; if it skews toward one degree dominantly, normalization is the answer. |
| W5 | Should K=2 per degree be configurable via CLI / config? | Future polish | Hard-coded for now to keep the ship narrow. If the cap turns out to matter for analyze use cases, expose as a `--top-k-per-degree` flag. |

## 9. Roadmap impact

This spec lands as **v0.5.10** (minor: behavior change visible in
`analyze` output — degrees 1 and 3 now produce candidates). No
schema change, no breaking API. Existing consumers see additional
candidates in `top_counts` and `signals`; consumers that filter by
`count.degree == 2` continue to work unchanged.

After this ships:

1. **Multi-degree backtest run** (~6h on the curated universe). Same
   harness, no harness changes — it consumes whatever the pipeline
   emits. Produces per-degree trade outcome data.
2. **Empirical EBT refit spec** (§9 W1) using the data from step 1.
   Methodology mirrors the 2026-05-07 degree-2 fit.
3. **Re-run the C5 inspection** from the sanity-run script — it
   becomes meaningful (per-degree EBT diversity is now non-trivial)
   and either confirms calibration or surfaces issues.

The next-next phase is the W1 follow-up sweep (relax `max_holding_bars`
for high-R cells) deferred from the target_R sweep. That work is
independent of S6 — it could run in parallel.

## 10. Estimated runtime

- Spec: this document (~30 min).
- Plan: ~30 min.
- Implementation: ~1h (3-line config change + 3 tests).
- Verification (full pytest + ruff): ~5 min.
- Audit + fix any incidental test failures from multi-degree
  candidate fan-out: 30-60 min depending on test surface.
- Commit + branch merge: ~15 min.

Total: ~3-4 hours of active work. No long-running compute jobs in
this spec — the ~6h multi-degree backtest is a *follow-up*, not part
of S6 itself.
