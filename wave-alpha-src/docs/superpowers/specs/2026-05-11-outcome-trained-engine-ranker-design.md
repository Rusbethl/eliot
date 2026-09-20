---
related:
  - docs/superpowers/specs/2026-05-10-outcome-tracking-design.md  # source of labeled outcomes this spec consumes
  - docs/superpowers/specs/2026-05-10-outcome-driven-right-edge-training-design.md  # sibling outcome-trained model; this spec deliberately mirrors its patterns
  - docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md  # holdout-evaluation harness reused for promotion gate
  - docs/superpowers/plans/2026-05-11-outcome-data-bootstrap.md  # densifier producing the rows this spec trains on
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §6 ranking + blend formula being changed
  - src/wave_alpha/elliott/enumerator.py  # _score() hand-tuned weights this refit complements
  - src/wave_alpha/llm/ranking.py  # blend_scores formula being revised
  - src/wave_alpha/right_edge/  # mirrored module layout
---

# Outcome-trained engine ranker — design

**Status:** Draft
**Author:** alexchen
**Date:** 2026-05-11
**Related code:** `src/wave_alpha/elliott/enumerator.py`, `src/wave_alpha/llm/ranking.py`, `src/wave_alpha/pipeline.py`, `src/wave_alpha/right_edge/` (pattern reference), `src/wave_alpha/history/`

## 1. Motivation

`engine_score` is the product of a hand-tuned linear combination in `enumerator._score()`:

```python
raw = _WEIGHT_BASE * base + _WEIGHT_CONFLUENCE * confluence + _WEIGHT_RECENCY * recency
```

where `base = 1.0 - 0.10 * len(soft_violations)`, `confluence` comes from `fib_features.confluence_score`, and `recency` decays linearly with pivot-gap. Coherence is then layered on outside the count, as a multiplier in `blend_scores`:

```python
final = coh_multiplier * (alpha * engine_score + (1 - alpha) * llm_prob)
```

None of these weights — `_WEIGHT_*`, the `0.10`-per-soft-violation slope, the `0.05`-per-pivot recency decay, the coherence multiplier — were learned from outcomes. They are reasonable engineering defaults from the v0 design. With `history.sqlite` densification landing in parallel (`2026-05-11-outcome-data-bootstrap`), we will shortly have ~1000-1300 resolved `(snapshot, top-1 count, outcome)` rows across the curated 50-ticker universe at quarterly cadence. That is enough data to ask the smaller, well-bounded question this spec answers:

> *Given a top-K candidate's component scores plus its timeframe coherence, what is the calibrated probability that the resulting signal hits target before stop or time-stop?*

The right-edge model already answers a related question (with a different feature set, focused on right-edge confirmation). The two are intentionally **orthogonal predictors**: this ranker scores structural pattern quality and timeframe agreement; right-edge scores entry-timing confirmation. Keeping them separate preserves the role separation just shipped in the 2026-05-10 outcome-driven right-edge spec, while letting both feed the final blend.

A secondary motivation: folding coherence *into* the logistic instead of leaving it as an outside multiplier on the whole blend is more principled. Coherence is a structural property of the engine's candidates, not of the LLM's probability. The current `coh_multiplier * (alpha * engine + (1-alpha) * llm)` formula discounts `llm_prob` by coherence, which has no causal justification. This spec cleans that up.

## 2. Goals (v1)

- A new module `src/wave_alpha/engine_ranker/` that mirrors `right_edge/`'s layout: features, heuristic (current formula expressed as a fallback), logistic (artifact-backed), loader (`auto`-mode with silent fallback), training, promotion.
- The logistic predicts binary `P(outcome=target)` from four features: `template_fit`, `fib`, `recency`, `coherence`, with **shared coefficients and per-degree intercepts** (mirrors right-edge per-degree Platt pattern).
- Applied as a **pipeline-level re-ranker** on the top-K survivors. `enumerator._score()` is left untouched — it continues to perform in-template-window dedup and top-K cut with the existing hand-tuned weights.
- Three component sub-scores (`template_fit_component`, `fib_component`, `recency_component`) are added to `WaveCount` so the re-ranker reads them directly rather than recomputing.
- New CLI: `wave-alpha train engine-ranker [--use-outcomes] [--write] [--force]`.
- Promotion gate combining **paired-bootstrap on Brier vs heuristic baseline** AND **backtest expectancy on the holdout split** — both must show non-negative lift before a non-`--force` write.
- New final-score formula when artifact is present: `final = alpha * p_engine + (1 - alpha) * llm_prob`. The `coh_multiplier` term is dropped because coherence is now a logistic feature. Heuristic-fallback path retains the current `coh × engine` formula for behavior preservation.
- Reuses the outcome-training eval harness shipped in #5 for the holdout report.
- Artifact JSON `~/.wave_alpha/engine_ranker_logistic.json` plus a frozen baseline `models/v1.json` shipped in the wheel so `auto` mode finds a model from day one.

