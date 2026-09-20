---
related:
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §8 ablation grid
  - docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md  # §13 roadmap
  - docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md  # §10.1 GO + grid recommendations
  - docs/superpowers/specs/2026-05-06-atr-multiple-wiring-design.md  # S5 closed
---

# Ablation grid sweep — design

## 1. Motivation

The main design `2026-05-03-wave-alpha-design.md` §8 specified an
ablation grid sweep (`entry_trigger × stop_source × target_source ×
min_confidence` = 2×2×3×4 = 48 cells) as the v0.5 finishing step:
*"per cell: hit rate, avg R, expectancy, profit factor, sample size.
Winners ship as defaults; full table published in docs."*

The trade-sim layer spec `2026-05-04-trade-sim-layer-design.md` §13
deferred this grid to a separate spec keyed off
`run_trade_layer(cfg, *, provider, rules) -> TradeLayerResult`. The
sanity run `2026-05-05-trade-sim-sanity-run.md` §10.1 issued a GO
verdict to write that spec, with three concrete recommendations:

1. Sweep `min_confidence` (the centroid's 0.45 admits high-volume
   `contracting_triangle` signals at 12% hit rate; a higher floor
   should reduce that pattern's weight).
2. Slice coherence-by-direction (test the "shorts skew full-coherence"
   hypothesis from E4/E5).
3. Track `expected_bars_to_target` empirical median by degree as a
   secondary metric (separate T7 follow-up will refit the heuristic).
4. Add `atr_multiple` once it's wired (S5 — now closed by
   `2026-05-06-atr-multiple-wiring-design.md`).

This spec scopes the grid. Two further constraints inform the design:

- The sanity-run centroid had **expectancy −0.13R**. A naive 48-cell
  sweep that runs ~17h sequentially around a known-loser centroid
  risks an entire grid of negative expectancies — a result derivable
  from intuition. A tiered approach (cheap screening first, full
  validation only on a positive signal) is more compute-respectful.
- `count_target` and `fib_extension` are equivalent code paths today
  (per commit `da9b0d5`). The original 3-level `target_source` axis
  carries a redundant cell. Pruning is hygiene, not scope cut.

## 2. Goals and non-goals

**Goals**

- Identify a `TradeRules` configuration whose expectancy on the
  curated universe over 2020–2024 is non-pathological — ideally
  positive — and ship it as the validated default.
- Map the sensitivity of trade economics to each axis the original §8
  named (entry, stop, target, min_confidence) plus the
  `direction_modes` axis surfaced by sanity-run findings.
