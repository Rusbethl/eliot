# Fib Structural Features Design

**Status:** Approved (brainstorm 2026-05-03), pending plan + implementation.

**Goal:** Replace the single-ratio `_confluence_bonus` in `engine_score` with a pattern-aware multi-ratio confluence, and surface the same ratios as ML features for the right-edge confirmation model. One computation pass, two consumers (engine ranking + ML training).

**Non-goal:** Time-fib (bar-count ratios), channel-line validity, weight re-tuning from training data, visual fib bands on the deepdive chart. Each is a follow-up.

---

## Context

`elliott/enumerator.py:_score()` blends three components per wave count:

- 0.50 — template fit (`1 - 0.10 × |soft_violations|`)
- 0.20 — fib confluence (currently: W2 retracement of W1, generic distance-to-nearest-fib-level)
- 0.30 — recency (decays linearly to 0.2 over a 16-pivot gap)

The 0.20 confluence slot uses `fib.fib_confluence_score(target_price=W2.end, reference=W1)`. It rewards W2 sitting near *any* of `(0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.0, 2.618)`, treating all levels equally and ignoring W3, W4, W5 entirely.

The right-edge confirmation model (`right_edge/extractor.py FeatureExtractor`) currently extracts features from a tentative pivot's snapshot — ATR, required move, recent swing magnitudes — without any wave-count context. Pattern structure is invisible to the model.

---

## Architecture

Two new pure modules under `src/wave_alpha/elliott/`:

### `fib_bands.py`

Hardcoded canonical-band constants per `(pattern, ratio_name)`. Plain Python — these are expert-curated EW values, not user config; YAML loader would be premature.

```python
@dataclass(frozen=True)
class Band:
    ideal_lo: float
    ideal_hi: float
    acceptable_lo: float
    acceptable_hi: float
    weight: float  # share of the 0.20 confluence budget for this pattern

IMPULSE_BANDS: dict[str, Band] = {
    "w2_w1_retrace":       Band(0.382, 0.618, 0.236, 0.786, 0.06),
    "w3_w1_extension":     Band(1.382, 2.000, 1.000, 2.618, 0.06),
    "w4_w3_retrace":       Band(0.236, 0.382, 0.146, 0.500, 0.04),
    "w5_w1_ratio":         Band(0.618, 1.382, 0.500, 1.618, 0.02),
    "w5_thrust_extension": Band(0.382, 0.618, 0.236, 1.000, 0.02),
}

ZIGZAG_BANDS: dict[str, Band] = {
    "b_a_retrace": Band(0.500, 0.618, 0.382, 0.786, 0.12),
    "c_a_ratio":   Band(1.000, 1.272, 0.618, 1.618, 0.08),
}

CURATED_PATTERNS: frozenset[str] = frozenset({"impulse", "zigzag"})
```

Per-pattern weights sum to 0.20 — same overall confluence budget as before. Per-ratio weights inside that budget reflect EW conviction (W2 retrace and W3 extension are highest, W5 ratios lowest, B:A retrace dominates zigzag).

### `fib_features.py`

Pure functions, no I/O.

```python
@dataclass(frozen=True)
class FibFeatures:
    pattern: str
    is_curated_pattern: bool

    # Impulse (None for non-impulse counts)
    w2_w1_retrace: float | None = None
    w3_w1_extension: float | None = None
    w4_w3_retrace: float | None = None
    w5_w1_ratio: float | None = None
    w5_thrust_extension: float | None = None

    # Zigzag (None for non-zigzag counts)
    b_a_retrace: float | None = None
    c_a_ratio: float | None = None


def fib_features(count: WaveCount) -> FibFeatures: ...
def confluence_score(features: FibFeatures) -> float: ...
def trapezoid(value: float, band: Band) -> float: ...
```

`fib_features(count)` dispatches on `count.pattern`:
- `"impulse"` → emits all 5 impulse ratios from `count.segments()`
- `"zigzag"` → emits both zigzag ratios
- otherwise → returns `FibFeatures(pattern=count.pattern, is_curated_pattern=False)` with all ratio fields `None`

`confluence_score(features)` returns the weighted sum of `trapezoid(ratio, band)` across the pattern's bands. Returns `0.0` for non-curated patterns (caller falls back to legacy generic scorer).

`trapezoid(value, band)`:
- Outside `[acceptable_lo, acceptable_hi]` → `0.0`
- Inside `[ideal_lo, ideal_hi]` → `1.0`
- In the lower ramp `[acceptable_lo, ideal_lo)` → linear from 0 to 1
- In the upper decay `(ideal_hi, acceptable_hi]` → linear from 1 to 0

```
            1.0 ┤        ┌───────────┐
                │       /             \
                │      /               \
            0.0 ┤─────/                 \─────
                     ↑    ↑       ↑    ↑
                  acc_lo  ideal_lo  ideal_hi  acc_hi
```

`fib.py` stays untouched — it remains the low-level math (retrace/extension level tables, generic confluence). The new module sits above it, consuming wave counts.

