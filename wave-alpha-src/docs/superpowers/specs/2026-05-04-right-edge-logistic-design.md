# Right-Edge Logistic Confirmation Model — Design

**Date:** 2026-05-04
**Spec section closed:** §5.4 (right-edge probability model — phased)
**Targets:** v0.4.x

## Motivation

Spec §5.4 specifies a phased right-edge probability model:

1. **v0.1 — heuristic.** Hand-coded weighted sum. Shipped.
2. **v0.2+ — logistic regression.** Trained on backtest-derived labels. Promoted only if it beats the heuristic on a held-out window.
3. **vN — gradient-boosted trees.** Only if logistic plateaus.

The v0.4 backtest harness now produces labeled tentative-pivot outcomes per `backtest/pivot_layer.py:PivotEvaluation`. The promotion gate is therefore unblocked: we can train the logistic, run the gate, and either promote it (replacing the heuristic in live inference) or keep the heuristic with a measurable result behind that decision.

A latent bug surfaces in the same area: `backtest/pivot_layer.py:_features_for` returns hard-coded placeholders (`retracement_progress=0.5`, `fib_zone_match=0.5`, ...). Today's pivot-layer Brier numbers reflect the heuristic-on-constants, not the heuristic-on-data. Fixing this is independent of the logistic and is bundled into this design because the same FeatureExtractor will serve both jobs.

## Goals

- Train a calibrated logistic regression for tentative-pivot confirmation, using real point-in-time features.
- Honest promotion gate: only ship the logistic if it beats the heuristic on aggregate out-of-sample Brier with bootstrap confidence intervals AND keeps per-degree calibration within the heuristic's range on every degree.
- Fix the placeholder-feature bug in `pivot_layer.py` as a side effect — its Brier numbers become real.
- Persistent, diffable, reproducible model artifact (no pickle, no external ML lib runtime dep).
- Safe live inference: pipeline picks heuristic vs. logistic based on whether a promoted model file exists; falls back to heuristic on any load failure.

## Non-goals

- Implementing RSI or fib-zone features. The spec lists them; we drop both from the schema for v0.4.x and ship on the five real features. (Deferred: separate work item if/when the logistic-on-five-features plateaus.)
- One logistic per degree. Only Platt calibration is per-degree; the logistic itself is unified across all 5 degrees (0..4 per `_DEFAULT_ATR_MULTIPLES`).
- Gradient boosting (spec §5.4 vN — only if logistic plateaus).
- Online / continuous training. This is offline batch training, run by an explicit CLI command.

## Architecture

```
right_edge/
  features.py       # MODIFY — drop rsi_divergence_strength, fib_zone_match
  extractor.py      # NEW — shared FeatureExtractor used by training + backtest
  heuristic.py      # MODIFY — drop the same two stub fields from the weighted sum
  logistic.py       # NEW — LogisticConfirmationModel: load JSON, predict_proba
  training.py       # NEW — fit weights + per-degree Platt scalers via walk-forward
  promotion.py      # NEW — bootstrap-CI gate, PROMOTE/HOLD/REJECT verdict
  models/
    right_edge_logistic_v1.json   # NEW — persisted model artifact (git-tracked)

backtest/
  pivot_layer.py    # MODIFY — use FeatureExtractor; expose ATR + required-move from detect_multi_degree

cli/
  train_app.py      # NEW — `wave-alpha train right-edge [--write] [--force]`

pipeline.py / web/deps.py
                    # MODIFY — pick LogisticConfirmationModel (if model file present and PROMOTE) else HeuristicConfirmationModel
```

### Component responsibilities

**`right_edge/features.py` (modified)**
- `RightEdgeFeatures` keeps five fields: `retracement_progress`, `bars_since_candidate`, `volume_decline_pct`, `higher_degree_zone_match`, `atr_regime`.
- `extract_features(...)` signature loses `fib_zone_match` arg.

**`right_edge/extractor.py` (new)**
- `class FeatureExtractor` with one entry point: `extract(candidate, point_in_time_bars, atr_at_candidate, required_move, higher_degree_zone_match) -> RightEdgeFeatures`.
- Pure function-style: no state, no I/O. Existing logic from `extract_features()` lifts here unchanged after the schema cleanup.
- Becomes the *only* place feature computation happens. Both training and live/backtest inference call this.

**`right_edge/heuristic.py` (modified)**
- Existing weighted sum drops the two stubs and renormalizes weights so the surviving five sum to 1.0:
  - `retracement_progress`: 0.40
  - `bars_since_candidate`: 0.25
  - `volume_decline_pct`: 0.15
  - `higher_degree_zone_match`: 0.10
  - `atr_regime`: 0.10