- Produce a sensitivity table + winner config + per-axis ranking that
  can be inlined into `docs/` per the §8 commitment ("full table
  published in docs").
- Surface the per-cell sanity-run findings (direction-sliced
  coherence, per-pattern breakdowns, EBT empirical) so they are
  validated or refuted across the grid.
- Honor the `2026-05-06-atr-multiple-wiring-design.md` S5 closure by
  exercising `atr_multiple` in Phase 2.
- Tier the work: Phase 1 (16 cells, ~6h) screens for a positive region
  before Phase 2 (32 cells, ~12h) maps it.
- Provide a clear, named next-step if Phase 1 finds no positive
  region — no silent retries with new axes.

**Non-goals**

- Bayesian optimization, adaptive sweeps, or any non-static grid
  shape. The grid is a fixed cartesian product per phase.
- Cross-validation or rolling-window grid runs. Each cell runs the
  same 2020–2024 window once, mirroring the sanity-run methodology.
- LLM-blended cells. `LLMMode.DISABLED` is mandatory for backtest
  comparability per `CLAUDE.md`.
- Position sizing, dollar P&L, portfolio-level concurrency caps,
  correlation filters, sector caps. R-units only — same scope as the
  trade-sim layer.
- Per-pattern `min_confidence` floors. If Phase 2 finds
  `contracting_triangle` is the dominant drag and a per-pattern floor
  would help, that lands as a follow-up spec, not a grid axis.
- Re-fitting `expected_bars_to_target` empirically. The grid
  *measures* the EBT distribution per cell; T7 spec applies the fit.
- Real-time progress UI. Logs and post-run artifacts only.
- Parallelism (multiprocessing across cells). Implementation plan can
  choose; the spec only commits to "harness runs N cells with
  comparable metrics."

## 3. Architecture

### 3.1 Two-phase tiered structure

```
Phase 1 (screening, 16 cells)
  axes: min_confidence × direction_modes × entry_trigger
  fixed: stop_source=count_invalidation, target_source=fib_extension,
         slippage=5bps, step=5, universe=curated 50, window=2020-2024

  → trigger evaluation:
       best cell expectancy ≥ +0.05R OR profit_factor ≥ 1.05,
       gated by closed_trades ≥ 200

  GO  → Phase 2
  NO-GO → spec addendum with named next-step (engine work),
          Phase 2 not run

Phase 2 (validation, 32 cells, conditional)
  axes: entry_trigger × stop_source × target_source × min_confidence
        = 2 × 2 × 2 × 4
  pinned: direction_modes = Phase 1 winner
          target_R = 2.0 (only consulted when target_source=fixed_R)
          slippage=5bps, step=5, universe=curated 50, window=2020-2024

  → ranked cells, axis sensitivity, recommended config
```

### 3.2 Phase 1 axes

| Axis | Levels | Why |
|---|---|---|
| `min_confidence` | `0.35, 0.45, 0.55, 0.65` | Original §8 levels. Direct control over signal volume; sanity run found 0.45 admits high-volume `contracting_triangle` at 12% hit rate. |
| `direction_modes` | `[long]`, `[long, short]` | Sanity run E4/E5 + by_direction: shorts dominate (69% of trades) and underperform (-0.25R vs +0.14R for longs). Long-only restriction is the highest-ROI hypothesis. |
| `entry_trigger` | `immediate`, `confirmation` | Original §8 axis. `confirmation` reduces signals + adds 1-bar lag; tests whether selectivity beats timing. |

Total: 4 × 2 × 2 = **16 cells**.

Held-fixed values match the sanity-run centroid (post-fix): `stop_source=count_invalidation`, `target_source=fib_extension`, `slippage_bps=5`, `step=5`, universe `data/universe_curated.txt` (50 tickers), window `2020-01-01..2024-12-31`, `time_stop_mode=degree_based`.

### 3.3 Phase 1 → Phase 2 trigger

GO criteria (both must hold):

- Across the 16 cells, **at least one** has `expectancy ≥ +0.05R` *or* `profit_factor ≥ 1.05`.
- That same cell has `closed_trades ≥ 200` (sample-size floor matching the sanity-run E6 invariant).

NO-GO consequence:

- The grid spec gets a §6 addendum titled "Phase 1 verdict — NO-GO"
  containing the per-cell summary table + the named next-step.
- Phase 2 is **not** run on this branch.
- The named next-step is **not** "try a different grid." It is
  engine work, with three explicit candidates in priority order:
  1. **S6 — multi-degree enumeration** (broaden
     `_DEFAULT_DEGREE_FOR_COUNT = 2` to enumerate at multiple
     degrees). The current single-degree pipeline produces a degenerate
     EBT distribution and may starve the sim of degree-3+ patterns
     that generate larger moves. Most likely high-impact engine fix.
  2. **Pattern-specific `min_confidence`** (give
     `contracting_triangle` a higher floor than `impulse`). The
     sanity run showed `contracting_triangle` at 59% of signals and
     -0.15R; a per-pattern floor is structural rather than a grid
     knob.
  3. **Coherence-bucket recalibration** (the fixed 0.7/0.4 boundaries
     inherited from count-layer reports may be wrong for trade
     economics).

### 3.4 Phase 2 axes

| Axis | Levels | Why |
|---|---|---|
| `entry_trigger` | `immediate`, `confirmation` | Original §8. |
| `stop_source` | `count_invalidation`, `atr_multiple` | Original §8. `atr_multiple` is now wired (S5 closed by `2026-05-06-atr-multiple-wiring-design.md`); Phase 2 is the first head-to-head comparison against `count_invalidation`. |
| `target_source` | `fib_extension`, `fixed_R` (with `target_R=2.0`) | Original §8 was 3 levels but `count_target` == `fib_extension` today. `fixed_R` with `target_R=2.0` matches the original §8 "fixed_2R" naming. |
| `min_confidence` | `0.35, 0.45, 0.55, 0.65` | Original §8. |

Pinned: `direction_modes` = Phase 1 winner. `target_R = 2.0` is consulted only when `target_source = fixed_R`.

Total: 2 × 2 × 2 × 4 = **32 cells**.

### 3.5 Per-cell behavior

Each cell is a single `run_trade_layer(...)` call. The cell receives:

- `BacktestConfig` (universe, start, end, step) shared across the grid
- `TradeRules` constructed from the cell's axis values + held-fixed values
- The shared `provider: OHLCVProvider` instance (cache reused)

The cell produces:

- `TradeLayerResult` (closed trades, headline metrics, breakdowns)
- Markdown report via existing `render_trade_markdown`
- JSON sidecar (existing `result.json` shape)
- Trades CSV (existing shape)

These artifacts mirror the sanity-run shape exactly so a single cell's
output is interpretable as a self-contained backtest report. Cross-cell
analysis is purely additive.

### 3.6 Cross-cell aggregation

`GridResult` exposes:

- `cells: list[GridCell]` — one entry per cell with its axis values + `TradeLayerResult`.
- `ranked_by_expectancy() -> list[GridCell]` — descending order, filtered to `closed_trades ≥ 200`.
- `axis_sensitivity() -> dict[str, dict[level, float]]` — for each axis, the marginal mean expectancy at each level (averaged across all cells holding the other axes constant). Exposes which knob moves the metric most.
- `winner() -> GridCell | None` — `ranked_by_expectancy()[0]` if non-empty, else `None`.
- `by_direction_summary() -> dict[CellId, dict[direction, float]]` — for each cell, expectancy split by long/short. Surfaces interaction effects when `direction_modes` is `[long, short]`.

### 3.7 CLI surface

New subcommand under the existing `backtest` sub-app:

```bash
uv run wave-alpha backtest grid \
  --grid configs/grid_phase1.yaml \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --out reports/grid-2026-05-XX/phase1/
```

The `--grid` YAML defines axes + levels; everything else uses the
existing `backtest trade` flag conventions.

Two configs ship with the spec:

- `configs/grid_phase1.yaml` — Phase 1 (16 cells)
- `configs/grid_phase2.yaml` — Phase 2 (32 cells), with placeholder
  `direction_modes` field documented as "fill in from Phase 1 winner
  before invoking"

### 3.8 Output structure

Per phase, under `reports/grid-2026-05-XX/phase{1,2}/`:

```
grid.md          # top-level summary: ranking, sensitivity, winner, verdict
summary.csv      # one row per cell: axis values + headline metrics
cells/<cell_id>/
  report.md      # render_trade_markdown(...) — same shape as sanity-run
  result.json    # full JSON sidecar
  trades.csv     # one row per TradeRecord
```

Cell ID format: deterministic from axis values, joined by `_`:

```
phase1: entry-immediate_dir-long_minconf-0.45
phase2: entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.45
```

This makes diffing across phases mechanical (Phase 2's
`entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.45`
is the row that should match Phase 1's
`entry-immediate_dir-<winner>_minconf-0.45` for the corresponding
direction-pinned slice).

### 3.9 Lookahead safety

Unchanged from the trade-sim sanity run. `run_trade_layer` already
enforces `PointInTimeView` per call; the grid is a thin orchestration
on top. The synthetic-leak test
`tests/backtest/test_lookahead_guard.py` continues to be the
authoritative check.

The grid harness adds no new bar-fetch sites and does not bypass the
sanity-run's plumbing. Specifically: it must not collect bars once at
the grid level and pass them to all cells — each cell goes through
`run_trade_layer` which goes through the cached provider which goes
through `PointInTimeView`. This is enforced by lint-style review, not
by an additional test (the existing leak guard already covers the
boundary).

## 4. Tests

The grid is orchestration code, not numerical engine code. Tests focus
on shape, determinism, and aggregation correctness — not on cell
metrics (those are the trade-sim layer's contract).

### 4.1 `test_grid_config_yaml_round_trips`
A YAML file with axes + levels parses into `GridConfig`; calling
`GridConfig.cells()` returns the cartesian product of axis levels with
held-fixed values applied. Shape pinned: 16 cells from a Phase 1
config, 32 from a Phase 2 config.

### 4.2 `test_grid_cell_ids_are_deterministic`
Two `GridConfig` instances with the same axes + levels produce the
same cell IDs in the same order. Pins reproducibility across runs.

### 4.3 `test_run_grid_calls_run_trade_layer_per_cell`
Stub `run_trade_layer` to return a fixed `TradeLayerResult`. Run a
2-cell grid. Assert: `run_trade_layer` called exactly twice, once with
each cell's `TradeRules`. Pins the orchestration boundary.

### 4.4 `test_grid_axis_sensitivity_marginal_means`
Construct a synthetic `GridResult` with hand-set per-cell expectancies.
Assert `axis_sensitivity()` returns the correct marginal means for
each axis × level combination. Numerical correctness, no engine
involvement.

### 4.5 `test_grid_winner_respects_sample_size_floor`
Construct a `GridResult` where the highest-expectancy cell has
`closed_trades = 50` (below the 200 floor) and the second-highest has
`closed_trades = 1000`. Assert `winner()` returns the second cell.
Pins the §3.6 sample-size constraint.

### 4.6 `test_grid_phase1_trigger_go_when_threshold_met`
Construct a `GridResult` with one cell at `expectancy = +0.07R,
closed_trades = 500`. Assert the trigger function returns GO.

### 4.7 `test_grid_phase1_trigger_no_go_when_threshold_unmet`
Construct a `GridResult` with all cells at `expectancy ≤ 0`. Assert
NO-GO. Then construct one where the highest cell is `+0.06R` but
`closed_trades = 100`. Assert NO-GO (sample-size gate).

### 4.8 `test_grid_no_lookahead`
The existing `tests/backtest/test_lookahead_guard.py` synthetic-leak
test must still pass when run after a grid (no new fetch sites or
slicing surfaces introduced).

## 5. File structure

**New:**
- `src/wave_alpha/backtest/grid.py` — `GridConfig`, `GridCell`, `GridResult`, `run_grid(...)`, `phase1_trigger(...) -> Literal["GO","NO-GO"]`. Single file, single responsibility. ~250 LOC budget.
- `src/wave_alpha/backtest/grid_report.py` — `render_grid_markdown(result, *, config) -> str`. Single file. ~150 LOC budget.
- `configs/grid_phase1.yaml` — Phase 1 axis definitions.
- `configs/grid_phase2.yaml` — Phase 2 axis definitions.
- `tests/backtest/test_grid.py` — tests 4.1–4.7.

**Modified:**
- `src/wave_alpha/cli/app.py` — add `backtest grid` subcommand. Lazy imports per existing convention.
- `pyproject.toml` — version bump (decided at merge time per the §9 S4 pattern from prior specs; likely v0.5.6).
- `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` (this file) — post-Phase-1 and post-Phase-2 results sections (§6) filled in.

**Not modified:**
- `src/wave_alpha/backtest/trade_layer.py` — `run_trade_layer` is the orchestration point, untouched.
- `src/wave_alpha/trades/derive.py` — no engine changes from the grid spec.
- `src/wave_alpha/pipeline.py` — no pipeline changes.

## 6. Results

### 6.1 Phase 1 results

> **Run date:** 2026-05-07 (UTC)
> **Cache state:** warm (49/50 tickers fully cached across all 3 intervals at run start)
> **Wall time:** 7h 29min (16 cells × ~28 min average; modestly over the ~22 min/cell sanity-run extrapolation, attributable to per-cell signal acceptance variance)
> **Code SHA:** `8b25adb` (`feat(cli): backtest grid subcommand`)
> **Universe:** 50 tickers (`data/universe_curated.txt`)

Per-cell summary (16 rows):

| cell_id | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| minconf-0.35_dir-long_entry-immediate | 2495 | 0.24 | +0.16 | +0.16 | 1.35 |
| minconf-0.35_dir-long_entry-confirmation | 1603 | 0.33 | +0.11 | +0.11 | 1.27 |
| minconf-0.35_dir-both_entry-immediate | 4632 | 0.14 | -0.13 | -0.13 | 0.70 |
| minconf-0.35_dir-both_entry-confirmation | 2619 | 0.22 | -0.06 | -0.06 | 0.86 |
| minconf-0.45_dir-long_entry-immediate | 2418 | 0.25 | +0.16 | +0.16 | 1.35 |
| minconf-0.45_dir-long_entry-confirmation | 1554 | 0.33 | +0.11 | +0.11 | 1.27 |
| minconf-0.45_dir-both_entry-immediate | 4577 | 0.14 | -0.13 | -0.13 | 0.71 |
| minconf-0.45_dir-both_entry-confirmation | 2579 | 0.22 | -0.06 | -0.06 | 0.87 |
| minconf-0.55_dir-long_entry-immediate | 2151 | 0.25 | **+0.17** | **+0.17** | **1.39** |
| minconf-0.55_dir-long_entry-confirmation | 1392 | 0.33 | +0.14 | +0.14 | 1.33 |
| minconf-0.55_dir-both_entry-immediate | 4218 | 0.14 | -0.17 | -0.17 | 0.63 |
| minconf-0.55_dir-both_entry-confirmation | 2351 | 0.21 | -0.05 | -0.05 | 0.89 |
| minconf-0.65_dir-long_entry-immediate | 2083 | 0.25 | +0.16 | +0.16 | 1.34 |
| minconf-0.65_dir-long_entry-confirmation | 1335 | 0.33 | +0.15 | +0.15 | 1.35 |
| minconf-0.65_dir-both_entry-immediate | 4139 | 0.14 | -0.17 | -0.17 | 0.63 |
| minconf-0.65_dir-both_entry-confirmation | 2324 | 0.21 | -0.05 | -0.05 | 0.89 |

Axis sensitivity:

| axis | level | mean expectancy | mean PF | mean closed_trades |
|---|---|---:|---:|---:|
| min_confidence | 0.35 | +0.019 | 1.05 | 2837 |
| min_confidence | 0.45 | +0.020 | 1.05 | 2782 |
| min_confidence | 0.55 | +0.024 | 1.06 | 2528 |
| min_confidence | 0.65 | +0.022 | 1.05 | 2470 |
| direction_modes | [long] | **+0.145** | **1.33** | 1878 |
| direction_modes | [long, short] | **−0.103** | **0.77** | 3429 |
| entry_trigger | immediate | +0.005 | 1.01 | 3339 |
| entry_trigger | confirmation | +0.038 | 1.09 | 1969 |

Best cell:

```
cell_id: minconf-0.55_dir-long_entry-immediate
axes: min_confidence=0.55, direction_modes=[long], entry_trigger=immediate
closed_trades: 2151
expectancy: +0.174 R
profit_factor: 1.39
hit_rate: 0.25
```

**Three findings:**

1. **`direction_modes` is by far the dominant axis** — the long-only mean (+0.145R) is 0.25R better than the both-directions mean (−0.103R), an 8× larger sensitivity than any other axis. Every long-only cell is positive; every both-directions cell is negative. The sanity-run §10.1 hypothesis ("shorts are the problem") is conclusively confirmed.

2. **`min_confidence` sensitivity is essentially nil** — all 4 levels cluster within 0.005R of each other. The companion analyses in `reports/grid-2026-05-06/coherence_continuous_analysis.md` predicted this: high-coherence trades aren't structurally bad in the post-2023 (4h-available) regime, so a higher LLM-blended-score floor doesn't filter out a meaningful loser bucket. The implication: **`min_confidence` is not a load-bearing knob in the current regime** — defending it as a default doesn't matter much, but it also can't be used to compensate for a poorly-chosen `direction_modes`.

3. **`entry_trigger`'s marginal mean (+0.038 confirmation vs +0.005 immediate) is misleading** — within long-only cells, `immediate` wins or ties at every `min_confidence` level; the confirmation outperformance comes from filtering more shorts (which lose). The actual best cell uses `immediate`. Phase 2 should sweep both regardless.

### 6.2 Phase 1 verdict

- [x] **GO** — proceed to Phase 2 with `direction_modes = [long]` pinned.
- [ ] **NO-GO** — fill in the addendum below; Phase 2 not run.

**Reasoning:** Best cell `minconf-0.55_dir-long_entry-immediate` cleared the trigger (expectancy +0.174R ≥ +0.05R, PF 1.39 ≥ 1.05, closed_trades 2151 ≥ 200). Six of the eight long-only cells cleared the trigger by both expectancy and PF criteria — the positive region is broad, not a singleton. Pinning `direction_modes = [long]` for Phase 2 is well-supported: long-only is positive across every other axis combination tested, while both-directions is negative across every combination. The companion direction-coherence and continuous-coherence analyses (`reports/grid-2026-05-06/{direction,coherence_continuous}_analysis.md`) independently confirmed that shorts uniformly lose at every coherence × tf_count slice and that the prior "high coherence is bad" finding is largely a 2-TF/regime artifact rather than a structural engine bias.

#### Phase 1 NO-GO addendum (only if NO-GO)

_(not applicable — verdict is GO)_

### 6.3 Phase 2 results

> **Run date:** 2026-05-08 (UTC)
> **Cache state:** warm (cache populated by Phase 1, no fetches needed)
> **Wall time:** 15h 55min (32 cells × ~30 min average)
> **Code SHA:** `38a4c54` (Phase 2 config pin commit)
> **Pinned `direction_modes` from Phase 1:** `[long]`

Per-cell summary (32 rows, key cells highlighted):

| cell_id | n | hit | avg R | expectancy | PF |
|---|---:|---:|---:|---:|---:|
| entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.35 | 2495 | 0.24 | +0.16 | +0.16 | 1.35 |
| entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.45 | 2418 | 0.25 | +0.16 | +0.16 | 1.35 |
| entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.55 | 2151 | 0.25 | +0.17 | +0.17 | 1.39 |
| entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.65 | 2083 | 0.25 | +0.16 | +0.16 | 1.34 |
| entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.35 | 1861 | 0.40 | +0.27 | +0.27 | 1.44 |
| **entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.45** | **1818** | **0.40** | **+0.27** | **+0.27** | **1.44** |
| entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.55 | 1622 | 0.40 | +0.26 | +0.26 | 1.42 |
| entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.65 | 1560 | 0.40 | +0.25 | +0.25 | 1.40 |
| entry-confirmation_stop-count-invalidation_target-fib-extension_minconf-0.35 | 1603 | 0.33 | +0.11 | +0.11 | 1.27 |
| entry-confirmation_stop-count-invalidation_target-fib-extension_minconf-0.45 | 1554 | 0.33 | +0.11 | +0.11 | 1.27 |
| entry-confirmation_stop-count-invalidation_target-fib-extension_minconf-0.55 | 1392 | 0.33 | +0.14 | +0.14 | 1.33 |
| entry-confirmation_stop-count-invalidation_target-fib-extension_minconf-0.65 | 1335 | 0.33 | +0.15 | +0.15 | 1.35 |
| entry-confirmation_stop-atr-multiple_target-fib-extension_minconf-0.35 | 1347 | 0.48 | +0.27 | +0.27 | **1.52** |
| entry-confirmation_stop-atr-multiple_target-fib-extension_minconf-0.45 | 1318 | 0.48 | +0.26 | +0.26 | 1.51 |
| entry-confirmation_stop-atr-multiple_target-fib-extension_minconf-0.55 | 1190 | 0.47 | +0.26 | +0.26 | 1.51 |
| entry-confirmation_stop-atr-multiple_target-fib-extension_minconf-0.65 | 1143 | 0.47 | +0.25 | +0.25 | 1.48 |

Cells with `target_source=fixed_R` are omitted from this excerpt — see §6.3 finding #3 below; their metrics are bit-for-bit identical to the corresponding `fib_extension` cells.

Axis sensitivity:

| axis | level | mean expectancy | mean PF | mean closed_trades |
|---|---|---:|---:|---:|
| entry_trigger | immediate | +0.211 | 1.39 | 2001 |
| entry_trigger | confirmation | +0.195 | 1.41 | 1360 |
| stop_source | count_invalidation | +0.145 | 1.33 | 1878 |
| stop_source | **atr_multiple** | **+0.261** | **1.47** | 1482 |
| target_source | fib_extension | +0.203 | 1.40 | 1680 |
| target_source | fixed_R | +0.203 | 1.40 | 1680 |
| min_confidence | 0.35 | +0.203 | 1.40 | 1826 |
| min_confidence | 0.45 | +0.201 | 1.39 | 1777 |
| min_confidence | 0.55 | +0.208 | 1.41 | 1588 |
| min_confidence | 0.65 | +0.201 | 1.39 | 1530 |

Top 3 cells by expectancy:

| rank | cell_id | closed_trades | hit_rate | expectancy | PF |
|---:|---|---:|---:|---:|---:|
| 1 | entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.45 | 1818 | 0.40 | +0.272 | 1.44 |
| 2 | entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.35 | 1861 | 0.40 | +0.272 | 1.44 |
| 3 | entry-confirmation_stop-atr-multiple_target-fib-extension_minconf-0.35 | 1347 | 0.48 | +0.270 | 1.52 |

Recommended default config (the cell to ship as new `TradeRules()` defaults):

```yaml
direction_modes: [long]      # Phase 1 winner pinning, +0.25R sensitivity vs [long, short]
entry_trigger: immediate     # marginal; confirmation has higher hit rate but smaller sample
stop_source: atr_multiple    # +0.116R sensitivity over count_invalidation — the dominant Phase 2 lever
target_source: fib_extension # currently equivalent to fixed_R (see finding #3); harmless choice
min_confidence: 0.45         # axis is essentially flat; 0.45 = current default, no change
# slippage_bps: 5            # held fixed across grid
# step: 5                    # held fixed
# target_R: 2.0              # ignored when target_source=fib_extension
```

**Three findings:**

1. **`stop_source=atr_multiple` is the dominant Phase 2 lever.** Mean
   expectancy lifts from +0.145R to +0.261R — a 0.116R sensitivity gap,
   the largest in either phase. Combined with the Phase 1
   `direction_modes` lift (+0.25R), the recommended config is +0.272R
   vs the original sanity-run centroid's −0.13R, a swing of +0.4R.
   The atr_multiple wiring spec
   (`2026-05-06-atr-multiple-wiring-design.md`) said the grid would
   sweep both stop sources head-to-head once wired; the head-to-head
   verdict is unambiguous.

2. **Hit rate is dramatically higher under `atr_multiple`** — 0.40 for
   `immediate` and 0.48 for `confirmation`, vs 0.24–0.33 for
   `count_invalidation`. The mechanism is consistent with the
   atr_multiple wiring spec's premise: a vol-scaled fixed stop
   (anchor ± 2 × ATR) gives signals room to breathe vs the
   count-derived stop, which often sits very close to the entry
   anchor (one pivot away) and gets hit on noise. The trade-off is
   ~30% lower closed_trades count under atr_multiple (volatility-
   sized stops trip the time-stop more often, presumably because
   trades take longer to reach target).

3. **`target_source` axis is degenerate.** Every (entry, stop,
   min_confidence) pair produced bit-for-bit identical metrics across
   `fib_extension` and `fixed_R`. Investigation: `_target_price` in
   `derive.py` ignores `rules.target_source` entirely; the
   `_target_price_fixed_r` helper exists but is never called from
   `derive_signal`. Mirror of the prior atr_multiple S5 wiring gap.
   16 of the 32 Phase 2 cells (50% of wall-time) duplicated the
   non-`fixed_R` path with no actual variation. Logged as **§9 G7**;
   does not invalidate the Phase 2 winner verdict (the dominant
   stop_source signal remains clear). The recommended config uses
   `target_source=fib_extension` — the de facto behavior — and a
   follow-up spec will wire `fixed_R` so a future grid can sweep it.

4. **`entry_trigger` and `min_confidence` are essentially flat.** Both
   axes have <0.02R sensitivity. The original §8 design said
   "Immediate entry — the count IS the signal; confirmation gates
   dilute the timing edge. Validated by ablation in v0.5." The
   ablation says: validated, but only marginally. Confirmation
   produces a higher hit rate (0.40 → 0.48 under atr_multiple) at
   smaller sample size, with essentially the same expectancy.
   `min_confidence` doesn't move the needle in any direction —
   consistent with the companion analyses showing coherence is
   weakly anti-correlated with realized R.

Direction-by-cell breakdown is **not applicable** — `direction_modes`
was pinned to `[long]` per the Phase 1 verdict, so no shorts to
slice.

### 6.4 Verdict

- [x] **Defaults updated** (partial). The Phase 2 winner identifies
  one clear, low-philosophical-risk default change to ship as part of
  this spec's merge: `stop_source = "atr_multiple"`. Two recommended
  changes (`direction_modes = [long]` and the `entry_trigger` /
  `min_confidence` choices) are documented in this spec's §6.3
  "Recommended default config" block but **not** applied to
  `TradeRules()` defaults — they're either philosophical (long-only
  default conflicts with the main design's "EW is structurally
  bidirectional" principle) or marginal (the latter two axes are
  essentially flat).
- [ ] **No update.** Not applicable — the atr_multiple lift is too
  large to ignore.

**Reasoning:**

`stop_source` flips from `count_invalidation` to `atr_multiple`. The
+0.116R sensitivity is engine-level evidence; the empirical hit-rate
lift (0.24–0.33 → 0.40–0.48) corroborates the mechanism (vol-scaled
stops give trades room to breathe vs tight count-derived stops). The
atr_multiple path is wired and tested
(`2026-05-06-atr-multiple-wiring-design.md`); shipping it as default
is a straightforward release decision. A follow-up commit on `main`
post-merge updates `TradeRules.stop_source` default to
`"atr_multiple"` along with the v0.5.6 release.

`direction_modes` stays `[long, short]` as the schema default. The
grid evidence is unambiguous (+0.25R sensitivity), but defaulting the
system to long-only is a product-level decision that affects how
users frame the tool (a directional-trend filter vs a structural
analyzer). The recommendation goes in the §6.3 config block + a v0.6
roadmap note; users opting for the empirically-best config write a
YAML with `direction_modes: [long]`.

`entry_trigger` and `min_confidence` defaults stay unchanged
(`immediate` and `0.45` respectively). They're both within the noise
floor of the sensitivity table (<0.02R gap across all levels). The
existing defaults aren't suboptimal in any meaningful sense.

`target_source` would have stayed `count_target` regardless — it's
currently a no-op axis (G7), and changing the default to a different
no-op label would be theater.

The follow-up sequence post-merge:

1. **EBT empirical fit spec** (`2026-05-07-ebt-empirical-fit-design.md`)
   — already drafted on `feat/ebt-empirical-fit`, plan ready.
   Validates against the cell shipped here (Phase 2 winner).
2. **`target_source` wiring** (G7) — mirror of the atr_multiple wiring
   spec; small follow-up.
3. **Per-pattern `min_confidence`** (G3) — `contracting_triangle`
   dominance was the original motivation; this grid didn't slice
   per-pattern within long-only cells, so the question is open. May
   land alongside G7 or separately.

## 7. Sim-bug fix ledger

If any cell exposes a sim bug (e.g. a state-machine invariant
violation that wasn't surfaced by the sanity run), record it here in
the same fix-as-you-go pattern as the sanity-run §7. Empty if the grid
is clean.

| # | Commit | Invariant violated | Fix summary |
|---|---|---|---|
| | | | |

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Cold cache → 17h+ runtime, multi-day if interrupted | Pre-warm pass before kickoff, mirroring the sanity-run procedure. The `scripts/prewarm_4h_cache.py` helper is already in the repo. |
| Phase 1 NO-GO delivers an inconclusive spec | §3.3 names the explicit next-step. The spec is **not** appended with retry grids — it ends with a pointer to engine work. |
| Phase 2 winner is direction-pinned to a Phase 1 finding that was a sample-size artifact | Phase 1's GO trigger requires `closed_trades ≥ 200` per cell; Phase 2's per-cell `by_direction` breakdown surfaces interaction effects. If the breakdown contradicts the Phase 1 pin, that's a finding documented in §6.3 and the recommended default may keep `direction_modes = [long, short]` regardless. |
| 32 cells × 50 tickers × 5y produces ~64MB of trades.csv aggregate | `reports/` is gitignored. Durable record is `summary.csv` + spec-inlined verdicts. |
| Right-edge model artifacts drift between dev machines | Same risk as sanity run; same mitigation: shipped logistic + calibrated heuristic are committed (`f7d20f3`, `09fa647`). Verify `git status` before kickoff. |
| `target_R = 2.0` is hardcoded; a different value might win Phase 2 | This is intentional — the original §8 grid said "fixed_2R" verbatim. If Phase 2 finds `fixed_R` competitive, a follow-up spec sweeps `target_R` itself (1.5, 2.0, 2.5, 3.0) to refine. Not in this spec. |
| A new sim bug is hidden by aggregation across 32 cells | Each cell ships its own `report.md` with the same B-bar structure as the sanity run; a cell-level sanity check (re-run `scripts/inspect_sanity_run.py` against any cell with surprising metrics) is the contingency. |

## 9. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| G1 | Should the harness parallelize across cells (multiprocessing)? | Implementation plan | Pure engineering tradeoff; spec only commits to "comparable metrics per cell." 8-core machine cuts 17h → ~2.5h. |
| G2 | Should `target_R` be swept (1.5, 2.0, 2.5)? | Follow-up spec post-Phase-2 | Only if Phase 2 finds `fixed_R` competitive. Otherwise `target_R = 2.0` is a frozen detail. |
| G3 | Per-pattern `min_confidence` floors | Follow-up spec post-Phase-2 | Listed in §3.3 as a Phase 1 NO-GO escape candidate. If Phase 2 surfaces `contracting_triangle` as the dominant drag, this is the first follow-up. |
| G4 | Should `expected_bars_to_target` be empirically refit before Phase 2? | **Resolved by `2026-05-07-ebt-empirical-fit-design.md`** | The follow-up spec ships post-grid-merge with a single-cell validation pass against the Phase 2 winner.  Order is preserved: Phase 1 → Phase 2 → EBT fit. |
| G5 | Should multi-degree enumeration (S6) ship before this grid? | Phase 1 NO-GO case | If Phase 1 is GO, the single-degree pipeline produced enough signal to validate; multi-degree work is then orthogonal. If Phase 1 is NO-GO, S6 is the prime candidate next-step per §3.3. |
| G6 | Per-cell empirical EBT distribution | Recorded in `result.json`, not aggregated | Each cell's `result.json` already includes the data needed; cross-cell EBT aggregation is a T7 input, not a grid-level concern. |
| G7 | Wire `target_source` (fixed_R + count_target) into `derive_signal` | Separate follow-up spec | Phase 2 §6.3 finding #3: `_target_price` ignores `rules.target_source`; `_target_price_fixed_r` exists but is never called. Mirror of the atr_multiple S5 wiring gap. The Phase 2 grid wasted 16 of 32 cells on a non-functional axis. Follow-up spec wires the dispatch + adds `target_R` sweep capability for a future grid. |

## 10. Roadmap impact

This spec is the v0.5 finishing step per the main design's §11
roadmap row: **"v0.5 — Backtest trade-sim layer; ablation grid sweep;
sensitivity table published in docs."**

When merged, the v0.5 commitment is structurally complete:
- v0.5.0–0.5.3: trade-sim layer + 4h cache + sanity-run prerequisites
- v0.5.4: trade-sim sanity-run findings + `derive.py` direction fixes
- v0.5.5: `atr_multiple` wiring (S5)
- **v0.5.6 (this spec): ablation grid + recommended defaults**

Post-v0.5, the natural follow-ups are:
- **T7 empirical EBT refit** — keyed off this grid's per-cell EBT data.
- **Per-pattern `min_confidence` floors (G3)** — if Phase 2 surfaces a dominant-pattern drag.
- **Multi-degree enumeration (S6)** — engine work, orthogonal to grid.
- **v0.6: continuous-improvement loop** — local trade-outcome capture, personalization, etc., per the main design §10–§11.

If Phase 1 verdict is NO-GO, the v0.5 finishing step is **not**
complete on the basis of this spec alone — the named next-step (engine
work) is the real v0.5 finisher, and this spec lands as a "we tried,
here's what we learned" record. The roadmap row is then carried into
v0.6.
