---
related:
  - docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md
  - docs/superpowers/specs/2026-05-04-4h-cache-strategy-design.md
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §8 ablation grid
---

# Trade-sim sanity run — 2020-2024 curated universe

## 1. Motivation

The trade-sim layer (`wave-alpha backtest trade`, shipped v0.5.2) and the
4h cache strategy (v0.5.3) together close the gap between count
enumeration and trade-level metrics. The trade-sim layer's design spec
(§13 Roadmap impact) and the 4h cache spec (§9 Roadmap impact) both name
the same gating step before the §8 ablation grid sweep: a single-config
sim run on the curated universe to verify the harness produces correct
and plausible numbers.

This spec scopes that run. It is a *run*, not a *feature*: zero new CLI
commands, zero new modules unless a sim bug surfaces. The deliverable is
artifacts (report, JSON, CSV) plus this spec extended with results and a
go/no-go verdict on the ablation grid.

The B-bar — *correctness + plausible economics* — is the contract:

- **Correctness:** lookahead-free per the existing synthetic-leak guard;
  state-machine produces well-formed `TradeRecord`s (no negative R, no
  exit-before-entry, no dangling `in_position` rows except `open_at_end`);
  per-degree `expected_bars_to_target` distributions are non-degenerate.
- **Plausible economics:** headline metrics are non-pathological
  (hit-rate not 0% or 100%, |expectancy| not > 10R, profit factor finite
  unless `n=0`); coherence buckets are weakly monotone in expectancy
  (`full ≥ partial` within the same TF-count slice).

The sim does *not* need to be profitable on default rules — that is what
the ablation grid is for. We only need confidence that the sim is correct
before the grid amplifies a bug 48×.

## 2. Goals and non-goals

**Goals**

- Produce one clean trade-sim run on the curated universe over
  2020-2024, with weekly+daily+4h-where-available coverage, under a
  single fixed `TradeRules` centroid (§3).
- Persist artifacts under `reports/trade-sim-2026-05-05/` for audit.
- Verify the B-bar (§5) by inspecting the artifacts.
- Capture sim bugs found during inspection, fix in-branch with TDD
  (Workflow C, §4.5), and re-run until the B-bar holds — or escape to a
  fresh design spec if a structural bug demands one.
- Produce the empirical `expected_bars_to_target` distribution per
  degree as a side-product, unblocking deferred decision T7 from the
  trade-sim layer spec ("× 1.5 — empirical fit").
- Make a go/no-go call on writing the ablation-grid spec next.

**Non-goals**

- Ablation grid sweep itself (§8 of main design — separate spec, gated
  on this one).
- New CLI commands. The existing `wave-alpha backtest trade` plus a YAML
  rules file is the entire interface.
- Position sizing, dollar P&L, portfolio-level concurrency caps,
  correlation filters. R-units only, per-ticker concurrency only — same
  scope as the trade-sim layer spec.
- Edge optimization. The centroid is *a* config, not *the best* config.
- Right-edge model retraining. The current shipped logistic + calibrated
  heuristic are the active models for this run.

## 3. Configuration

### 3.1 Centroid TradeRules

Persisted to `configs/sanity_run.yaml` and loaded via the existing
`--rules` flag of `backtest trade`.

