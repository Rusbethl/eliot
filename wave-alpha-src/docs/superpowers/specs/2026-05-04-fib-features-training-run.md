# Fib Features Training Run — 2026-05-04

**Status:** PROMOTE_CALIBRATION (gate passes vs heuristic), but **HOLD vs v1** — keep v1 as the bundled logistic artifact.

**Goal:** Retrain the right-edge logistic model with the 14 new Fib feature slots from
`docs/superpowers/specs/2026-05-03-fib-structural-features-design.md` and decide whether
to swap the bundled artifact. Generated artifact saved alongside v1 for documentation
and future re-evaluation.

## Configuration

- Command: `wave-alpha train right-edge --with-fib-features --start 2021-01-01 --end 2024-12-31 --seed 42 --write --output ...v2.json`
- Universe: `data/universe_curated.txt` (50 tickers, mixed sectors / vol regimes / IPO ages)
- Walk-forward: quarterly folds, 4-quarter warmup
- Seed: 42 (deterministic)
- Feature schema: 19 fields (5 legacy + 14 new — 7 raw fib ratios + 7 presence indicators)
- Bundled artifacts at time of run:
  - `right_edge_logistic_v1.json` — 5-feature, gate verdict PROMOTE_CALIBRATION
  - `right_edge_calibrated_heuristic_v1.json` — unaffected by this run

## Dataset