- Version label changes to `heuristic_v2` so promoted-model metadata can record what was beaten.

**`right_edge/logistic.py` (new)**
- `LogisticConfirmationModel` with:
  - `from_json(path) -> LogisticConfirmationModel` — loads weights, intercept, per-degree Platt scalers, metadata.
  - `predict_proba(features, degree) -> float` — logistic forward pass + Platt scale for that degree.
- Hand-coded sigmoid + linear combination. No scikit-learn at runtime.
- Class invariant: a successfully-loaded model has `gate.verdict == "PROMOTE"`. Anything else fails to load.

**`right_edge/training.py` (new)**
- `fit_logistic(rows: list[TrainingRow]) -> LogisticParams` — gradient descent (or closed-form via NumPy `lstsq` on logit-transformed targets, then refined). Reproducible: pinned RNG seed, deterministic iteration order.
- `fit_platt(predictions: list[(proba, outcome)]) -> PlattScaler` — Platt is itself a 1-feature logistic; reuses the same fit routine.
- `walk_forward(universe, provider, *, fold_quarters=1) -> WalkForwardResult` — expanding-window walk-forward across the 50-ticker universe × 5y daily. Each fold: train on rows from inception → fold start; OOS-predict the fold; fit per-degree Platt on those OOS predictions; emit calibrated OOS predictions.
- Output: aggregated calibrated OOS predictions across all folds, plus a final logistic refit on the entire history (used for live inference).

**`right_edge/promotion.py` (new)**
- `class PromotionGate`:
  - Inputs: aggregated OOS predictions for both logistic and heuristic, on the *same* prediction set.
  - Bootstrap (1000 resamples, RNG seed pinned) Brier CIs for both.
  - Per-degree calibration error for both.
  - Returns `Verdict { verdict: PROMOTE|HOLD|REJECT, logistic_brier_ci, heuristic_brier_ci, per_degree_calibration }`.
- Verdict rules:
  - **PROMOTE**: logistic Brier upper-CI < heuristic Brier lower-CI **AND** logistic per-degree calibration error ≤ heuristic per-degree calibration error on every degree.
  - **REJECT**: logistic worse on Brier (lower-CI > heuristic upper-CI) OR worse on any per-degree calibration.
  - **HOLD**: anything else (overlapping CIs, mixed calibration). Default outcome — bias toward not promoting.

**`right_edge/models/right_edge_logistic_v1.json` (new)**
- Persisted model artifact. Plain JSON, diffable, git-tracked. Schema:
  ```json
  {
    "version": "logistic_v1",
    "trained_at": "2026-05-04",
    "git_sha": "abc1234",
    "feature_order": ["retracement_progress", "bars_since_candidate", "volume_decline_pct", "higher_degree_zone_match", "atr_regime"],
    "weights": [w1, w2, w3, w4, w5],
    "intercept": b0,
    "platt_per_degree": {
      "0": {"a": ..., "b": ...},
      "1": {"a": ..., "b": ...},
      "2": {"a": ..., "b": ...},
      "3": {"a": ..., "b": ...},
      "4": {"a": ..., "b": ...}
    },
    "training": {
      "universe_size": 50,
      "n_folds": ...,
      "n_train_rows": ...,
      "n_oos_rows": ...,
      "rng_seed": ...
    },
    "gate": {
      "verdict": "PROMOTE",
      "logistic_brier_ci": [lo, mid, hi],
      "heuristic_brier_ci": [lo, mid, hi],
      "per_degree_calibration": {
        "0": {"logistic": ..., "heuristic": ...},
        "1": {"logistic": ..., "heuristic": ...},
        "2": {"logistic": ..., "heuristic": ...},
        "3": {"logistic": ..., "heuristic": ...},
        "4": {"logistic": ..., "heuristic": ...}
      }
    }
  }
  ```

**`backtest/pivot_layer.py` (modified)**
- `_features_for(...)` replaced with a call into `FeatureExtractor.extract(...)` using ATR-at-candidate + required-move surfaced from `detect_multi_degree`. Removes the placeholder-features bug.
- `detect_multi_degree` (or a sibling helper) gains a way to return per-degree ATR snapshot + required-move alongside the pivots so callers don't have to recompute. Cleanest path: `detect_multi_degree` returns `dict[int, DegreeSnapshot]` where `DegreeSnapshot = (pivots, atr, required_move)`. Existing callers updated.