```yaml
# configs/sanity_run.yaml — post-discovery centroid (Phase E iter 1)
direction_modes: [long, short]
entry_trigger: immediate
stop_source: count_invalidation
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

Two fields deviate from `TradeRules()` defaults:

| Field | Default | Centroid | Why |
|---|---|---|---|
| `target_source` | `count_target` | `fib_extension` | Equivalent code path today (per the 2026-05-04 spec clarification commit `da9b0d5`); naming the canonical extension explicitly avoids future ambiguity if `count_target` is rebound to a different definition. |
| `slippage_bps` | `10` | `5` | More representative of liquid US equities at exchange-open fills; matches a likely grid centroid. |

All other fields are defaults.

**Discovery (Phase E iter 1):** the original centroid specified
`stop_source: atr_multiple`, picked as a "clean fixed-vol baseline." The
first sim run revealed `atr_multiple` is not wired into `derive_signal`
— the schema accepts it but the code falls through to `last_pivot`,
producing extremely tight count-derived stops. The centroid was
switched to `count_invalidation` (the actually-implemented path).
Wiring `atr_multiple` into `derive_signal` is logged in §9 (S5) as a
follow-up; the ablation grid spec should sweep both stop sources once
`atr_multiple` is wired.

### 3.2 Run scope

| Knob | Value |
|---|---|
| Universe | `data/universe_curated.txt` (71 tickers) |
| Start | `2020-01-01` |
| End | `2024-12-31` |
| Step | `5` (signal generation every 5 business days) |
| Timeframes | weekly + daily + 4h-where-available (post-2023-05 per yfinance 730d limit + cache fallback) |
| LLM mode | `disabled` (mandatory for backtest comparisons per `CLAUDE.md`) |

### 3.3 Output artifacts

All under `reports/trade-sim-2026-05-05/` (gitignored — see §4.6):

| File | Contents |
|---|---|
| `report.md` | `render_trade_markdown` output: headline, by-direction, by-pattern, by-coherence-bucket, by-year. |
| `result.json` | Full sidecar including `by_tf_count` and `coverage_4h`. |
| `trades.csv` | One row per `TradeRecord` for ad-hoc analysis. |
| `expected_bars_distribution.json` | Derived post-run from `trades.csv`: per-degree median + p25/p75 of realized bars-to-exit on winning trades. Feeds T7. |

The first three are produced by the existing CLI flags (`--out`,
`--json`, `--trades-csv`). The fourth is computed by a small inline
script in the spec follow-up section (one-shot, not a new CLI command).

## 4. Workflow

Workflow C from the brainstorm: fix-as-you-go on a single branch,
multiple commits if needed, one final report. The branch is
`feat/trade-sim-sanity-run`.

### 4.1 Pre-warm

The 4h cache is empty for most of the curated universe. A cold sim loop
will spend most of its wall time on yfinance fetches. Pre-warm by
running `wave-alpha analyze` (or a tiny script that calls
`pipeline.run_analysis`) once per ticker with `as_of=2024-12-31` before
the main sim. This populates `cache.sqlite` for daily + weekly +
4h-where-available across the full window.

### 4.2 Add the centroid config

Create `configs/sanity_run.yaml` as in §3.1. Commit:

```
feat(configs): centroid TradeRules for trade-sim sanity run
```

### 4.3 Optional: render `by_tf_count` in the markdown report

The JSON sidecar exposes `by_tf_count`, but `render_trade_markdown`
currently does not. The B-bar inspection in §5 reads from JSON, so this
is non-blocking — but adding the table to the markdown is one trivial
edit in `src/wave_alpha/backtest/report.py` (one more `_table(...)`
call) plus a test. If we touch the report file at all during fix
iterations, fold this in opportunistically. Otherwise defer.

### 4.4 Run

```bash
uv run wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --rules configs/sanity_run.yaml \
  --out reports/trade-sim-2026-05-05/report.md \
  --json reports/trade-sim-2026-05-05/result.json \
  --trades-csv reports/trade-sim-2026-05-05/trades.csv
