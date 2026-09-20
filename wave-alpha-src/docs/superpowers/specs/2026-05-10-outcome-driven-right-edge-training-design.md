---
related:
  - docs/superpowers/specs/2026-05-10-outcome-tracking-design.md  # source of labeled outcomes this spec consumes
  - docs/superpowers/specs/2026-05-09-wave-count-history-design.md  # history.sqlite — the storage being trained against and auto-pruned
  - docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md  # existing training pipeline + JSON artifact format
  - src/wave_alpha/right_edge/  # training.py, extractor.py, loader.py, promotion.py
  - src/wave_alpha/history/  # store.py, service.py, resolver.py
---

# Outcome-driven right-edge training (with bounded history) — design

**Status:** Draft
**Author:** alexchen
**Date:** 2026-05-10
**Related code:** `src/wave_alpha/right_edge/`, `src/wave_alpha/history/`, `src/wave_alpha/prefs/`, `src/wave_alpha/cli/app.py`

## 1. Motivation

The 2026-05-10 outcome-tracking spec shipped a queryable ground-truth dataset: every snapshot with a signal now resolves to `target` / `stop` / `time_stop` / `pending` as bars elapse, with realized R, MFE, MAE, and bars-held. Its §3 non-goals explicitly defer the natural follow-up — *retraining the right-edge model on these realized outcomes* — to a separate spec. This is that spec.

Today, `right_edge/training.py` learns from a synthetic dataset built from pivot-confirmation labels (whether a candidate's terminal pivot was the *actual* terminal pivot, as measured by the multi-degree detector run on bars after the candidate's `as_of`). This labeling is engineer-defined and proxies the question the user actually cares about: *did the trade work?* The proxy is reasonable enough to ship, but it has known failure modes — a candidate whose terminal pivot was "correct" by the detector can still produce a losing trade if the stop is hit before the target, and vice versa.

Realized-outcome labels close the gap between what the model learns and what the user cares about, **once enough outcomes have accumulated.** The catch: at v1 (this spec landing), the user will have weeks-to-months of cron-collected outcomes — tens, not hundreds. Outcome-only training over a tiny dataset is unstable and can degrade the live model. So this spec ships a *blended* training run that fades from synthetic-dominated to outcome-dominated as the dataset grows, with the existing promotion gate (`right_edge/promotion.py`) as the safety net.

A secondary motivation: `history.sqlite` currently grows unbounded. A 50-ticker daily cron is projected at ~30 MB/year. That's fine for a year, awkward at five. This spec adds bounded auto-pruning so the user does not need to think about disk growth.

## 2. Goals (v1)

- A new training mode that blends the existing synthetic dataset with outcome-labeled examples drawn from `history.sqlite`.
- Outcome examples are constructed by **recomputing features at training time** from `cache.sqlite` bars at the snapshot's `as_of` — no additive storage in `history.sqlite`.
- Per-degree sample-weight schedule that ramps outcome influence smoothly from ~0 (when N_outcomes ≪ N_target) to equal-with-synthetic at N_outcomes ≥ N_target (= 100 per degree), capped thereafter.
- The existing `right_edge_logistic.json` artifact format is reused. Promotion gate (paired-bootstrap Brier + per-degree ECE in `right_edge/promotion.py`) is reused as-is.
- Auto-prune of `history.sqlite` rows older than `history.retention_days` (default 730, configurable; `0` disables). Pruning is per-ticker, runs inside `record_snapshot` after the resolver pass, best-effort with errors swallowed.
- Manual training trigger: `wave-alpha train right-edge --use-outcomes`. Matches existing `train` ergonomics.
- A new `wave-alpha history` sub-app for manual inspection (`stats`) and pruning (`prune`).
- Training-run metadata (row counts, skip counters, schedule constants) is recorded in the artifact JSON for reproducibility.

## 3. Non-goals (v1)