---

## Ratio definitions

For impulse with segments `(W1, W2, W3, W4, W5)`:

| Ratio | Formula |
|---|---|
| `w2_w1_retrace` | `\|W2.end − W2.start\| / \|W1.end − W1.start\|` |
| `w3_w1_extension` | `\|W3.end − W3.start\| / \|W1.end − W1.start\|` |
| `w4_w3_retrace` | `\|W4.end − W4.start\| / \|W3.end − W3.start\|` |
| `w5_w1_ratio` | `\|W5.end − W5.start\| / \|W1.end − W1.start\|` |
| `w5_thrust_extension` | `\|W5.end − W5.start\| / \|W3.end − W1.start\|` |

For zigzag with segments `(A, B, C)`:

| Ratio | Formula |
|---|---|
| `b_a_retrace` | `\|B.end − B.start\| / \|A.end − A.start\|` |
| `c_a_ratio` | `\|C.end − C.start\| / \|A.end − A.start\|` |

All ratios computed from `Decimal` segment endpoints, returned as `float` (consumers expect float).

If `count.segments()` has fewer segments than the pattern requires (defensive), the missing ratios are `None`. This shouldn't happen post-validator, but the function tolerates it.

---

## engine_score integration

`enumerator._confluence_bonus(count, tpl)` becomes:

```python
def _confluence_bonus(count: WaveCount, tpl: PatternTemplate) -> float:
    feats = fib_features(count)
    if feats.is_curated_pattern:
        return confluence_score(feats)  # already weighted within 0.20 budget
    # Fallback for flat / expanded_flat / contracting_triangle / ending_diagonal
    segs = count.segments()
    if len(segs) < 2:
        return 0.0
    return fib_confluence_score(target_price=segs[1].end.price, reference=segs[0])
```

Wrapper math in `_score()` is unchanged: `0.50 × base + 0.20 × confluence + 0.30 × recency`. Curated-pattern counts now get the multi-ratio score; non-curated counts get the legacy generic score.

**One subtle point:** `confluence_score` returns the *weighted sum* (max 0.20 for impulse, 0.20 for zigzag), so it must be divided by the 0.20 budget before being multiplied by `_WEIGHT_CONFLUENCE = 0.20` again — otherwise impulse counts can never exceed `0.20 × 0.20 = 0.04` confluence contribution. Two clean options:

1. `confluence_score` returns a normalized 0..1 (sum of `weight_share × trapezoid` where `weight_share = weight / 0.20`). Then `_score()` multiplies by `_WEIGHT_CONFLUENCE` as today.
2. `confluence_score` returns 0..0.20 directly, and `_score()` adds it raw without re-multiplying.

Pick **(1)** — keeps the budget as a config value in one place (`fib_bands.py`) and leaves `_score()` arithmetic unchanged.

---

## Right-edge model integration

`right_edge/extractor.py FeatureExtractor.extract(...)` accepts a new optional kwarg:

```python
def extract(
    self,
    *,
    pivot: PivotEvent,
    snapshot: DegreeSnapshot,
    bars: list[Bar],
    top_count: WaveCount | None = None,  # NEW
) -> dict[str, float]: ...
```

When `top_count is not None`, the extractor adds 7 raw-ratio features named `fib_w2_w1_retrace`, `fib_w3_w1_extension`, ..., `fib_c_a_ratio`. Missing ratios (e.g., zigzag count → impulse ratios are `None`) are encoded as `0.0` plus a paired indicator flag `fib_<name>_present` (1.0 if the ratio is set, 0.0 otherwise). Indicator flags are critical: the model must distinguish "ratio not applicable" from "ratio is exactly 0".

Total new features: 7 raw + 7 indicators = 14.

When `top_count is None` (e.g., bars too short for a count, no pattern fit), all 14 features are 0 — equivalent to "no count data, indicators all off".

Callers gain a new step before extraction:

- **`pipeline.analyze`** — already runs `enumerate_counts` for ranked counts; pipes `ranked_daily[0].count` (or `None` if empty) into the extractor.
- **`right_edge/assessment.py:assess_from_bars`** — adds an enumerator call for the relevant degree, takes top-1, passes into the extractor.

The enumerator call uses pivots filtered to the assessment's degree. Same pivot stream the snapshot already uses, so no extra detection cost.

### Feature schema implication

The two shipped right-edge model artifacts (`logistic` and `calibrated_heuristic`) are trained on the *current* feature schema. Adding 14 features makes the old artifacts incompatible. The plan must include:

- A retrain pass on both artifact types using the extended feature set.
- Loader version-check (loader rejects an artifact whose feature-name list doesn't match the current `FeatureExtractor`'s output).
- Promotion-gate verdict captured in a fresh `docs/superpowers/specs/YYYY-MM-DD-fib-features-training-run.md`.

If retraining shows the new features don't lift calibration, the gate should HOLD and the new fib features should still ship into engine_score (which is the headline win). That decision is captured at training-run time, not now.