- Training rows assembled: **3806** (single number — same `assemble_training_rows`
  call as v1's training, plus the new `top_count` plumbing per Task 15-and-friends so
  fib slots are populated in training rows the same way they're populated at serve time)
- Walk-forward OOS predictions: **2869**
- One delisted symbol failed download (`BRK.B`); skipped silently

## Results

### Logistic vs heuristic (gate metric)

| Metric | v2 (19-feat) | Heuristic baseline |
|---|---|---|
| Brier 95% CI | [0.2349, 0.2388, 0.2428] | [0.2305, 0.2406, 0.2498] |
| ECE degree 0 | 0.0792 | 0.1998 |
| ECE degree 1 | 0.0561 | 0.1403 |
| ECE degree 2 | 0.0466 | 0.1350 |
| ECE degree 3 | 0.0382 | 0.1066 |
| ECE degree 4 | 0.0505 | 0.1367 |
| **Verdict** | **PROMOTE_CALIBRATION** |   |

The gate compares logistic-vs-heuristic. Brier CIs overlap (essentially tied), but
per-degree calibration is dramatically better (~60% ECE reduction at every degree).
That's the standard PROMOTE_CALIBRATION signature.

### v2 vs v1 (informal — gate doesn't run this comparison)

| Metric | v1 (5-feat) | v2 (19-feat) | Δ |
|---|---|---|---|
| Logistic Brier mean | 0.2318 | 0.2388 | **+0.0070** (worse) |
| Logistic Brier CI | [0.2282, 0.2318, 0.2350] | [0.2349, 0.2388, 0.2428] | shifted right |
| ECE degree 0 | 0.0603 | 0.0792 | **+0.0189** (worse) |
| ECE degree 1 | 0.0492 | 0.0561 | +0.0069 (worse) |
| ECE degree 2 | 0.0552 | 0.0466 | -0.0086 (better) |
| ECE degree 3 | 0.0403 | 0.0382 | -0.0021 (slightly better) |
| ECE degree 4 | 0.0472 | 0.0505 | +0.0033 (slightly worse) |

CIs overlap on Brier so the regression isn't clearly outside noise, but on a
metric-by-metric scoreboard v2 loses 3 of 5 calibration buckets.

### v2 top-|coef| features

```
+1.2408  retracement_progress     ← dominant (legacy)
+0.2934  atr_regime               ← legacy
-0.0567  fib_w4_w3_retrace
-0.0395  fib_c_a_ratio
-0.0368  fib_w3_w1_extension
-0.0361  fib_w2_w1_retrace
+0.0280  fib_w5_w1_ratio
+0.0241  fib_c_a_ratio_present
…
```

Every fib coefficient (raw or indicator) sits at < 1/20 of `retracement_progress`.
The model is treating fib as low-signal noise. Without a strong feature among the
14 new ones, the extra capacity hurts slightly — classic too-many-features-on-thin-
data behavior even though 3806 rows / 19 params = 200 rows/param is not strictly
underpowered.

## Decision: branch B — keep v1 bundled, defer v2

**Action taken:**
- v1 (`right_edge_logistic_v1.json`) remains the bundled artifact.
- v2 is committed alongside v1 at `right_edge_logistic_v2.json` for reproducibility
  and future re-evaluation. **Not loaded by default.**
- `_DEFAULT_LOGISTIC_PATH` in `loader.py` continues to point at v1 — no production
  prediction change.
- Phase 1 (`engine_score` Fib confluence) ships unconditionally — it's a separate,
  unrelated win.
- Phase 2 plumbing (`RightEdgeFeatures` 14 new fields, dynamic `_vector`, top-count
  feeding through `assess_from_bars` and `assemble_training_rows`) ships and remains
  load-bearing for future experiments.

**Why not ship v2:**
1. v2 fails the implicit "be at least as good as the existing artifact" test — Brier
   regresses by 0.7%, half of per-degree calibration buckets regress.
2. Fib coefficients are tiny across the board — the new features don't carry
   meaningful signal on this dataset.
3. The PROMOTE_CALIBRATION verdict is a vs-heuristic comparison that v1 already
   passes. Both v1 and v2 beat the heuristic; v1 beats v2. Therefore v1 wins.

**Why not revert Phase 2 plumbing:**
- Defaults are zero — old artifacts produce identical predictions because their
  `feature_order` lists exactly the legacy 5 fields and the dynamic `_vector` reads
  exactly those.
- The plumbing is needed by *any* future training experiment. Reverting and re-adding
  it later would be churn.

## Hypotheses for follow-up

- **Sample selection.** Walk-forward OOS rows skew toward common pivot shapes that
  the heuristic already handles well; the structural features may matter on rarer
  edge cases not represented in the 2869 OOS predictions.
- **Train/serve top-count consistency.** Training computes the top count from the
  `initial` snap pivots and reuses it for every tentative pivot in the snap;
  serve-time `assess_from_bars` does the same. Consistent today, but means earlier
  tentative pivots in a snap are scored with a "future" count. A point-in-time top
  count per pivot might surface different feature signal — would require expensive
  per-pivot re-enumeration.
- **Pattern coverage.** Curated bands cover only impulse + zigzag (~80% of patterns
  the engine emits). Counts that hit other patterns return zero ratios + zero
  indicators. If a meaningful share of training rows correspond to non-curated
  patterns, fib features for those rows are uninformative — the model can't learn
  from indicators-all-zero rows.
- **Outcome label sensitivity.** Walking 4-quarter warmup may be coarse; the logistic
  may underfit short-term confirmation patterns where fib levels actually matter. A
  shorter warmup (or rolling-window CV) might surface different coefficients.

## Caveats

- One delisted symbol (`BRK.B`) skipped silently — universe is effectively 49 tickers.
- 4 of the 50 are post-2020 IPOs (SNOW, PLTR, RBLX, COIN) — small bar histories,
  may dominate certain quarters.
- Both artifacts trained with `seed=42` for reproducibility — different seeds may
  shift the rankings; one seed is not strong evidence either way for marginal results.

## Files

| Path | Action |
|---|---|
| `src/wave_alpha/right_edge/models/right_edge_logistic_v1.json` | Unchanged — remains bundled |
| `src/wave_alpha/right_edge/models/right_edge_logistic_v2.json` | NEW — committed for documentation; not loaded |
| `src/wave_alpha/right_edge/loader.py:_DEFAULT_LOGISTIC_PATH` | Unchanged — still points at v1 |