- **Three-class outcome heads.** The logistic model is binary today; `time_stop` is folded into the negative class by default (configurable to drop instead).
- **Per-degree separate artifacts.** The existing per-degree Platt scalers are the per-degree mechanism. One JSON, one schedule.
- **Auto-retraining triggers** (post-resolution background runs, scheduled cron). Manual CLI only.
- **Pruning `cache.sqlite`.** It is much larger and shared across analyze/scan/backtest — a separate concern.
- **LLM blend (α) evaluation against outcomes.** Different model, different spec.
- **Cross-version engine outcome comparison.** Depends on `engine_version` snapshots accumulating; a separate later spec.
- **Right-edge feature-schema evolution.** Recompute uses the *current* `RightEdgeFeatures` definition. If features change later, retraining is required — that constraint already exists for the synthetic path.
- **Snapshot payload migration to store features.** Explicit decision: features are recomputed, never stored, so retention pruning is the only DB-size lever.
- **Outcome-driven sample weighting at the count-enumeration layer** (e.g. up-weighting templates that produce more targets). Out of scope; this spec touches only the right-edge confirmation head.

## 4. User-visible behavior

When the user runs `wave-alpha train right-edge --use-outcomes`:

1. The command loads the existing synthetic dataset (today's behavior).
2. It reads resolved rows from `history.sqlite` (where `outcome_status IN ('target', 'stop', 'time_stop')`).
3. For each resolved row it replays the pipeline at the snapshot's `as_of` to recompute features.
4. It stacks the two datasets with sample weights per the per-degree ramp schedule, trains the logistic model, and evaluates promotion exactly as today.
5. It prints a report: `synthetic_rows`, `outcome_rows`, `skipped_drift`, `skipped_no_bars`, `schedule`, and the promotion verdict.
6. If promoted, `~/.wave_alpha/right_edge_logistic.json` is overwritten with new metadata fields recording the outcome dataset summary.

When the user runs `wave-alpha analyze AAPL` after any cron-driven `record_snapshot`: snapshots for AAPL older than `history.retention_days` are silently deleted from `history.sqlite`. No user-visible change beyond bounded DB growth.

When the user runs `wave-alpha history stats`: prints rows per ticker, rows per `outcome_status`, oldest/newest `as_of`, DB file size on disk.

When the user runs `wave-alpha history prune --older-than 365d [--ticker T] [--dry-run]`: manual pruning, optionally scoped to one ticker, with dry-run preview.

## 5. Architecture

### 5.1 Module split

| Module | Change |
|---|---|
| `src/wave_alpha/right_edge/outcome_dataset.py` (new) | Pure builder: `build_outcome_dataset(*, db_path, bar_provider, degree, schedule, n_synth_per_degree) -> OutcomeDataset`. Reads resolved snapshots, replays pipeline, recomputes features, applies labels and weights, returns a frozen result with skip counters. |
| `src/wave_alpha/right_edge/training.py` | `train_logistic(synthetic, outcome=None, *, schedule=None)` gains optional outcome dataset; internally stacks rows with sample weights. Backward-compatible: callers passing only `synthetic` behave exactly as today. |
| `src/wave_alpha/right_edge/loader.py`, `right_edge/promotion.py` | **Unchanged.** Existing JSON artifact format and promotion gate apply unchanged. |
| `src/wave_alpha/right_edge/extractor.py` | **Unchanged.** Already used by training pipeline; outcome dataset calls the same entry point. |
| `src/wave_alpha/history/store.py` | New `prune_older_than(*, db_path, ticker, cutoff) -> int` returning the deleted-row count. |
| `src/wave_alpha/history/service.py` | `record_snapshot` calls `prune_older_than` after the resolver pass. Failures swallowed at WARNING; same best-effort pattern as resolver-on-write. |
| `src/wave_alpha/prefs/` | New `HistoryConfig` block: `retention_days: int = 730`. New `RightEdgeOutcomeTrainingConfig`: `n_target_per_degree: int = 100`, `include_time_stops: bool = True`, `time_stop_label: float = 0.0`. Defaults take effect on first read of an older `config.yaml` without these keys (additive — no migration). |
| `src/wave_alpha/cli/app.py` | `train right-edge` gains `--use-outcomes`, `--outcome-weight-target`, `--limit`, `--include-time-stops/--drop-time-stops`. New `history` sub-app with `stats` and `prune` commands. Lazy-imports preserved. |

### 5.2 Retraining data flow

```
wave-alpha train right-edge --use-outcomes
  ↓
load_synthetic_dataset()                            (existing)
  ↓
build_outcome_dataset(db_path, provider, degree)
  ├── SELECT ticker, as_of, payload_json, outcome_status FROM snapshots
  │     WHERE outcome_status IN ('target', 'stop', 'time_stop')
  │
  ├── For each row:
  │     ├── Deserialize payload_json → Snapshot
  │     ├── provider.get_bars(ticker, end=as_of, lookback=...)
  │     │     (cache.sqlite hit; no network unless cold; lookahead-guarded
  │     │      by PointInTimeView)
  │     ├── PointInTimeView(bars, as_of)            ← lookahead boundary
  │     ├── detect_multi_degree → pivots at degree d (= signal.degree)
  │     ├── enumerate_counts(pivots, degree=d)
  │     ├── Locate matching count by count_id
  │     │     (drift fallback: top-1 if pattern + direction + degree match)
  │     ├── Extract RightEdgeFeatures via right_edge/extractor.py
  │     └── Label per §6, weight deferred to schedule pass
  │
  ├── Per-degree pass: compute weights per §6 (needs full row counts)
  └── Return OutcomeDataset(rows, skipped_drift, skipped_no_bars, schedule)
  ↓
train_logistic(synthetic, outcome, schedule=schedule)
  ├── Stack synthetic rows (weight 1.0) + outcome rows (computed weights)
  ├── Fit per-degree logistic + Platt scalers
  └── Return TrainedArtifact (metadata includes schedule + dataset stats)
  ↓
evaluate_promotion(old, new) → PROMOTE | PROMOTE_CALIBRATION | NO_PROMOTE
  ↓
[PROMOTE] Write ~/.wave_alpha/right_edge_logistic.json
[NO_PROMOTE] Artifact untouched; report still emitted
```

### 5.3 Auto-prune flow

`record_snapshot` already calls the outcome resolver after upserting today's row. The prune step follows:

```python
# In history/service.py:record_snapshot, after resolver pass:
try:
    if config.history.retention_days > 0:
        cutoff = today - timedelta(days=config.history.retention_days)
        prune_older_than(db_path=db_path, ticker=ticker, cutoff=cutoff)
except Exception:
    logger.warning("auto-prune failed for %s; continuing", ticker, exc_info=True)
```

Per-ticker scope is deliberate: a single `analyze` request cannot stall on a global table sweep, and one ticker's malformed row cannot drag deletion across other tickers.

### 5.4 Storage path

Unchanged: `~/.wave_alpha/history.sqlite`, `~/.wave_alpha/cache.sqlite`, `~/.wave_alpha/right_edge_logistic.json`. No new files, no migrations. The new `right_edge` artifact metadata fields are additive JSON keys.

## 6. Data model

### 6.1 `WeightSchedule` (frozen dataclass, `right_edge/outcome_dataset.py`)

```python
@dataclass(frozen=True)
class WeightSchedule:
    n_target_per_degree: int = 100      # full-ramp threshold
    include_time_stops: bool = True     # if False, time_stop rows skipped entirely
    time_stop_label: float = 0.0        # used only when include_time_stops=True
```

Recorded verbatim in the trained artifact's `metadata.outcome_schedule` so any future evaluator can reproduce the weighting.

### 6.2 `OutcomeRow`, `OutcomeDataset`

```python
@dataclass(frozen=True)
class OutcomeRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    label: float                        # 0.0 or 1.0
    weight: float                       # computed by schedule pass

@dataclass(frozen=True)
class OutcomeDataset:
    rows: list[OutcomeRow]
    skipped_drift: int                  # count_id not present in recomputed enumeration
    skipped_no_bars: int                # provider could not return bars at as_of
    schedule: WeightSchedule            # echoed for artifact metadata
```

### 6.3 Label encoding

| `outcome_status` | Label | Notes |
|---|---|---|
| `target` | `1.0` | Trade hit the target before stop or time cap. |
| `stop` | `0.0` | Stop was hit. |
| `time_stop` | `schedule.time_stop_label` (default `0.0`) | When `include_time_stops=False`, row is skipped entirely instead of labeled. |
| `pending` | *skipped* | Not yet resolved; never included in training. |

### 6.4 Sample-weight computation

`n_synth_per_degree` is derived from the synthetic dataset (already in hand at training entry) and passed into the weight-assignment pass. The weight assignment itself happens inside `build_outcome_dataset` so the returned `OutcomeDataset` is fully formed — `training.py` does not re-touch weights.

Per-degree, once after building the row list:

```python
def assign_weights(rows: list[OutcomeRow], n_synth_per_degree: dict[int, int], schedule: WeightSchedule) -> list[OutcomeRow]:
    n_outc_per_degree = Counter(r.degree for r in rows)
    weights_by_degree: dict[int, float] = {}
    for degree, n_outc in n_outc_per_degree.items():
        n_synth = n_synth_per_degree.get(degree, 0)
        ramp = min(1.0, n_outc / schedule.n_target_per_degree)
        if n_outc == 0 or n_synth == 0:
            weights_by_degree[degree] = 0.0  # defensive — no rows means no contribution
        else:
            weights_by_degree[degree] = (n_synth / n_outc) * ramp
    return [replace(r, weight=weights_by_degree[r.degree]) for r in rows]
```

Properties (traced in §7 of the brainstorm):

- **n_outc = 0** → no outcome rows, training is identical to today.
- **n_outc = 50, n_target = 100, n_synth = 500**: `ramp = 0.5`, `weight = (500/50) × 0.5 = 5.0`. Total outcome influence = 50 × 5.0 = 250 = half the synthetic mass.
- **n_outc = 100** (target reached): `ramp = 1.0`, `weight = n_synth / n_outc`. Outcomes balance synthetic exactly.
- **n_outc ≫ 100**: ramp caps at 1.0, balance remains 1:1.

### 6.5 Count-drift handling

Recomputing pivots at the same `as_of` can produce a slightly different top count (e.g. after an ATR-multiple re-tune). Policy:

1. Find the count in the recomputed enumeration with the same `count_id` as `snapshot.signal.count_id`. If present, use its features.
2. If absent, accept the top-1 of the recomputed enumeration **only if its `pattern`, direction (via signal direction), and degree all match** the snapshot's signal.
3. Otherwise, skip the row (`skipped_drift++`). Log at INFO with ticker / as_of / cause.

Drift is logged but not promoted to error: the dataset is "best-effort honest" rather than "100% complete."

### 6.6 SQLite changes

`history/store.py` gains one function — no schema change:

```python
def prune_older_than(*, db_path: Path, ticker: str, cutoff: date) -> int:
    """Delete snapshots for ticker with as_of < cutoff. Returns row count."""
    with sqlite3.connect(db_path) as conn:
        with conn:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE ticker = ? AND as_of < ?",
                (ticker, cutoff.isoformat()),
            )
            return cur.rowcount
```

The existing `idx_snapshots_ticker_as_of` index makes the deletion O(log n + k).

## 7. CLI surface

### 7.1 `train right-edge` (extended)

```
wave-alpha train right-edge [--use-outcomes]
                            [--outcome-weight-target N=100]
                            [--limit N]
                            [--include-time-stops | --drop-time-stops]
                            [--write]
```

`--use-outcomes` is opt-in. Without it, behavior is byte-identical to today. `--limit N` caps how many outcome rows are processed (recompute is slow on cold caches — useful for fast iteration). `--write` retains its current promotion-gated semantics.

### 7.2 `history` (new sub-app)

```
wave-alpha history stats
wave-alpha history prune --older-than 365d [--ticker T] [--dry-run] [--json]
```

`stats` prints: row counts by ticker, by `outcome_status`, oldest/newest `as_of`, DB file size. `prune` is an explicit manual companion to auto-prune; it does not require `retention_days` to be set, and supports `--dry-run` to preview deletions before committing.

## 8. Configuration

Additive keys in `~/.wave_alpha/config.yaml`. All have defaults — older configs continue to work without edits.

```yaml
history:
  retention_days: 730              # 0 disables auto-prune; default ~2 years
right_edge:
  outcome_training:
    n_target_per_degree: 100
    include_time_stops: true
    time_stop_label: 0.0           # ignored when include_time_stops is false
```

`config.yaml` schema lives in `prefs/`. New blocks register defaults via Pydantic, so `config show` displays them after first read.

## 9. Error handling

| Failure | Behavior |
|---|---|
| Provider cannot return bars for a `(ticker, as_of)` | Skip row, `skipped_no_bars++`. Continue. |
| Recomputed enumeration produces no matching count and no acceptable fallback | Skip row, `skipped_drift++`. Continue. |
| Feature extraction raises | Skip row, `skipped_drift++` (same bucket — both are candidate-quality skips), log at WARNING with ticker/as_of, continue. Should be rare. |
| Auto-prune raises inside `record_snapshot` | Caught at WARNING, never propagates. Matches resolver-on-write best-effort pattern. |
| `--use-outcomes` with empty history.sqlite | Build returns empty `OutcomeDataset`; training proceeds with synthetic-only behavior (degenerate-but-valid). Report shows `outcome_rows: 0` and a clear info line. |
| Promotion verdict `NO_PROMOTE` | Existing artifact untouched. Training stats still emitted. Non-zero exit code preserved (matches today's behavior). |

## 10. Testing

### 10.1 Unit tests (new)

1. **`tests/right_edge/test_outcome_dataset.py`** — fixture history.sqlite with 6 resolved rows across 2 tickers and 2 degrees (mix of target / stop / time_stop) plus a fake `OHLCVProvider` returning canned bars. Verify built dataset has correct labels, counts, skip counters, and lookahead boundary respected.
2. **`tests/right_edge/test_weight_schedule.py`** — table-driven, traces the four ramp regimes from §6.4: n_outc = 0, < target, = target, ≫ target.
3. **`tests/right_edge/test_training_blended.py`** — end-to-end `train_logistic(synthetic, outcome, schedule=...)` produces an artifact whose metadata records the schedule. `--drop-time-stops` path covered. Drift-fallback path covered.
4. **`tests/history/test_auto_prune.py`** — insert 10 rows spanning 800 days for ticker A and 5 rows for ticker B; call `record_snapshot` with `retention_days=365`; assert A's old rows deleted, A's recent rows kept, **B untouched.** Also covers `retention_days=0` (disable) and `prune_older_than` SQL semantics directly.
5. **`tests/cli/test_history_stats.py`**, **`tests/cli/test_history_prune.py`** — CLI smoke tests, `--dry-run` and `--json` paths.

### 10.2 Existing tests that must remain green

- `tests/backtest/test_lookahead_guard.py` — synthetic-leak guard. Outcome dataset construction routes bars through `PointInTimeView`; the guard's plant must continue to fire if any new fetch site bypasses the view.
- `tests/right_edge/test_promotion.py` — promotion gate unchanged; this spec adds inputs, not gate logic.
- All existing `tests/cli/test_train_*.py` — `--use-outcomes` is additive; default-path training byte-identical.

## 11. Rollout

Single PR; no migration. Order of operations inside the PR:

1. Add `HistoryConfig.retention_days` and `prune_older_than` to `history/store.py` + `history/service.py`. Ships auto-prune behind the config default.
2. Add `outcome_dataset.py`, extend `training.py` with optional outcome dataset.
3. Add CLI flags and new `history` sub-app.
4. Tests across all layers.
5. Update CLAUDE.md "Right-edge confirmation models" section to mention `--use-outcomes` and the recompute-at-train-time policy.

The user's first action on update: run `wave-alpha history stats` to see current shape, then `wave-alpha train right-edge --use-outcomes --no-write` to dry-run a blended training pass and inspect the report before promoting.

## 12. Future work

- **Auto-retrain trigger.** Background or scheduled retraining once N_outcomes crosses N_target. Not in scope; manual is sufficient at the current data rate.
- **Cross-version outcome comparison.** `engine_version` is already snapshotted; aggregating per-version expectancy answers "did the engine bump help?" Separate spec.
- **LLM α evaluation against outcomes.** Same data source, different model. Separate spec.
- **Three-class head / fractional R-based labels.** Possible if binary-only proves limiting; current logistic head is binary so this is a larger lift.
- **Outcome-weighted enumeration scoring.** Up-weight templates whose historical outcomes are stronger. Touches the count layer, not the confirmation head; out of scope.
- **`cache.sqlite` retention policy.** Independent concern, much larger DB, separate spec if it becomes a problem.