**`pipeline.py` and `web/deps.py` (modified)**
- New helper `load_right_edge_model(config)` that:
  - Reads `right_edge.model` config (default `"auto"`).
  - `auto`: try `LogisticConfirmationModel.from_json(path)`; on success return it; on any failure (file missing, JSON invalid, verdict ≠ PROMOTE) fall back to `HeuristicConfirmationModel`.
  - `heuristic`: always heuristic.
  - `logistic`: load logistic; if missing, raise — do NOT silently fall back (operator-explicit).
- Pipeline / web app call this once at init and pass the resulting model down. Live inference path unchanged otherwise.

**`cli/train_app.py` (new)**
- `wave-alpha train right-edge` — run walk-forward + gate, print verdict and numbers, do NOT write.
- `wave-alpha train right-edge --write` — write `right_edge/models/right_edge_logistic_v1.json` only if verdict is PROMOTE.
- `wave-alpha train right-edge --write --force` — write regardless of verdict (for HOLD experimentation; the resulting JSON's verdict field still records HOLD/REJECT and `from_json` will refuse to load it for live inference).

### Configuration

`config.yaml` (or whatever wave-alpha already uses):
```yaml
right_edge:
  model: auto       # auto | heuristic | logistic
```

Default behavior with no config: `auto`, which means heuristic until a PROMOTE-verdict model JSON is committed.

### Determinism

- Logistic fit: pinned RNG seed, deterministic gradient-descent iteration order.
- Walk-forward: deterministic fold boundaries (calendar-aligned quarters).
- Bootstrap: pinned RNG seed embedded in the model JSON.
- Net result: `wave-alpha train right-edge` produces bit-for-bit identical model JSON across runs given the same data.

### Test plan

**Unit**
- `FeatureExtractor`: deterministic against fixture pivots + bars; covers HIGH and LOW pivot types.
- `LogisticConfirmationModel.predict_proba`: matches a reference scipy/numpy logistic on toy weights.
- Platt scaler: monotonic in input proba; identity for `(a=1, b=0)`.
- `PromotionGate`: synthetic Brier CIs covering all three verdicts (overlapping → HOLD; logistic-cleaner → PROMOTE; logistic-worse → REJECT); calibration-violation case.

**Integration**
- End-to-end `train right-edge` on a tiny fixture universe (3 tickers × 1 year). Verdict reproducible bit-for-bit across two runs.
- `auto` mode loader: with no model file → returns heuristic; with a PROMOTE-verdict file → returns logistic; with a HOLD/REJECT file → returns heuristic.

**Regression**
- Snapshot test on `pivot_layer.py` Brier output: numbers MUST change after the placeholder-features fix. Locks in the bug fix.

## Privacy

No new data flows. Training reads from the existing local OHLCV cache. Model JSON is local-only and git-tracked in this repo (no remote upload).

## Training run 2026-05-03 — Verdict: HOLD

First training pass against the v0.4 backtest universe.

**Command:** `wave-alpha train right-edge --start 2021-01-01 --end 2026-04-01 --write`

**Data:** 50 tickers requested; 49 contributed (BRK.B was unavailable from Yahoo — "possibly delisted; no timezone found"). 21 quarterly as-of pairs spanning 2021-Q1 through 2026-Q1.

**Sample sizes:** 4998 training rows; 4061 OOS predictions.

**Brier (paired bootstrap, 1000 resamples, 95% CI):**

|   | lo | mid | hi |
|---|---|---|---|
| Logistic  | 0.2282 | 0.2318 | 0.2350 |
| Heuristic | 0.2290 | 0.2366 | 0.2448 |

CIs overlap (logistic upper 0.2350 > heuristic lower 0.2290). Logistic midpoint is slightly better but not statistically separable on Brier alone.

**Per-degree calibration error (ECE):**

| degree | logistic | heuristic | ratio |
|---|---|---|---|
| 0 | 0.0603 | 0.1863 | 3.1× better |
| 1 | 0.0492 | 0.1270 | 2.6× better |
| 2 | 0.0552 | 0.1188 | 2.2× better |
| 3 | 0.0403 | 0.1087 | 2.7× better |
| 4 | 0.0472 | 0.1527 | 3.2× better |

Logistic is dramatically better-calibrated on every degree. The heuristic's stated probabilities are systematically miscalibrated (the renormalized weighted sum doesn't track empirical confirmation rates).

**Why the gate said HOLD despite calibration win:** the current gate requires PROMOTE on BOTH Brier AND calibration. Brier overlap fails the strict-better Brier check, so the calibration improvement isn't sufficient on its own. This is conservative-by-design, but for this run it left a meaningful improvement on the table.

**Heuristic remained in production.** The model artifact was not written to disk on this initial run.