---

## Test plan

### `tests/elliott/test_fib_features.py` (new)

- **`fib_features` golden tests:** for each curated pattern, hand-construct a `WaveCount` with known prices and assert each ratio matches the expected `Decimal` math to 6 decimal places.
- **Non-curated fallback:** `fib_features(flat_count).is_curated_pattern is False` and all ratio fields are `None`.
- **Defensive empty count:** count with too few segments → all ratios `None`, no exception.

### `tests/elliott/test_fib_bands.py` (new)

- **`trapezoid` boundary cases:** value at `acceptable_lo` returns `0.0`, at `ideal_lo` returns `1.0`, midpoint of ideal returns `1.0`, at `acceptable_hi` returns `0.0`, just outside acc band returns `0.0`.
- **`trapezoid` ramp slope:** value halfway between `acceptable_lo` and `ideal_lo` returns `0.5` (within float tolerance).
- **Weight-sum invariants:** `sum(b.weight for b in IMPULSE_BANDS.values()) == 0.20`, same for `ZIGZAG_BANDS`. Asserted as a test, not a runtime check.
- **`confluence_score` normalization:** textbook impulse (every ratio at ideal-band midpoint) returns `1.0`. Off-textbook impulse (every ratio outside acceptable) returns `0.0`. Mixed cases return values in `(0, 1)`.

### `tests/elliott/test_enumerator.py` (modify)

- Existing golden score tests will see numerical changes — update expected values.
- Add: an impulse count with textbook ratios outscores an otherwise-identical impulse count with off-textbook ratios.
- Add: a flat count's score is unchanged from current behavior (fallback path).

### `tests/right_edge/test_extractor.py` (modify)

- New: extractor with `top_count=None` produces zero-valued fib features and zero-valued indicators.
- New: extractor with an impulse `top_count` populates 5 raw fib features + 5 indicators set to 1, zigzag fields set to 0 with indicators 0.
- New: feature-name list contains all 14 new names in a stable order (asserted as a list snapshot).

### `tests/right_edge/test_loader.py` (modify)

- New: loader rejects a stub artifact whose `feature_names` doesn't match the current extractor's names. Surfaces a clear error rather than silently mis-aligning features.

### `tests/right_edge/test_assessment.py` (modify)

- New: `assess_from_bars` invokes the enumerator and passes the top count into the extractor. Verified by feeding bars that produce a known count and asserting the predicted features include the expected fib values.

### `tests/test_pipeline.py` (modify)

- Existing tests: spot-check that `analyze()`'s right-edge probability is computed using a top-count-aware extractor. Use a stub model that records its inputs.

---

## Open questions deferred to plan stage

- Exact band boundaries — the values in `fib_bands.py` are a textbook starting point. The plan can refine using historical data if cheap, otherwise ship as-is and tune later.
- Whether to expose the per-ratio scores in deepdive UI ("Wave 3 confluence: 0.85 (textbook)") — UI follow-up, not in this spec.
- Whether to surface fib features via `pipeline.AnalyzeResult` so the deepdive page can render them without re-computing — likely yes, but not load-bearing for the core feature work.

---

## Summary

| File | Change |
|---|---|
| `src/wave_alpha/elliott/fib_bands.py` | NEW — `Band` dataclass + `IMPULSE_BANDS`, `ZIGZAG_BANDS`, `CURATED_PATTERNS` |
| `src/wave_alpha/elliott/fib_features.py` | NEW — `FibFeatures` dataclass, `fib_features()`, `confluence_score()`, `trapezoid()` |
| `src/wave_alpha/elliott/enumerator.py` | MODIFY — `_confluence_bonus` dispatches curated vs. fallback |
| `src/wave_alpha/right_edge/extractor.py` | MODIFY — `extract()` adds `top_count` kwarg + 14 new feature slots |
| `src/wave_alpha/right_edge/assessment.py` | MODIFY — runs enumerator, passes top count to extractor |
| `src/wave_alpha/right_edge/loader.py` | MODIFY — feature-name version check |
| `src/wave_alpha/pipeline.py` | MODIFY — passes top ranked count into right-edge extractor |
| `tests/elliott/test_fib_features.py` | NEW |
| `tests/elliott/test_fib_bands.py` | NEW |
| `tests/elliott/test_enumerator.py` | MODIFY |
| `tests/right_edge/test_extractor.py` | MODIFY |
| `tests/right_edge/test_loader.py` | MODIFY |
| `tests/right_edge/test_assessment.py` | MODIFY |
| `tests/test_pipeline.py` | MODIFY |
| Trained artifacts | RETRAIN — `logistic` + `calibrated_heuristic` on extended feature set |
| `docs/superpowers/specs/YYYY-MM-DD-fib-features-training-run.md` | NEW (verdict captured during training step) |

No DB schema change. No CLI change. No web template change in this spec — UI surfacing is a follow-up.
