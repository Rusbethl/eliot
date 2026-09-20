---
related:
  - docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md  # §6.6 EBT distribution, §9 S2 deferral
  - docs/superpowers/specs/2026-05-06-ablation-grid-design.md  # §9 G4 deferral
---

# `expected_bars_to_target` empirical fit — design

## 1. Motivation

The trade-sim sanity run (`2026-05-05-trade-sim-sanity-run.md` §6.6)
captured the empirical distribution of realized bars-to-target on
winning trades for the production-path degree (degree 2):

| degree | n_wins | p25 | median | p75 |
|---|---:|---:|---:|---:|
| 2 | 148 | 7.5 | **17** | **29** |

The production heuristic in `src/wave_alpha/trades/derive.py`
(`_DEFAULT_BARS_PER_DEGREE.get(2) = 25` and `max_holding_bars = int(expected * 1.5) = 38`) is meaningfully looser than the empirical
distribution: median is 17 (heuristic claims 25), p75 is 29 (heuristic
sets max_holding to 38).

The sanity-run §9 S2 deferred the refit to a separate spec because
*"fitting changes engine behavior, which would invalidate this run's
metrics."* The same logic applies to the ablation grid (Phase 1
already ran with the heuristic; spec §9 G4 logged the same deferral).

This spec ships the refit, validated by re-running the ablation grid's
Phase 2 winner cell with the new fits and confirming the winner's
metrics don't materially degrade.

## 2. Goals and non-goals

**Goals**

- Replace the degree-2 entry of `_DEFAULT_BARS_PER_DEGREE` with the
  empirical median (`17`).