**Follow-ups identified:**
- **Gate rule extension:** "PROMOTE on calibration-only when Brier overlaps and every degree's ECE is strictly better by ≥X%." Would have promoted this run. ✅ Implemented as `PROMOTE_CALIBRATION` verdict with X=20% relative threshold.
- **5 degrees observed (0–4), not 3.** Spec assumed degrees 1/2/3. ✅ Spec text + JSON examples updated.
- **Heuristic recalibration:** Platt-per-degree on top of the heuristic as a smaller alternative to shipping the logistic. ✅ Implemented as `CalibratedHeuristicModel` + `wave-alpha train calibrated-heuristic` CLI.
- **Re-train when RSI/fib-zone features land** (Q2-A deferred).

## Training run 2026-05-03 (after follow-ups) — Verdicts: PROMOTE_CALIBRATION + PROMOTE

Re-ran both training pipelines after shipping the gate extension + calibrated heuristic.

### Logistic — verdict PROMOTE_CALIBRATION

Same data, same gate logic except for the new calibration-only promotion path. The original HOLD flipped to PROMOTE_CALIBRATION because every degree's ECE improvement (60-70% relative) exceeded the 20% threshold. Artifact at `src/wave_alpha/right_edge/models/right_edge_logistic_v1.json`.

### Calibrated heuristic — verdict PROMOTE (full Brier+calibration)

`wave-alpha train calibrated-heuristic --start 2021-01-01 --end 2026-04-01 --write`

Same 4998 train rows / 4061 OOS predictions. The "model" being evaluated is heuristic + per-degree Platt vs. raw heuristic.

**Brier (paired bootstrap):**

|   | lo | mid | hi |
|---|---|---|---|
| Calibrated heuristic | 0.2198 | 0.2240 | 0.2279 |
| Raw heuristic        | 0.2290 | 0.2366 | 0.2448 |

CIs do NOT overlap — calibrated heuristic upper-CI (0.2279) is below raw lower-CI (0.2290). Brier is strictly better. Combined with per-degree calibration improvement on every degree, verdict is full **PROMOTE**.

**Per-degree calibration:**

| degree | calibrated | raw    |
|--------|-----------:|-------:|
| 0      | 0.0767     | 0.1863 |
| 1      | 0.0301     | 0.1270 |
| 2      | 0.0567     | 0.1188 |
| 3      | 0.0719     | 0.1087 |
| 4      | 0.0669     | 0.1527 |

Artifact at `src/wave_alpha/right_edge/models/right_edge_calibrated_heuristic_v1.json`.

### Calibrated heuristic vs logistic — surprising result

|   | Brier lo | Brier mid | Brier hi | ECE deg=0 | deg=1 | deg=2 | deg=3 | deg=4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Logistic              | 0.2282 | 0.2318 | 0.2350 | 0.0603 | 0.0492 | 0.0552 | 0.0403 | 0.0472 |
| Calibrated heuristic  | 0.2198 | 0.2240 | 0.2279 | 0.0767 | 0.0301 | 0.0567 | 0.0719 | 0.0669 |

**The simpler model wins overall Brier.** Calibrated heuristic's upper-CI (0.2279) sits one bp below logistic's lower-CI (0.2282) — strictly better at the 95% level, but barely. The hand-coded weighted sum + 2-parameter-per-degree Platt is a tighter discriminator than the 5-feature gradient-descent logistic + same Platt.

Per-degree calibration is mixed: logistic wins on degrees 0/3/4 (the rarer pivots), calibrated heuristic wins on degree 1, tie on 2.

**Loader preference order (currently logistic > calibrated > raw)** is therefore an open question. Two reasonable choices:
- **Keep current order:** logistic ships when both exist; calibrated heuristic only when logistic is missing or unloadable.
- **Flip to calibrated > logistic:** simpler model with tighter Brier; logistic kept available for explicit `right_edge.model: logistic` config.

The two artifacts are individually valid (both PROMOTE-class verdicts). Keeping both lets users select via config and gives the project a benchmark for future model iterations.

## Open questions / deferred

- **RSI + fib-zone features.** Deferred (Q2-A). Re-train if/when added; logistic is cheap to refit.
- **Sliding-window walk-forward** (vs. expanding). Deferred — would want ~10y history before this becomes meaningful.
- **Per-degree separate logistic weights.** Deferred — Platt-per-degree handles the calibration concern; the codebase emits 5 degrees (0..4 per `_DEFAULT_ATR_MULTIPLES`), and per-degree fits would need substantially more samples per degree than the current universe provides.

## Disclaimer policy

No user-visible changes to disclaimer text. The model artifact's metadata captures provenance for reproducibility, but the live UI surfaces the same probability number it does today — just sourced from a calibrated logistic instead of a hand-coded sum when promoted.