## 3. Non-goals (v1)

- **Refit of `enumerator._score()`** itself. Within-window dedup uses the current hand-tuned weights — that's a different job (relative ordering within one template family) and doesn't need outcome data.
- **Refit of `alpha` (LLM blend weight).** Different model, different data dependency, separate exercise.
- **Expanded feature set** beyond `template_fit / fib / recency / coherence`. The scoping turn explicitly rejected an expanded-feature variant for v1 to keep the model interpretable and well-fit on the ~1k-row dataset.
- **Replacing right-edge.** The two models are orthogonal predictors and both feed the final blend pipeline (right-edge's gating remains in place where it is today; this ranker re-ranks the candidates that survive).
- **Storing component scores in `history.sqlite`.** Mirrors the outcome-driven right-edge philosophy — features are recomputed at training time from cached bars. No schema bloat.
- **Fully per-degree separate models.** Per-degree *intercepts* only; weights are shared. ~250-400 rows per degree is too thin to support fully independent fits.
- **Three-class outcome head** (`target` / `time_stop` / `stop` as ordinal). Binary `target` vs `{stop, time_stop}` for v1; `time_stop` folded into the negative class by default, configurable.
- **Auto-retraining.** Manual CLI only.
- **Counterfactual labels for non-top-1 alternates.** Alternates were never traded; we do not synthesize labels for them. Training is `N_resolved_snapshots` rows.
- **A CLI flag to toggle coherence in/out of the logistic.** Coherence is always a feature when the logistic is active; if a user wants the old multiplier they set `engine_ranker.kind=heuristic` in config.

## 4. User-visible behavior

When the user runs `wave-alpha analyze AAPL` on stock config after the artifact ships:

- Candidates surface ranked by `final = alpha * p_engine + (1 - alpha) * llm_prob`. For most candidates the *ordering* is similar to today (the logistic is trained on the same components); the difference is calibrated absolute scores and the elimination of the coherence-on-LLM discount.
- The CLI table prints `engine_prob` (the calibrated probability) alongside `engine_score` (the legacy hand-tuned raw score) so the difference is visible. Both columns are stable across releases.

When the user runs `wave-alpha train engine-ranker --use-outcomes`:

1. The command reads resolved rows from `history.sqlite` (where `outcome.status ∈ {target, stop, time_stop}`).
2. For each row it replays the pipeline at `(ticker, as_of)` against cached bars (LLM disabled), pulls the top-1 candidate's `template_fit_component / fib_component / recency_component` and the snapshot's coherence, and labels `y = 1 if status == 'target' else 0` (or per `--drop-time-stops`).
3. Time-split walk-forward: oldest 70% train, newest 30% holdout. Cutoff date is recorded in the artifact.
4. Fits a logistic with shared coefficients + per-degree intercepts on the train split. Class weight defaults to `"balanced"` if the observed `target` rate is outside `[0.35, 0.65]`.
5. Evaluates promotion against the heuristic baseline on the holdout: paired-bootstrap on Brier (10k resamples, two-sided 95% CI) AND backtest expectancy in R using `pipeline.derive_signal` rules. Verdict prints as `PROMOTE` / `HOLD` / `INSUFFICIENT_DATA`.
6. On `PROMOTE` and `--write`: overwrites `~/.wave_alpha/engine_ranker_logistic.json` with feature_order, weights, per-degree intercepts, train/holdout row counts, cutoff date, observed prior, and the promotion CIs. `--force` writes regardless of verdict.

When the user runs `wave-alpha config set engine_ranker.kind {auto, logistic, heuristic}`:

- `auto` (default): prefer the local logistic artifact, fall back to packaged `models/v1.json`, fall back to heuristic if both missing/malformed.
- `logistic`: hard-require an artifact; raise `EngineRankerArtifactMissing` if absent.
- `heuristic`: use the current hand-tuned formula and *retain* the `coh_multiplier` outside-the-blend behavior — exact behavior parity with today's release.

## 5. Architecture

### 5.1 Module split

| Module | Change |
|---|---|
| `src/wave_alpha/engine_ranker/__init__.py` (new) | Re-exports the loader and the `EngineRankerModel` Protocol. |
| `src/wave_alpha/engine_ranker/features.py` (new) | `EngineRankerFeatures` frozen Pydantic model: `template_fit, fib, recency, coherence: float`, `degree: int`. Pure-data; extraction from `(WaveCount, CoherenceResult)` is a helper. |
| `src/wave_alpha/engine_ranker/heuristic.py` (new) | `HeuristicEngineRanker` — wraps the current `_WEIGHT_BASE / _WEIGHT_CONFLUENCE / _WEIGHT_RECENCY` formula and applies `coh_multiplier` outside, so `pipeline.run_analysis` calling `model.score(features)` against the heuristic produces identical numbers to today. |
| `src/wave_alpha/engine_ranker/logistic.py` (new) | `LogisticEngineRanker` with `predict_proba(features) -> float`; JSON-serializable artifact with `feature_order, weights, intercepts_by_degree, version, metadata`. |
| `src/wave_alpha/engine_ranker/loader.py` (new) | `load_engine_ranker(config)` honoring `EngineRankerConfig.kind ∈ {auto, logistic, heuristic}`. `auto` order: local → packaged → heuristic; silent fallback for missing or malformed artifact, log at INFO. |
| `src/wave_alpha/engine_ranker/training.py` (new) | `build_outcome_dataset(*, db_path, provider) -> OutcomeDataset`; `train_logistic(dataset, *, walk_forward_split=0.7) -> LogisticEngineRanker`; emits training metadata for the artifact. |
| `src/wave_alpha/engine_ranker/promotion.py` (new) | `evaluate_promotion(candidate, baseline, holdout, signal_rules) -> PromotionVerdict`. Verdict is `PROMOTE` only if Brier-lift CI lower bound ≥ 0 AND expectancy-lift CI lower bound ≥ 0. |
| `src/wave_alpha/engine_ranker/models/v1.json` (new) | Frozen baseline artifact shipped in the wheel via the existing `[tool.hatch.build.targets.wheel] packages` glob. Generated from the first successful training run; subsequent runs may bump to `v2.json` etc. with a versioning convention parallel to `right_edge/models/`. |
| `src/wave_alpha/elliott/enumerator.py` | `_score()` unchanged. New: returns `WaveCount` with `template_fit_component / fib_component / recency_component` populated (decomposed sub-scores). |
| `src/wave_alpha/elliott/models.py` | `WaveCount`: add three optional `float` fields, defaulting to `None` for backwards-compat in tests that build counts by hand. Frozen Pydantic — explicit `model_copy(update={...})` from the scorer. |
| `src/wave_alpha/pipeline.py` | New step between coherence computation and `_build_ranked_counts`: when the resolved `engine_ranker` is a `LogisticEngineRanker`, invoke `predict_proba` per candidate and stash the result on `RankedCount` as `engine_prob: float`. When the resolved model is the heuristic, `engine_prob` is left `None` (the un-cohered raw is not a calibrated probability and would mislead consumers). Pass `engine_prob` through to `blend_scores`. |
| `src/wave_alpha/llm/ranking.py` | `blend_scores` gains an optional `engine_prob: float | None` argument and a `use_logistic_path: bool` flag. When `use_logistic_path=True`: `final = alpha * engine_prob + (1-alpha) * llm_prob` (no `coh_multiplier`). When `False`: existing formula. Path selection driven by `engine_ranker.kind`. |
| `src/wave_alpha/prefs/` | New `EngineRankerConfig`: `kind: Literal["auto","logistic","heuristic"] = "auto"`, `outcome_training: EngineRankerOutcomeTrainingConfig` with `walk_forward_split: float = 0.7`, `class_balance_threshold: tuple[float, float] = (0.35, 0.65)`, `include_time_stops: bool = True`. Additive, no migration. |
| `src/wave_alpha/cli/app.py` | `train` sub-app gains `engine-ranker` command. Lazy imports preserved. |

### 5.2 Data flow

```
history.sqlite (resolved rows: status ∈ {target, stop, time_stop})
  ↓ build_outcome_dataset
  for each (ticker, as_of):
    bars  = cache.sqlite (lookahead-safe via PointInTimeView)
    result = pipeline.run_analysis(req, llm_mode=DISABLED)
    top1  = result.ranked_daily[0]
    feats = EngineRankerFeatures.from_count(top1.count, result.coherence)
    label = 1 if outcome.status == "target" else 0
  ↓ train_logistic
  walk-forward split → fit shared weights + per-degree intercepts
  ↓ evaluate_promotion
  paired-bootstrap(Brier) vs HeuristicEngineRanker AND backtest-expectancy(R)
  ↓ verdict ∈ {PROMOTE, HOLD, INSUFFICIENT_DATA}
  if PROMOTE & --write: overwrite ~/.wave_alpha/engine_ranker_logistic.json
```

At inference time:

```
pipeline.run_analysis(req)
  ↓ detect_multi_degree → enumerate_counts (per timeframe)
  ↓ coherence(W, D, 4h)
  ↓ for each candidate c in top_k:
      feats = EngineRankerFeatures.from_count(c, coherence)
      c.engine_prob = engine_ranker.predict_proba(feats)
  ↓ derive_signal uses final = α·engine_prob + (1-α)·llm_prob (logistic path)
                    or final = coh × (α·engine_score + (1-α)·llm_prob) (heuristic path)
```

### 5.3 Component decomposition on `WaveCount`

`enumerator._score()` currently returns a single `float`. The refit needs the three component values back. Two clean options were considered:

- **Decompose on `WaveCount` (chosen).** Add `template_fit_component`, `fib_component`, `recency_component` to the model; populate them in `_score()` via `count.model_copy(update={...})`. Pipeline re-ranker reads them directly. No recomputation, single source of truth, deterministic.
- Re-extract at pipeline level. Avoids touching the model, but recomputes `confluence_score(fib_features(count))` for every candidate — wasted work and an opportunity for the two computations to drift.

The decomposition is purely additive: the fields default to `None`, so existing tests that construct `WaveCount` instances by hand are unaffected. Production code paths always populate them via the scorer.

### 5.4 Heuristic baseline (`HeuristicEngineRanker`)

The fallback expresses the current behavior as the `EngineRankerModel` Protocol implementation:

```python
def predict_proba(self, features: EngineRankerFeatures) -> float:
    raw = (_WEIGHT_BASE * features.template_fit
           + _WEIGHT_CONFLUENCE * features.fib
           + _WEIGHT_RECENCY * features.recency)
    # The legacy path multiplies by coherence outside the candidate (in blend_scores),
    # so this function returns the un-cohered raw. Caller in `use_logistic_path=False`
    # mode applies coh_multiplier as today.
    return max(0.0, raw)
```

The promotion gate compares the *logistic candidate* against the *heuristic baseline* under the **same** `use_logistic_path` flag — that is, the baseline also includes coherence as a multiplier OUTSIDE in the live formula, so we are comparing the two end-to-end live pipelines, not a mismatched mix. The Brier and backtest metrics use the corresponding final scores.

### 5.5 Promotion gate

Two tests, both must pass for `PROMOTE`:

1. **Brier lift, paired bootstrap.** For each holdout row `i`, compute `brier_h_i = (p_h_i - y_i)^2` for heuristic and `brier_l_i = (p_l_i - y_i)^2` for logistic. Resample row indices with replacement 10,000 times, compute `mean(brier_h - brier_l)` per resample. Verdict pass: lower bound of 95% CI ≥ 0 (logistic non-inferior on Brier).
2. **Expectancy lift, paired bootstrap.** For each holdout row, compute the realized R the trade would have produced under each model's top-1 selection (`pipeline.derive_signal` rules); resample as above; verdict pass: lower bound of 95% CI ≥ 0 (logistic non-inferior on R-expectancy).

A row count gate (`min_resolved_per_degree = 30`) elevates verdict to `INSUFFICIENT_DATA` if any degree present in the dataset has fewer than 30 resolved rows; the user is told to densify further (or run with `--force`).

**v1 implementation note — R proxy.** The v1 promotion gate uses a coarse sign-of-prediction proxy for R rather than the `derive_signal`-based realized R described above: each holdout row contributes `+1 R` if `label == 1` and `prob ≥ 0.5`, `-1 R` if `label == 0` and `prob ≥ 0.5`, and `0 R` otherwise. This is a deliberate v1 simplification to ship the gate without coupling promotion to backtest internals (`derive_signal` requires trade-rule config and OHLCV bars not available during the promotion evaluation loop). The proxy is sufficient to reject obvious regressions. A follow-up PR should implement the full `derive_signal` path before any logistic artifact is shipped to users.

### 5.6 Backward compatibility

- **Existing `engine_score` field on `WaveCount` and `RankedCount`** is retained, populated identically. Downstream consumers (history snapshots, CLI tables, web UI tooltips) continue to read it.
- **New `engine_prob` field** is added alongside as `float | None`. When `engine_ranker.kind="heuristic"` it is `None`; consumers tolerate missing.
- **`blend_scores` signature** gains optional keyword args with defaults that reproduce today's formula. No existing caller breaks.
- **History snapshot schema**: `SnapshotCount` gains an optional `engine_prob: float | None = None`. Rows written before this PR (including those produced by the outcome-data bootstrap, which lands first) will have `None`; rows written after this PR with `engine_ranker.kind` resolving to logistic will have a populated value. The history resolver, outcome dataset, and training loop all tolerate `None` — they recompute features from cached bars at training time and do not depend on stored `engine_prob`.
- **Heuristic-fallback users** (no artifact, `kind=heuristic`, or `kind=auto` on a system without `models/v1.json` available) see no behavior change at all — the existing formula runs end-to-end.

## 6. Training & evaluation details

### 6.1 Walk-forward split

- Sort rows by `as_of`. Cutoff index at the 70th percentile of distinct `as_of` values (cutoff is itself an `as_of` date, not a row index — so a single date with many tickers stays in one split).
- Cutoff date is stored in the artifact `metadata.walk_forward_cutoff`.
- A new unit test analogous to `tests/backtest/test_lookahead_guard.py` plants a leakage scenario (an outcome row whose `as_of > cutoff` accidentally appears in train) and asserts the trainer rejects it.

### 6.2 Per-degree intercepts

- One shared coefficient vector `w ∈ R^4` for `[template_fit, fib, recency, coherence]`.
- One intercept `b_d` per observed degree `d`. Degree is taken from `WaveCount.degree`.
- Logit: `z = w · features + b_{degree}`; `p = sigmoid(z)`.
- At inference, an unseen degree falls back to the *median* intercept of the trained degrees with a WARNING log; this is a graceful degradation case (e.g. multi-degree enumeration adds a new degree post-training).

### 6.3 Reuse of outcome-training eval harness

Deferred to a follow-up PR. The `evaluate_promotion` function in this branch is self-contained and does not reuse the `right_edge.outcome_eval` harness shipped in #5. The integration (a `--model engine-ranker` option so one report covers both models) is left for a subsequent PR once both harnesses have stabilized.

### 6.4 Recompute determinism

The training pipeline must produce **bit-identical** features for the same `(ticker, as_of)` across runs. Determinism inputs:
- `cache.sqlite` bars are immutable for past dates.
- `PointInTimeView` is deterministic.
- `enumerate_counts` with the eval-cache landed in #4 is deterministic.
- `coherence` is deterministic.

A dataset-determinism unit test computes the feature matrix twice and asserts byte-equal Pickle. This guards against accidental nondeterminism in any of the upstream paths.

## 7. CLI surface

```
wave-alpha train engine-ranker \
    [--use-outcomes]              # default true once artifact path exists; flag exists for parity with right-edge
    [--limit N]                   # cap rows for fast iteration
    [--include-time-stops/--drop-time-stops]
    [--write]                     # write artifact only if verdict=PROMOTE (or --force)
    [--force]                     # write regardless of verdict
    [--report path.md]            # markdown report companion (reuses harness)

wave-alpha config set engine_ranker.kind {auto, logistic, heuristic}
wave-alpha config set engine_ranker.outcome_training.walk_forward_split 0.7
```

Existing `wave-alpha analyze`, `wave-alpha scan`, `wave-alpha backtest count` automatically pick up the new ranker via `engine_ranker.kind` in config — no flags needed.

## 8. Testing

- **Unit:** `EngineRankerFeatures.from_count` extraction against synthetic `WaveCount` + `CoherenceResult` fixtures.
- **Unit:** `LogisticEngineRanker.predict_proba` against pinned weights produces known outputs (regression-lock against the v1 artifact).
- **Unit:** `HeuristicEngineRanker.predict_proba` reproduces today's `_score()` to within `1e-12` for a fixture of 50 synthetic candidates.
- **Unit:** `loader` returns logistic when artifact present, heuristic when absent, heuristic when malformed (with INFO log).
- **Unit:** `_score()` populates the three component fields on the returned `WaveCount`.
- **Integration:** `pipeline.run_analysis` end-to-end with `engine_ranker.kind="logistic"` returns ranked counts with populated `engine_prob`, in an order consistent with the artifact's prediction.
- **Integration:** `pipeline.run_analysis` with `engine_ranker.kind="heuristic"` produces bit-identical `final_score` values to a golden snapshot captured at HEAD (regression lock for the heuristic-fallback path — no user-visible drift).
- **Promotion (synthetic):** dataset where logistic strictly dominates heuristic → `PROMOTE`; dataset where it strictly loses → `HOLD`; dataset with `< 30` rows in one degree → `INSUFFICIENT_DATA`.
- **Walk-forward lookahead guard:** planted-leak test (analogous to `test_lookahead_guard.py`).
- **Determinism:** byte-identical feature matrix across two builder invocations.
- **CLI:** `train engine-ranker` dry-run prints planned row counts; `--write` without `PROMOTE` and without `--force` exits non-zero and writes nothing.

## 9. Risks & open questions

- **Risk: data sparsity.** ~1000 rows distributed across degrees may yield ~250-400 per degree. Per-degree intercepts are cheap parameters but if one degree has <30 rows, the intercept is essentially the prior — handled by the `INSUFFICIENT_DATA` gate. Mitigation: re-run densifier at finer cadence (monthly vs quarterly) if needed.
- **Risk: feature leakage via PointInTimeView misuse in `build_outcome_dataset`.** Mitigated by routing all bar access through `PointInTimeView(bars, as_of)` (already the project's single source of truth) and the lookahead-guard test.
- **Risk: drop of `coh_multiplier` on the LLM term silently regresses LLM-enabled live runs** (those scores will be uniformly higher). Mitigated by the heuristic-path regression test and by the side-by-side `engine_score` / `engine_prob` columns in the CLI table making the change visible.
- **Risk: `HeuristicEngineRanker` reproduces today's behavior in test, but the live integration via `blend_scores(use_logistic_path=False)` could diverge if the `coh_multiplier` plumbing is mis-wired.** Mitigated by the end-to-end heuristic-path golden test.
- **Open question: should `engine_ranker.kind="auto"` be the default in `config.yaml`?** Yes — the packaged `models/v1.json` ships with the release; users get the trained ranker out of the box. Users who want strict legacy can opt-in to `kind=heuristic`. Captured here so it doesn't surface as a surprise in the plan.
- **Open question: should the snapshot `top_count_hash` include `engine_prob`?** No — the hash should remain a function of the *structural* count only (pattern, degree, pivots, labels). The probability is computed *from* the count by a separately-versioned model and shouldn't break snapshot dedup when the model is retrained. Recorded explicitly to avoid the wrong call at implementation time.

## 10. Rollout

1. Ship the module + CLI + heuristic-path regression test in one PR. `engine_ranker.kind` defaults to `auto`; without a `models/v1.json` it transparently falls back to heuristic. **Zero user-visible behavior change at this commit.**
2. Run the densifier (out-of-band, per the bootstrap plan).
3. Run `wave-alpha train engine-ranker --use-outcomes --write` against the densified DB. If verdict is `PROMOTE`, the local artifact is written.
4. Capture the artifact as `src/wave_alpha/engine_ranker/models/v1.json`, commit, and release. Users on `kind=auto` (default) now get the trained ranker.
5. Document in `CHANGELOG.md` that the `final_score` formula changed for users on `kind=auto` (coherence now a feature, no longer an outside multiplier on the LLM term).
6. If verdict is `HOLD` or `INSUFFICIENT_DATA`, do not ship a `v1.json`. The auto path stays on heuristic; we densify further and try again. This is acceptable — the infrastructure ships either way, the artifact gates on real lift.