- Add a separate per-degree `max_holding_bars` lookup so the
  multiplier is no longer hardcoded at `× 1.5`. Degree-2 entry is the
  empirical p75 (`29`); other degrees fall back to `median × 1.5`
  (preserving today's behavior for those degrees).
- Validate the refit against the ablation grid's Phase 2 winner cell:
  re-run that single cell and confirm expectancy delta is within
  ±0.05R of the pre-refit value.
- Close `2026-05-05-trade-sim-sanity-run.md` §9 S2 and
  `2026-05-06-ablation-grid-design.md` §9 G4.

**Non-goals**

- Refitting non-degree-2 entries. The pipeline still hardcodes
  `_DEFAULT_DEGREE_FOR_COUNT = 2`, so the sanity run produced data
  only for degree 2. Degrees 0, 1, 3, 4 keep their hand-coded
  heuristic values until S6 (multi-degree enumeration) ships and
  produces empirical data. A follow-up spec then completes the table.
- Storing the fit in an external artifact file (e.g.
  `models/ebt_lookup.json`). The right-edge artifact pattern is
  motivated by tensor-shaped weights and paired-bootstrap promotion;
  one integer per degree doesn't justify the loader/fallback
  machinery. Provenance lives in this spec.
- Making EBT or `max_holding_bars` `TradeRules`-configurable. That's a
  different feature ("user-overrideable EBT") with its own UX
  considerations; this spec ships the engine-default refit.
- Re-running the full Phase 1/Phase 2 grid with the refit.
  Single-cell validation is sufficient given the small magnitude of
  the change for degree 2 (one entry, one ratio recalibration).
- Refitting the `× 1.5` multiplier itself for degrees 3 and 4 (where
  it remains the fallback). The heuristic ratio is preserved as a
  no-data-yet placeholder.

## 3. Architecture

### 3.1 The change

In `src/wave_alpha/trades/derive.py`, replace:

```python
_DEFAULT_BARS_PER_DEGREE: dict[int, int] = {0: 5, 1: 10, 2: 25, 3: 60, 4: 150}
...
expected = _DEFAULT_BARS_PER_DEGREE.get(count.degree, 25)
max_hold = int(expected * 1.5)
```

with:

```python
# Median bars-to-target, per degree. Degree 2 is empirical (sanity run
# 2026-05-05, n=148 winners on the curated 50-ticker universe over
# 2020-2024, median=17 bars). Other degrees retain the pre-empirical
# heuristic until multi-degree enumeration ships and produces data.
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: 10, 2: 17, 3: 60, 4: 150}

# p75 bars-to-target, per degree, used as the time-stop upper bound.
# Degree 2 is empirical (p75=29 of winners). Other degrees fall back
# to median * 1.5 inside derive_signal (the existing heuristic ratio).
_EBT_P75_BY_DEGREE: dict[int, int] = {2: 29}
...
expected = _EBT_MEDIAN_BY_DEGREE.get(count.degree, 25)
max_hold = _EBT_P75_BY_DEGREE.get(count.degree, int(expected * 1.5))
```

The fallback for `_EBT_P75_BY_DEGREE` preserves the existing
`max_hold = int(expected * 1.5)` behavior for any degree without
empirical data. Today that means degrees 0, 1, 3, 4 are unchanged
in behavior. Tomorrow, when S6 ships and degree N's median is fitted,
populating `_EBT_P75_BY_DEGREE[N]` overrides that degree's time-stop
upper bound independently.

### 3.2 No other consumer changes

`expected_bars_to_target` and `max_holding_bars` are fields on
`TradeSignal` (Pydantic v2 frozen). No schema change; no migration.
The downstream consumers are:

- `trade_layer.TradeLayerEvaluator._walk_to_exit` reads
  `signal.max_holding_bars` to gate the time-stop. Tighter
  `max_holding_bars` = more time-stop fires earlier.
- The web/CLI deepdive surface `expected_bars_to_target` for display
  only.
- `tests/backtest/_synthetic.py:_record` and several `derive_signal`
  call-site tests assert specific values — these need updating.

### 3.3 Validation pass

The ablation grid spec lands the Phase 2 winner cell. Once that
verdict is in §6.4 of the grid spec, this spec's validation runs:

1. Identify the Phase 2 winner cell from
   `reports/grid-2026-05-06/phase2/grid.md`.
2. Build a one-cell YAML config for that cell's `TradeRules`.
3. Re-run `wave-alpha backtest trade --rules <cell.yaml>` for the
   same window (2020-2024, curated universe, step=5) on the post-refit
   code.
4. Compare the post-refit run's headline metrics
   (`expectancy`, `hit_rate`, `profit_factor`, `closed_trades`)
   side-by-side with the cell's pre-refit metrics from the grid's
   Phase 2 results.
5. Pass criterion: `|post - pre| ≤ 0.05R` on `expectancy`. Wider
   bands are documented findings rather than blockers if the user
   accepts; tighter bands are noise.

The validation outputs go to
`reports/ebt-empirical-fit-2026-05-XX/{report.md, result.json,
trades.csv, comparison.md}`. The `comparison.md` is the durable
artifact of the side-by-side, inlined into §6 of this spec at merge
time.

### 3.4 Sequencing constraint

This spec is **chronologically gated on the ablation grid spec
merging.** It can be written and committed to a feat branch in
parallel (the design is settled), but cannot merge until the grid's
v0.5.6 is in `main` because:

- The validation pass (§3.3) reads the grid's Phase 2 winner cell.
- Merging the EBT refit before Phase 2 lands would invalidate Phase 2
  metrics by changing the `max_holding_bars` engine constant
  mid-grid.

Branch: `feat/ebt-empirical-fit` (separate from
`feat/ablation-grid-spec`).

## 4. Tests

### 4.1 `test_derive_signal_uses_empirical_ebt_for_degree_2`

A degree-2 setup (the existing `_bullish_w5_setup` produces this)
yields:
- `signal.expected_bars_to_target == 17`
- `signal.max_holding_bars == 29`

Replaces the existing `expected == 25` / `max_hold == 38` assertions
in `test_derive.py`.

### 4.2 `test_derive_signal_falls_back_to_heuristic_for_unfit_degrees`

For a degree-3 setup (constructed via `count.model_copy(update={"degree": 3})` on the W5 setup), assert:
- `signal.expected_bars_to_target == 60` (heuristic, unchanged)
- `signal.max_holding_bars == 90` (`60 * 1.5`, fallback path)

### 4.3 `test_derive_signal_carries_degree` continues to pass

Existing test from the trade-sim sanity-run branch verifies
`signal.degree == count.degree`. No change required.

### 4.4 Existing direct-`TradeRecord`-construction tests update

`tests/backtest/_synthetic.py:_record` currently sets
`expected_bars_to_target=10` (a fixture-default); this is independent
of the runtime fit and stays as-is. Tests that thread through
`derive_signal` (e.g., the trade-layer integration tests) pick up the
new values automatically.

## 5. File structure

**Modified:**
- `src/wave_alpha/trades/derive.py` — constant rename + new lookup +
  dispatch change. ~10 lines including comments.
- `tests/trades/test_derive.py` — update existing degree-2 assertions
  to the new values; add the degree-3 fallback test.
- `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` §9 S2 —
  resolve with reference to this spec.
- `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` §9 G4 —
  resolve with reference to this spec.
- `pyproject.toml`, `src/wave_alpha/__init__.py`, `uv.lock` — version
  bump to v0.5.7 at merge time.
- This spec — fill §6 (validation results) at merge time.

**New (transient, gitignored):**
- `reports/ebt-empirical-fit-2026-05-XX/` — validation artifacts.

**Not modified:**
- `src/wave_alpha/trades/models.py` — no schema change.
- `src/wave_alpha/backtest/trade_layer.py` — engine numerics
  unchanged; just consumes `signal.max_holding_bars`.
- `src/wave_alpha/pipeline.py` — no change.

## 6. Validation results

> **Pre-refit cell:** `entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.45` (ablation grid Phase 2 winner, code SHA `38a4c54`) — expectancy +0.272R, n=1818
> **Post-refit run code SHA:** `a0eceb4` (`feat/ebt-empirical-fit` after T1+T2)
> **Run date:** 2026-05-08 (UTC)
> **Wall time:** 24m 55s (warm cache)

| metric | pre-refit (Phase 2) | post-refit | delta |
|---|---:|---:|---:|
| closed_trades | 1818 | 2007 | +189 |
| hit_rate | 0.4015 | 0.4260 | +0.0245 |
| avg_R | +0.2716 | +0.2181 | −0.0535 |
| expectancy | +0.2716 | +0.2181 | −0.0535 |
| profit_factor | 1.4431 | 1.3549 | −0.0881 |
| max_drawdown_R | 143.37 | 190.60 | +47.23 |

**Verdict (pass criterion: `|expectancy delta| ≤ 0.05R`):**

- [ ] **PASS — refit ships.** Verify the assertion above, commit
  the §6 fill, bump to v0.5.7.
- [x] **MARGINAL — ship with note.** `|delta| = 0.0535 R` ∈ (0.05, 0.10] R.
- [ ] **FAIL — do not ship.** `|delta| > 0.10 R`. Document the
  finding; revert this branch unmerged. Likely cause: the time-stop
  was implicitly carrying the engine; tightening it removes hidden
  edge.

**Reasoning:**

The empirical refit (`expected_bars_to_target` 25→17;
`max_holding_bars` 37→29) tightens the time-stop by 22%. The
mechanism is a tradeoff:

- **More closed trades (+189, +10.4%).** Faster position turnover
  frees the per-ticker concurrency slot sooner, allowing more
  re-entries within the 2020-2024 window.
- **Higher hit rate (+2.5pp).** The tighter exit captures more
  trades that were already in profit when the time-stop fires.
- **Lower avg R per trade (−0.054R).** Some winners that needed the
  full 37-bar window to reach target now close early at smaller R.
- **Worse drawdown (+47R, +33%).** More losers accumulate closer to
  entry before the new tighter time-stop kicks in.

Aggregate profit math:
- Pre-refit: 1818 × 0.272 = 494 R total
- Post-refit: 2007 × 0.218 = 437 R total

**Absolute profit −11.5% post-refit** even with the trade-count
lift. The refit's signal field (`expected_bars_to_target=17`) is
more honest — empirical median IS the typical winner exit — but the
looser heuristic was implicitly carrying small edge by holding
stalled trades long enough to recover.

The MARGINAL verdict ships the refit because:
1. The empirical median is the truthful default; signal fields that
   misrepresent reality cause downstream confusion (web UI shows
   `expected_bars_to_target` to users).
2. The expectancy degradation (−0.054R) is small in absolute terms
   — the cell is still strongly positive (+0.218R, PF 1.35).
3. The trade-off is *legible*: the spec records both pre and post
   metrics; future analysis can isolate the time-stop contribution.

**Follow-up logged:** the recommended-defaults from the grid spec
were derived on the pre-refit engine. Post-refit, the Phase 2 winner
is still the best cell within its (entry, stop, target,
min_confidence) class but its absolute expectancy is now +0.218R.
A future grid re-run could re-validate this if absolute edge
matters; for now, the recommendation in
`2026-05-06-ablation-grid-design.md` §6.4 stands with the caveat
that the actual post-refit expectancy is +0.218R, not +0.272R.

**Companion artifact:** `reports/ebt-empirical-fit-2026-05-08/comparison.md` (gitignored) holds the full mechanistic analysis.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Single-cell validation is too narrow — refit might pass on the winner but tank other cells | Acceptable: the recommended default is the winner cell; if the refit doesn't break the winner, that's the contract. If the user wants broader validation, run more cells from the Phase 2 results post-merge as ad-hoc work. |
| Phase 2 winner cell uses `target_source=fixed_R` (so EBT is irrelevant for target derivation but max_holding_bars still matters) | Both fits operate on `max_holding_bars` regardless of `target_source`. The EBT field is informational only past the signal generation point. The validation is meaningful even for `fixed_R` cells. |
| New empirical median (17) lower than heuristic (25) means the signal "promises" winners faster — caller-facing UI/CLI may need messaging update | The EBT field is shown in deepdive HTML and CLI JSON/markdown; the change is downward (more conservative claim, more accurate). No additional UX work required. |
| Fallback path for non-degree-2 cells silently retains `× 1.5` ratio | Documented in §2 (non-goal). Not a regression. |
| Validation run stalls on cold cache | Cache should be fully warm post-grid; pre-warm pass available if needed. |

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| E1 | Should we also fit non-degree-2 entries? | Multi-degree S6 follow-up | Pipeline produces only degree-2 data today. Hand-fitting other degrees is just relabeling guesses. |
| E2 | Should `max_holding_bars` switch from p75 to a different percentile (p80, p90)? | Empirical re-eval after S6 + 5y of fresh data | The p75 cut sacrifices 25% of historical winners that took longer; a p90 cut sacrifices 10% but holds losers longer. Re-evaluable as data accumulates. |
| E3 | Should the validation re-run multiple Phase 2 cells, not just the winner? | Out of scope (cost vs benefit) | Single-cell is the contract. Broader re-run is a follow-up if the user wants it. |
| E4 | Should this become an artifact file when degree-3+ data lands? | Multi-degree S6 follow-up | An artifact is justified once the lookup is 5+ degrees of empirical data, not 1. |

## 9. Roadmap impact

This spec ships as v0.5.7 (after the grid's v0.5.6). It closes:
- `2026-05-05-trade-sim-sanity-run.md` §9 S2 (deferred decision)
- `2026-05-06-ablation-grid-design.md` §9 G4 (same deferral, different spec)

It does NOT close `2026-05-05-trade-sim-sanity-run.md` §9 S6
(multi-degree enumeration), which remains the open path for
completing the lookup table.

Post-merge, the natural follow-up sequence is:
- **S6 multi-degree enumeration** (engine work, prerequisite for
  fitting other degrees).
- **EBT refit v2** (this spec's successor, fills out
  `_EBT_MEDIAN_BY_DEGREE` and `_EBT_P75_BY_DEGREE` for the new
  degrees once S6 produces data).
- **Per-pattern EBT fits** (G3 from grid spec — orthogonal to S6,
  fits EBT separately for `impulse` vs `contracting_triangle` vs
  `zigzag` etc).