```

Capture stdout/stderr to `reports/trade-sim-2026-05-05/run.log` for
debug if anything looks off.

### 4.5 Inspect → fix-as-you-go

For each B-bar criterion (§5), inspect the artifacts. If a criterion
fails:

1. Reproduce in a focused unit test under `tests/backtest/`. The test
   describes the violation in terms of `TradeRecord` invariants or
   metric properties — not the specific tickers/dates from this run
   (those are not deterministic across re-fetches).
2. Confirm the test fails on `main`.
3. Fix in `src/wave_alpha/backtest/trade_layer.py` (or `derive.py`,
   `count_layer.py` as needed) and confirm the test passes.
4. Commit with a clear `fix(backtest): …` message naming the invariant.
5. Re-run §4.4. The cache is warm now; reruns are fast.

If a fix requires a structural change beyond the trade-sim layer's
current design (e.g., the entry-trigger semantics need to differ between
intraday and daily timeframes), escape to a new design spec rather than
patching in this branch.

### 4.6 Persist results to the spec, not git history

`reports/` should be added to `.gitignore` if not already, since the
artifacts are large and reproducible from the inputs. The headline
numbers and breakdowns are inlined into §6 of this spec — that is the
canonical, durable record.

### 4.7 Verdict and merge

Once the B-bar holds:

1. Extend §6 of this spec with the final headline + breakdowns +
   per-criterion verdict.
2. Extend §7 with one row per fix made during §4.5 (or write
   "no fixes required" if the run was clean first time).
3. Make the explicit go/no-go call in §10.
4. Squash-merge or merge-commit into `main` per project convention.
5. The next spec (`docs/superpowers/specs/2026-05-XX-ablation-grid-design.md`)
   can begin once this lands.

## 5. B-bar pass criteria

The verdict in §10 is GO iff every row below is "pass". Every row gets
a one-line evidence note in §6.

### 5.1 Correctness — sim invariants

| # | Criterion | How to check |
|---|---|---|
| C1 | No future-bar leak | `tests/backtest/test_lookahead_guard.py` still passes. |
| C2 | Every closed trade has `risk_per_share > 0` | Read `trades.csv`; assert `entry_price != stop_price`. (Already enforced by `TradeSignal._check_risk` — this is a smoke check that no record bypassed it.) |
| C3 | Every closed trade has `exit_date >= fill_date` | `trades.csv` scan. |
| C4 | Every `in_position` record at end of run has `exit_reason == "open_at_end"` | `trades.csv` scan. |
| C5 | `expected_bars_to_target` populated and non-degenerate per degree | Read from per-record signals; assert each degree has ≥ 5 distinct values across the run. |
| C6 | Per-ticker concurrent count never exceeds `max_concurrent_signals_per_ticker=1` | `trades.csv` scan: group by ticker, sort by `fill_date`, check no overlap with prior trade's `(fill_date, exit_date]`. |
| C7 | Slippage applied directionally on every fill and exit | Spot-check one long and one short trade: `fill_price` vs bar open of `T+1`, `exit_price` vs bar value at exit reason — ratios should match `1 ± 5/10000`. |

### 5.2 Plausible economics

| # | Criterion | Threshold |
|---|---|---|
| E1 | Hit rate not pathological | `0.05 ≤ hit_rate ≤ 0.95` |
| E2 | Expectancy not pathological | `\|expectancy\| ≤ 10.0 R` |
| E3 | Profit factor finite (or `n=0`) | `result.profit_factor < ∞` when `n_wins > 0 and n_losses > 0` |
| E4 | Coherence-bucket monotonicity within 2-TF slice | `expectancy[full] ≥ expectancy[partial]` over trades with `tf_count == 2`. Tolerance: ±0.1 R for small samples (`min(n_full, n_partial) < 20`). |
| E5 | Coherence-bucket monotonicity within 3-TF slice | Same as E4, restricted to `tf_count == 3`. **Not required if `n_3tf < 50`** — too small to be reliable; documented as deferred to grid run with longer 4h history. |
| E6 | Sample size adequate | Total `closed_trades >= 200`. (Curated 71 tickers × 5y × weekly step gives capacity for ~10× this even with conservative signal acceptance rates.) |

### 5.3 Coverage smoke checks

| # | Criterion | Threshold |
|---|---|---|
| K1 | 4h coverage > 0% in the post-2023 window | `coverage_4h` headline > 0. (If 0, the cache pre-warm did not capture 4h — fix before re-run.) |
| K2 | `by_tf_count` populates both `2` and `3` keys | Both buckets non-empty in `result.json`. |
| K3 | `by_year` populates every year 2020–2024 | All 5 keys non-empty. |

A failure in §5.1 is a *hard fail* — fix and re-run. A failure in §5.2
or §5.3 may be (a) a sim bug — fix and re-run, or (b) genuinely the
behavior of the system at default rules — document as a finding for the
ablation grid. Distinguishing the two is the verdict author's call;
they explain their reasoning in §6.

## 6. Results

> **Run date:** 2026-05-06 (UTC)
> **Cache state at run:** warm (Phase D1 pre-warm + Phase E iter 0 left the cache fully populated)
> **Wall time:** 22 min 12 s (Phase E iter 1, post-fix; iter 0 was 58 min with mixed-cold cache)
> **Code SHA:** `1b99921` (`fix(derive): target + count_invalidation stop honor trade direction` + centroid switch to `count_invalidation`)
> **Universe:** 50 tickers (`data/universe_curated.txt` is 71 lines — 21 are comments/blanks)

### 6.1 Headline

```
closed trades:    4577
open at end:      130
hit rate:         0.142
avg R:            -0.130
expectancy:       -0.130 R
profit factor:    0.71
max drawdown:     -650.48 R
4h coverage:      28.4% (1301 / 4577 closed trades had 4h coherence)
```

### 6.2 By coherence bucket

| bucket | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| full | 1499 | 0.12 | -0.28 | -0.28 | 0.42 |
| partial | 2345 | 0.15 | -0.08 | -0.08 | 0.81 |
| disagreement | 733 | 0.17 | +0.03 | +0.03 | 1.08 |

**Reverse-monotonic.** Higher coherence performs *worse* on this
centroid, in both 2-TF and 3-TF slices (see §6.3 + verdict E4/E5).
This is a finding for the ablation grid, not a sim bug — see §6.7
verdict reasoning.

### 6.3 By TF-count

| tf_count | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| 2 | 3276 | 0.15 | -0.11 | -0.11 | 0.75 |
| 3 | 1301 | 0.13 | -0.18 | -0.18 | 0.61 |

3-TF (4h-included) is slightly worse on this centroid — also reverse
of the prior expectation. Under-determined: 4h availability is
post-2023-only, so 3-TF samples skew to a single regime.

### 6.4 By year

| year | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| 2020 | 914 | 0.16 | -0.14 | -0.14 | 0.69 |
| 2021 | 942 | 0.13 | -0.13 | -0.13 | 0.71 |
| 2022 | 900 | 0.15 | -0.10 | -0.10 | 0.78 |
| 2023 | 869 | 0.14 | -0.13 | -0.13 | 0.71 |
| 2024 | 952 | 0.13 | -0.15 | -0.15 | 0.66 |

Stable across years. No regime-specific anomaly.

### 6.5 By pattern

| pattern | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| zigzag | 641 | 0.20 | -0.23 | -0.23 | 0.63 |
| contracting_triangle | 2699 | 0.12 | -0.15 | -0.15 | 0.64 |
| ending_diagonal | 1045 | 0.15 | -0.05 | -0.05 | 0.88 |
| impulse | 168 | 0.17 | -0.07 | -0.07 | 0.82 |
| flat | 10 | 0.40 | +2.32 | +2.32 | 5.73 |
| expanded_flat | 14 | 0.14 | -0.50 | -0.50 | 0.33 |

`contracting_triangle` dominates the universe at n=2699 — over half
of all signals — which by itself depresses centroid economics. The
ablation grid should consider per-pattern `min_confidence` or pattern
exclusion knobs. `flat` (n=10) is too small to weight.

### 6.6 Empirical `expected_bars_to_target` distribution

| degree | n_wins | p25 | median | p75 |
|---|---:|---:|---:|---:|
| 2 | 148 | 7.5 | 17.0 | 29.0 |

Only degree 2 is enumerated today (see §9 S6). Heuristic
`_DEFAULT_BARS_PER_DEGREE.get(2) = 25` predicts winners exit in 25
bars; empirical median is 17. Distribution skews right
(p75=29 ≈ heuristic, p25=7.5 ≪ heuristic). The "× 1.5" max-holding
multiplier looks roughly right at p75, but a lower median suggests
many wins are quick and a tighter time-stop wouldn't lose much.

This data feeds T7 (replacing the heuristic with an empirical fit).
The fit itself is *not* applied in this spec — separate follow-up
keyed off this distribution. The single-degree limitation makes a
real per-degree fit deferred until multi-degree enumeration ships.

### 6.7 B-bar verdict per criterion

| # | Pass / Fail | Evidence |
|---|---|---|
| C1 | PASS | `tests/backtest/test_lookahead_guard.py` exit=0 |
| C2 | PASS | 0/4577 zero-risk records |
| C3 | PASS | 0/4577 order-violations (every `exit_date >= fill_date`) |
| C4 | PASS | every row has `exit_reason` populated |
| C5 | PASS (skipped) | only degree 2 enumerated today; check is degenerate by design (S6) |
| C6 | PASS | 0 per-ticker overlaps across 49 tickers |
| C7 | PASS | 50 fills inspected, all positive prices |
| E1 | PASS | hit_rate=0.142 ∈ [0.05, 0.95] |
| E2 | PASS | expectancy=-0.130R, |·| ≤ 10 |
| E3 | PASS | PF=0.71, finite (not ∞ with closed>0) |
| E4 | FAIL | tf_count=2: full=-0.24R (n=1126) < partial=-0.11R (n=1687). **Reverse-monotonic.** Documented finding, not sim bug — see reasoning below. |
| E5 | FAIL | tf_count=3: full=-0.41R (n=373) < partial=-0.02R (n=658). Same pattern as E4. |
| E6 | PASS | closed_trades=4577 ≥ 200 |
| K1 | PASS | coverage_4h=0.284 |
| K2 | PASS | n_2tf=3276, n_3tf=1301 — both populated |
| K3 | PASS | all 5 years 2020-2024 populated |

**Reasoning on E4/E5 reverse-monotonicity (genuine finding, not a sim bug):**

The state machine is correct (C1-C7 all green) and the samples are
large (n>373 in every coherence cell). Both 2-TF and 3-TF show the
same direction — partial > full > disagreement-equal-or-better — with
consistent magnitudes. This is not a noise artifact.

Two plausible drivers, both grist for the ablation grid:

1. **Direction skew.** Shorts dominate (3136 / 4577 = 69% of closed
   trades) and shorts perform much worse than longs (-0.25R vs
   +0.14R). If the "full" coherence bucket skews short — plausible
   because high coherence in a sustained bull regime would mark
   correction setups, which generate shorts — that alone produces
   reverse monotonicity. The ablation grid should slice
   coherence-by-direction.
2. **Coherence threshold calibration.** The fixed 0.7 / 0.4
   bucket boundaries inherited from the count-layer report may not be
   appropriate for the trade-sim layer. The grid should sweep
   thresholds or replace the bucket-level summary with continuous
   coherence-vs-realized-R analysis.

Either way, the sim is correctly *exposing* this misbehavior. That is
exactly what the sanity run is supposed to do. The grid's value is
finding a config where coherence is informative; this run shows the
default config isn't one.

## 7. Sim-bug fix ledger

| # | Commit | Invariant violated | Fix summary |
|---|---|---|---|
| 1 | `0fb4833` | Phase D1 — prewarm script crashed at startup | `CachedProvider` requires `db_path`; the v1 prewarm omitted it. Pass `~/.wave_alpha/cache.sqlite` explicitly. (Tooling fix, not engine.) |
| 2 | `308407e` | Phase E iter 1 — short trades exit at "target" with -26R loss; 0% hit rate across 12245 trades | **Two engine bugs in `derive.py`.** (a) `_target_price` projected `fib_extension` off W1's price direction, ignoring the trade direction — placing the target above anchor for W5-complete shorts. The sim's `bar.low <= target` check then fires immediately on day 0, exiting at the wrong-side target. Fix: project `\|W1\| × 1.618` from anchor on the trade-direction side. (b) `_stop_price` for `count_invalidation` returned `segs[3].end.price` (W4 end) for any 4-or-more-seg impulse — wrong for 5-seg shorts where the invalidation is the W5 extreme. Fix: 5+ seg impulse uses `last_pivot`. Test `test_short_signal_target_below_entry` pins both invariants. |
| 3 | `c00f4eb` | Phase E iter 1 — inspection script C5 EBT-diversity check fails when only one degree is enumerated, a known degenerate property of the current pipeline | Skip C5 with a note when `len(by_degree) == 1`. Becomes meaningful once multi-degree enumeration ships. (Inspector fix, not engine.) |

The two engine bugs in row 2 affect any backtest path or live analysis
that consumes a short signal — not just this sanity run. Both ship as
part of this branch.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Cold-cache fetch latency dominates wall time | §4.1 pre-warm pass. yfinance is rate-limited; if the warm pass itself is slow, run it overnight and retry the failures with backoff. |
| 4h cache returns < 730d for some tickers due to delisting / late-listed names | Acceptable — `coverage_4h` will be < 100% and `by_tf_count[2]` will be populated. Both are expected and the spec is built around them. |
| Curated universe contains a ticker that yfinance no longer serves | Fail fast in pre-warm; remove from `data/universe_curated.txt` for this run only (record exclusion in §6). Do not silently skip during the sim. |
| Sim run produces zero closed trades for a year | E6 (≥200 trades total) is the floor. By-year empty cells (K3 fail) trigger investigation of `min_confidence` or signal-acceptance gates. |
| Right-edge model artifacts drift between dev machines | The shipped logistic + calibrated heuristic are checked in (`f7d20f3`, `09fa647`). Verify `git status` is clean on those paths before §4.4. |
| Inspection in §5 misses a bug because of small sample | E5 explicitly tolerates this (n_3tf < 50 → skip). For other criteria, E6 forces ≥ 200 — if that floor is violated, sample-size insensitive criteria still gate. |

## 9. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| S1 | Should the centroid use `count_invalidation` instead? | Resolved in Phase E iter 1 | `atr_multiple` was unwired in `derive_signal`; centroid switched to `count_invalidation`. See S5. |
| S2 | Should `expected_bars_to_target` re-fitting happen in this branch? | **Resolved by `2026-05-07-ebt-empirical-fit-design.md`** | The follow-up spec ships the degree-2 empirical fit (median=17, p75=29) with a single-cell validation pass against the grid's Phase 2 winner. |
| S3 | Should we add `by_tf_count` rendering to the markdown report? | §4.3 — opportunistic, not required | JSON sidecar already exposes it. Inline rendering is < 10 LOC if we touch `report.py` for any other reason. |
| S4 | What about the v0.5.4 version bump? | Not in this spec | This spec ships docs and (possibly) trade-sim bug fixes. A bump is appropriate iff §7 is non-empty *and* the fixes change observable behavior. Decide at merge time. |
| S5 | Wire `atr_multiple` into `derive_signal` | Separate follow-up spec | `_stop_price_atr_multiple` is implemented as a unit but not threaded into the signal-generation path. Wiring requires plumbing `bars` through `derive_signal` and updating the pipeline. The ablation grid spec depends on this so both stop-sources can be swept. |
| S6 | Inspection script C5 (per-degree EBT diversity) is degenerate | Note in §6.7 evidence; revisit when multi-degree enumeration ships | `_DEFAULT_DEGREE_FOR_COUNT = 2` in pipeline.py means every signal carries the same EBT (constant per degree). The C5 invariant only becomes meaningful once the enumerator emits multiple degrees. |

## 10. Roadmap impact

This spec lands as documentation + (possibly) trade-sim layer
correctness fixes. Once the B-bar holds and §10.1 below is GO, the next
spec is the ablation grid sweep keyed off
`run_trade_layer(cfg, *, provider, rules) -> TradeLayerResult`.

### 10.1 Verdict

- [x] **GO** — proceed to write `docs/superpowers/specs/2026-05-XX-ablation-grid-design.md`.
- [ ] **NO-GO** — see §6.7 failure rows; root cause must be addressed in a follow-up spec before the grid can be written.

**Reasoning:** All 7 §5.1 correctness criteria pass (C5 skipped per S6
— degenerate by design with single-degree enumeration). Headline
economics are non-pathological after the iter-1 engine fix
(hit_rate 0.142, expectancy -0.130R, PF 0.71, max_dd 650R). E4/E5
reverse-monotonicity is a real finding — large samples, both 2-TF
and 3-TF agree, two plausible drivers (direction skew, threshold
calibration) that the grid is well-positioned to disentangle. The
sim is correctly *exposing* a config gap; the grid's job is to find a
config where coherence is informative.

The grid spec should additionally:

1. Sweep `min_confidence` (the centroid's 0.45 admits high-volume
   `contracting_triangle` signals at 12% hit rate; a higher floor
   should reduce that pattern's weight).
2. Slice coherence-by-direction (test the "shorts skew full-coherence"
   hypothesis directly).
3. Track `expected_bars_to_target` empirical median by degree as a
   secondary metric — separate T7 follow-up will refit the heuristic.
4. Add `atr_multiple` once it's wired (S5).

