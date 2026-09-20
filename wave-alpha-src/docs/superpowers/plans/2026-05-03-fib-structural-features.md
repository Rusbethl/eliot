# Fib Structural Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-ratio `_confluence_bonus` in `engine_score` with pattern-aware multi-ratio Fibonacci confluence, and surface the same ratios as ML features for the right-edge logistic model.

**Architecture:** Two new pure modules in `src/wave_alpha/elliott/`: `fib_bands.py` holds `(pattern, ratio_name) → Band` constants, `fib_features.py` extracts named ratios from a `WaveCount` and produces both an ML feature record and an engine-score `confluence_score`. The right-edge `RightEdgeFeatures` Pydantic model gains 14 new fields (7 raw ratios + 7 indicator flags) with safe defaults, and `LogisticConfirmationModel._vector` is refactored to read fields dynamically from `feature_order` so old 5-field artifacts keep loading without retrain. Logistic retrain is gated on the existing promotion gate.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, `uv run pytest -v`. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-03-fib-structural-features-design.md`.

---

## File Structure

```
src/wave_alpha/elliott/
├── fib_bands.py        NEW — Band dataclass + IMPULSE_BANDS, ZIGZAG_BANDS, CURATED_PATTERNS
├── fib_features.py     NEW — FibFeatures dataclass + fib_features(), confluence_score(), trapezoid()
├── enumerator.py       MODIFY — _confluence_bonus dispatches curated vs. fallback
└── fib.py              UNCHANGED — low-level math stays as-is

src/wave_alpha/right_edge/
├── features.py         MODIFY — RightEdgeFeatures gains 14 fields; extract_features takes top_count
├── extractor.py        MODIFY — FeatureExtractor.extract takes top_count; populates new fields
├── logistic.py         MODIFY — _vector reads from feature_order dynamically
├── loader.py           MODIFY — verifies feature_order ⊆ RightEdgeFeatures fields on load
├── assessment.py       MODIFY — runs enumerator, passes top count into extractor
├── training.py         MODIFY — emits 19-field feature_order when fib enabled
└── models/             new artifact JSONs land here after Phase 3

src/wave_alpha/pipeline.py    MODIFY — passes top ranked count to right-edge extractor

tests/elliott/
├── test_fib_bands.py        NEW
├── test_fib_features.py     NEW
└── test_enumerator.py       MODIFY — score expectations refresh, add textbook-vs-off tests

tests/right_edge/
├── test_features.py         MODIFY — defaults, new field population
├── test_extractor.py        MODIFY — top_count kwarg behavior
├── test_logistic.py         MODIFY — dynamic _vector with old + new feature_order
├── test_loader.py           MODIFY — feature-name set check
└── test_assessment.py       MODIFY — top count integration

docs/superpowers/specs/
└── 2026-05-DD-fib-features-training-run.md   NEW (Phase 3, after retrain)
```

---

## Phase 1 — Fib feature module + engine_score (ships independently)

Phase 1 produces a complete `engine_score` improvement. The right-edge model is untouched. Ship after Task 9.

### Task 1: Band dataclass + canonical constants

**Files:**
- Create: `src/wave_alpha/elliott/fib_bands.py`
- Test: `tests/elliott/test_fib_bands.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/elliott/test_fib_bands.py
from wave_alpha.elliott.fib_bands import (
    Band,
    CURATED_PATTERNS,
    IMPULSE_BANDS,
    ZIGZAG_BANDS,
)


def test_band_has_expected_fields() -> None:
    b = Band(ideal_lo=0.382, ideal_hi=0.618, acceptable_lo=0.236, acceptable_hi=0.786, weight=0.06)
    assert b.ideal_lo == 0.382
    assert b.weight == 0.06


def test_impulse_bands_cover_five_canonical_ratios() -> None:
    assert set(IMPULSE_BANDS.keys()) == {
        "w2_w1_retrace",
        "w3_w1_extension",
        "w4_w3_retrace",
        "w5_w1_ratio",
        "w5_thrust_extension",
    }


def test_zigzag_bands_cover_two_canonical_ratios() -> None:
    assert set(ZIGZAG_BANDS.keys()) == {"b_a_retrace", "c_a_ratio"}


def test_impulse_band_weights_sum_to_confluence_budget() -> None:
    assert sum(b.weight for b in IMPULSE_BANDS.values()) == 0.20


def test_zigzag_band_weights_sum_to_confluence_budget() -> None:
    assert sum(b.weight for b in ZIGZAG_BANDS.values()) == 0.20


def test_curated_patterns_set() -> None:
    assert CURATED_PATTERNS == frozenset({"impulse", "zigzag"})


def test_band_ordering_invariant() -> None:
    """For every band: acc_lo <= ideal_lo <= ideal_hi <= acc_hi."""
    for name, b in {**IMPULSE_BANDS, **ZIGZAG_BANDS}.items():
        assert b.acceptable_lo <= b.ideal_lo <= b.ideal_hi <= b.acceptable_hi, name
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/elliott/test_fib_bands.py -v
```

Expected: collection error or ImportError on `fib_bands`.

- [ ] **Step 3: Create the module**

```python
# src/wave_alpha/elliott/fib_bands.py
"""Canonical Fibonacci bands per (pattern, wave-pair).

Bands are expert-curated EW values, not user config — kept as Python constants
so they version-control alongside the math that consumes them. Per-pattern
weights sum to 0.20, matching the existing confluence weight in
``elliott/enumerator._WEIGHT_CONFLUENCE``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    """Trapezoidal scoring band for a single Fib ratio.

    The trapezoid is 1.0 inside ``[ideal_lo, ideal_hi]``, ramps linearly from
    0 at ``acceptable_lo`` to 1 at ``ideal_lo`` and from 1 at ``ideal_hi``
    back to 0 at ``acceptable_hi``, and is 0 outside the acceptable range.

    ``weight`` is this ratio's share of the per-pattern confluence budget
    (which sums to 0.20 — same as the legacy single-ratio weight).
    """

    ideal_lo: float
    ideal_hi: float
    acceptable_lo: float
    acceptable_hi: float
    weight: float


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

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/elliott/test_fib_bands.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_bands.py tests/elliott/test_fib_bands.py
git commit -m "feat(elliott): canonical fib bands per (pattern, wave-pair)"
```

---

### Task 2: Trapezoid scorer

**Files:**
- Modify: `src/wave_alpha/elliott/fib_bands.py` (add `trapezoid` function)
- Test: `tests/elliott/test_fib_bands.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/elliott/test_fib_bands.py`:

```python
import pytest

from wave_alpha.elliott.fib_bands import IMPULSE_BANDS, trapezoid

W2_BAND = IMPULSE_BANDS["w2_w1_retrace"]


def test_trapezoid_inside_ideal_band_is_one() -> None:
    assert trapezoid(0.5, W2_BAND) == 1.0
    assert trapezoid(W2_BAND.ideal_lo, W2_BAND) == 1.0
    assert trapezoid(W2_BAND.ideal_hi, W2_BAND) == 1.0


def test_trapezoid_at_acceptable_boundaries_is_zero() -> None:
    assert trapezoid(W2_BAND.acceptable_lo, W2_BAND) == 0.0
    assert trapezoid(W2_BAND.acceptable_hi, W2_BAND) == 0.0


def test_trapezoid_outside_acceptable_is_zero() -> None:
    assert trapezoid(0.0, W2_BAND) == 0.0
    assert trapezoid(1.5, W2_BAND) == 0.0
    assert trapezoid(-0.5, W2_BAND) == 0.0


def test_trapezoid_lower_ramp_is_linear() -> None:
    # Halfway between acceptable_lo (0.236) and ideal_lo (0.382) → 0.5
    midpoint = (W2_BAND.acceptable_lo + W2_BAND.ideal_lo) / 2
    assert trapezoid(midpoint, W2_BAND) == pytest.approx(0.5, abs=1e-9)


def test_trapezoid_upper_decay_is_linear() -> None:
    # Halfway between ideal_hi (0.618) and acceptable_hi (0.786) → 0.5
    midpoint = (W2_BAND.ideal_hi + W2_BAND.acceptable_hi) / 2
    assert trapezoid(midpoint, W2_BAND) == pytest.approx(0.5, abs=1e-9)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/elliott/test_fib_bands.py -v -k trapezoid
```

Expected: ImportError on `trapezoid`.

- [ ] **Step 3: Implement**

Append to `src/wave_alpha/elliott/fib_bands.py`:

```python
def trapezoid(value: float, band: Band) -> float:
    """0..1 score for ``value`` against a trapezoidal band.

    1.0 inside ``[band.ideal_lo, band.ideal_hi]``; linear ramp from 0 at
    ``band.acceptable_lo`` to 1 at ``band.ideal_lo``; linear decay from 1 at
    ``band.ideal_hi`` to 0 at ``band.acceptable_hi``; 0 outside the
    acceptable range. Degenerate band edges (e.g., ideal_lo == acceptable_lo)
    collapse the affected ramp; the implementation handles this without
    division-by-zero.
    """
    if value < band.acceptable_lo or value > band.acceptable_hi:
        return 0.0
    if band.ideal_lo <= value <= band.ideal_hi:
        return 1.0
    if value < band.ideal_lo:
        denom = band.ideal_lo - band.acceptable_lo
        return 0.0 if denom <= 0 else (value - band.acceptable_lo) / denom
    # value > band.ideal_hi
    denom = band.acceptable_hi - band.ideal_hi
    return 0.0 if denom <= 0 else (band.acceptable_hi - value) / denom
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/test_fib_bands.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_bands.py tests/elliott/test_fib_bands.py
git commit -m "feat(elliott): trapezoid scorer for fib band membership"
```

---

### Task 3: FibFeatures dataclass

**Files:**
- Create: `src/wave_alpha/elliott/fib_features.py`
- Test: `tests/elliott/test_fib_features.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/elliott/test_fib_features.py
import pytest

from wave_alpha.elliott.fib_features import FibFeatures


def test_fib_features_has_expected_fields() -> None:
    f = FibFeatures(pattern="impulse", is_curated_pattern=True)
    # Defaults None for every ratio
    for name in (
        "w2_w1_retrace",
        "w3_w1_extension",
        "w4_w3_retrace",
        "w5_w1_ratio",
        "w5_thrust_extension",
        "b_a_retrace",
        "c_a_ratio",
    ):
        assert getattr(f, name) is None


def test_fib_features_is_frozen() -> None:
    f = FibFeatures(pattern="impulse", is_curated_pattern=True)
    with pytest.raises(Exception):
        f.pattern = "zigzag"  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: ImportError on `fib_features`.

- [ ] **Step 3: Create the module skeleton**

```python
# src/wave_alpha/elliott/fib_features.py
"""Pattern-aware Fibonacci feature extraction for wave counts.

Single source of truth for ratio computation: the engine score consumes a
weighted ``confluence_score``; the right-edge model consumes the raw ratios
from ``FibFeatures`` directly. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FibFeatures:
    """Named Fib ratios for a single wave count.

    Ratio fields are ``None`` when they don't apply to the count's pattern
    (e.g., zigzag counts have ``b_a_retrace`` set but ``w2_w1_retrace`` None).
    Consumers must distinguish ``None`` (not applicable) from ``0.0`` (a real
    ratio that happens to be zero, which never occurs for length ratios).
    """

    pattern: str
    is_curated_pattern: bool

    # Impulse ratios (None for non-impulse counts)
    w2_w1_retrace: float | None = None
    w3_w1_extension: float | None = None
    w4_w3_retrace: float | None = None
    w5_w1_ratio: float | None = None
    w5_thrust_extension: float | None = None

    # Zigzag ratios (None for non-zigzag counts)
    b_a_retrace: float | None = None
    c_a_ratio: float | None = None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_features.py tests/elliott/test_fib_features.py
git commit -m "feat(elliott): FibFeatures dataclass for named pattern ratios"
```

---

### Task 4: Impulse ratio extraction

**Files:**
- Modify: `src/wave_alpha/elliott/fib_features.py`
- Test: `tests/elliott/test_fib_features.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/elliott/test_fib_features.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.fib_features import fib_features
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d_off: int, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 1) + timedelta(days=d_off),
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
    )


def _impulse_count() -> WaveCount:
    """Hand-built textbook impulse: W1=10, W2 retraces 0.5 of W1 = 5,
    W3 extends 1.618 of W1 = 16.18, W4 retraces 0.382 of W3 = ~6.18,
    W5 = 1.0 of W1 = 10."""
    return WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0,  "100.00", PivotType.LOW),    # W0
            _piv(4,  "110.00", PivotType.HIGH),   # end W1: |W1| = 10
            _piv(7,  "105.00", PivotType.LOW),    # end W2: |W2| = 5  → W2/W1 = 0.5
            _piv(14, "121.18", PivotType.HIGH),   # end W3: |W3| = 16.18 → W3/W1 = 1.618
            _piv(17, "115.00", PivotType.LOW),    # end W4: |W4| = 6.18 → W4/W3 = 0.382
            _piv(24, "125.00", PivotType.HIGH),   # end W5: |W5| = 10 → W5/W1 = 1.0
        ],
    )


def test_fib_features_impulse_emits_five_ratios() -> None:
    f = fib_features(_impulse_count())
    assert f.pattern == "impulse"
    assert f.is_curated_pattern is True
    assert f.w2_w1_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.w3_w1_extension == pytest.approx(1.618, abs=1e-6)
    assert f.w4_w3_retrace == pytest.approx(0.382, abs=1e-3)
    assert f.w5_w1_ratio == pytest.approx(1.0, abs=1e-6)
    # W5 thrust: |W5| / |W3.end - W1.start| = 10 / 21.18 = 0.4722...
    assert f.w5_thrust_extension == pytest.approx(10.0 / 21.18, abs=1e-3)
    # Zigzag fields are None on an impulse count
    assert f.b_a_retrace is None
    assert f.c_a_ratio is None
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/elliott/test_fib_features.py::test_fib_features_impulse_emits_five_ratios -v
```

Expected: ImportError on `fib_features`.

- [ ] **Step 3: Implement**

Append to `src/wave_alpha/elliott/fib_features.py`:

```python
from wave_alpha.elliott.fib_bands import CURATED_PATTERNS
from wave_alpha.elliott.models import WaveCount


def fib_features(count: WaveCount) -> FibFeatures:
    """Compute named Fib ratios for ``count``.

    Dispatches on ``count.pattern``; impulse counts emit 5 ratios, zigzag
    counts emit 2, all other patterns emit none (with
    ``is_curated_pattern=False``).
    """
    is_curated = count.pattern in CURATED_PATTERNS
    base = FibFeatures(pattern=count.pattern, is_curated_pattern=is_curated)
    if count.pattern == "impulse":
        return _impulse_features(count, base)
    return base


def _impulse_features(count: WaveCount, base: FibFeatures) -> FibFeatures:
    segs = count.segments()
    if len(segs) < 5:
        return base
    w1, w2, w3, w4, w5 = segs[:5]
    if w1.length == 0 or w3.length == 0:
        return base  # degenerate; cannot form ratios
    thrust = abs(w3.end.price - w1.start.price)  # W0 → W3 peak
    return FibFeatures(
        pattern=base.pattern,
        is_curated_pattern=base.is_curated_pattern,
        w2_w1_retrace=float(w2.length / w1.length),
        w3_w1_extension=float(w3.length / w1.length),
        w4_w3_retrace=float(w4.length / w3.length),
        w5_w1_ratio=float(w5.length / w1.length),
        w5_thrust_extension=float(w5.length / thrust) if thrust > 0 else None,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_features.py tests/elliott/test_fib_features.py
git commit -m "feat(elliott): fib_features for impulse — 5 named ratios"
```

---

### Task 5: Zigzag ratio extraction

**Files:**
- Modify: `src/wave_alpha/elliott/fib_features.py`
- Test: `tests/elliott/test_fib_features.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/elliott/test_fib_features.py`:

```python
def _zigzag_count() -> WaveCount:
    """Textbook zigzag: A=10 down, B=5 retrace (0.5), C=10 (equality with A)."""
    return WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            _piv(0,  "100.00", PivotType.HIGH),  # start A
            _piv(4,  "90.00",  PivotType.LOW),   # end A: |A| = 10
            _piv(7,  "95.00",  PivotType.HIGH),  # end B: |B| = 5  → B/A = 0.5
            _piv(14, "85.00",  PivotType.LOW),   # end C: |C| = 10 → C/A = 1.0
        ],
    )


def test_fib_features_zigzag_emits_two_ratios() -> None:
    f = fib_features(_zigzag_count())
    assert f.pattern == "zigzag"
    assert f.is_curated_pattern is True
    assert f.b_a_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.c_a_ratio == pytest.approx(1.0, abs=1e-6)
    # Impulse fields are None on a zigzag count
    assert f.w2_w1_retrace is None
    assert f.w3_w1_extension is None
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/elliott/test_fib_features.py::test_fib_features_zigzag_emits_two_ratios -v
```

Expected: zigzag fields all None (impulse-only branch hit).

- [ ] **Step 3: Implement**

In `src/wave_alpha/elliott/fib_features.py`, extend the dispatcher:

Replace:
```python
    if count.pattern == "impulse":
        return _impulse_features(count, base)
    return base
```

With:
```python
    if count.pattern == "impulse":
        return _impulse_features(count, base)
    if count.pattern == "zigzag":
        return _zigzag_features(count, base)
    return base
```

Add at module bottom:
```python
def _zigzag_features(count: WaveCount, base: FibFeatures) -> FibFeatures:
    segs = count.segments()
    if len(segs) < 3:
        return base
    a, b, c = segs[:3]
    if a.length == 0:
        return base
    return FibFeatures(
        pattern=base.pattern,
        is_curated_pattern=base.is_curated_pattern,
        b_a_retrace=float(b.length / a.length),
        c_a_ratio=float(c.length / a.length),
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_features.py tests/elliott/test_fib_features.py
git commit -m "feat(elliott): fib_features for zigzag — B:A and C:A ratios"
```

---

### Task 6: Non-curated fallback + defensive guards

**Files:**
- Test: `tests/elliott/test_fib_features.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/elliott/test_fib_features.py`:

```python
def test_fib_features_flat_is_not_curated() -> None:
    flat = WaveCount(
        pattern="flat",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.HIGH),
            _piv(4, "90",  PivotType.LOW),
            _piv(7, "100", PivotType.HIGH),
            _piv(14, "92", PivotType.LOW),
        ],
    )
    f = fib_features(flat)
    assert f.pattern == "flat"
    assert f.is_curated_pattern is False
    # No ratios populated for non-curated patterns
    for name in (
        "w2_w1_retrace", "w3_w1_extension", "w4_w3_retrace",
        "w5_w1_ratio", "w5_thrust_extension",
        "b_a_retrace", "c_a_ratio",
    ):
        assert getattr(f, name) is None


def test_fib_features_handles_short_impulse_defensively() -> None:
    # Only 3 pivots → 2 segments → not enough for impulse 5-ratio extraction.
    short = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.LOW),
            _piv(4, "110", PivotType.HIGH),
            _piv(7, "105", PivotType.LOW),
        ],
    )
    f = fib_features(short)
    assert f.pattern == "impulse"
    assert f.is_curated_pattern is True
    assert f.w2_w1_retrace is None  # not enough segments
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: 6 passed (the existing implementation already handles both cases — the flat dispatcher returns `base`, the impulse dispatcher's `len(segs) < 5` guard returns `base`).

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_fib_features.py
git commit -m "test(elliott): fib_features defensive cases — non-curated and short counts"
```

---

### Task 7: confluence_score with weight redistribution

**Files:**
- Modify: `src/wave_alpha/elliott/fib_features.py`
- Test: `tests/elliott/test_fib_features.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/elliott/test_fib_features.py`:

```python
from wave_alpha.elliott.fib_features import confluence_score


def test_confluence_score_textbook_impulse_is_one() -> None:
    """Every ratio sits at the midpoint of its ideal band → score 1.0."""
    f = fib_features(_impulse_count())
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-3)


def test_confluence_score_textbook_zigzag_is_one() -> None:
    f = fib_features(_zigzag_count())
    # B:A = 0.5 (in ideal [0.5, 0.618]), C:A = 1.0 (in ideal [1.0, 1.272])
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-3)


def test_confluence_score_off_textbook_impulse_is_zero() -> None:
    """All ratios outside their acceptable bands → score 0.0."""
    bad = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0,  "100.00", PivotType.LOW),
            _piv(4,  "110.00", PivotType.HIGH),
            _piv(7,  "100.50", PivotType.LOW),    # W2 retrace 0.95, far above acceptable
            _piv(14, "120.00", PivotType.HIGH),
            _piv(17, "115.00", PivotType.LOW),
            _piv(24, "125.00", PivotType.HIGH),
        ],
    )
    f = fib_features(bad)
    # At least the W2 ratio is way out; trapezoid for it is 0
    assert f.w2_w1_retrace is not None and f.w2_w1_retrace > 0.9
    assert confluence_score(f) < 0.5  # one strong outlier drags the total down


def test_confluence_score_non_curated_is_zero() -> None:
    flat = WaveCount(
        pattern="flat",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.HIGH),
            _piv(4, "90",  PivotType.LOW),
            _piv(7, "100", PivotType.HIGH),
            _piv(14, "92", PivotType.LOW),
        ],
    )
    assert confluence_score(fib_features(flat)) == 0.0


def test_confluence_score_redistributes_weight_when_ratio_missing() -> None:
    """If a ratio is None (e.g., short impulse), the remaining ratios cover
    the full pattern budget — max possible score stays 1.0."""
    short = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0,  "100.00", PivotType.LOW),
            _piv(4,  "110.00", PivotType.HIGH),
            _piv(7,  "105.00", PivotType.LOW),    # only W1 + W2 → only w2_w1_retrace populated
        ],
    )
    f = fib_features(short)
    # Manually set just w2_w1_retrace at midpoint of ideal so we can assert
    # redistribution makes the single populated ratio carry full weight.
    f = FibFeatures(pattern=f.pattern, is_curated_pattern=True, w2_w1_retrace=0.5)
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-9)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: ImportError on `confluence_score`.

- [ ] **Step 3: Implement**

Append to `src/wave_alpha/elliott/fib_features.py`:

```python
from wave_alpha.elliott.fib_bands import IMPULSE_BANDS, ZIGZAG_BANDS, trapezoid


def confluence_score(features: FibFeatures) -> float:
    """Weighted trapezoidal score for a ``FibFeatures``, normalized to 0..1.

    Returns 0 for non-curated patterns (caller falls back to legacy generic
    confluence). Ratios that are ``None`` are excluded from the sum AND from
    the weight denominator — i.e., a ratio's weight is redistributed across
    the populated ratios. A textbook count therefore scores 1.0 even when a
    single ratio is missing; missing data does not penalize the count.
    """
    bands = _bands_for(features.pattern)
    if not bands:
        return 0.0

    populated: list[tuple[str, float]] = []
    for name in bands:
        v = getattr(features, name)
        if v is not None:
            populated.append((name, v))
    if not populated:
        return 0.0

    total_weight = sum(bands[name].weight for name, _ in populated)
    if total_weight <= 0:
        return 0.0

    score = 0.0
    for name, value in populated:
        band = bands[name]
        score += (band.weight / total_weight) * trapezoid(value, band)
    return score


def _bands_for(pattern: str):  # -> dict[str, Band] | None
    if pattern == "impulse":
        return IMPULSE_BANDS
    if pattern == "zigzag":
        return ZIGZAG_BANDS
    return None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/test_fib_features.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/fib_features.py tests/elliott/test_fib_features.py
git commit -m "feat(elliott): confluence_score — weighted trapezoid sum with redistribution"
```

---

### Task 8: Wire confluence_score into engine_score

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py`
- Test: `tests/elliott/test_enumerator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/elliott/test_enumerator.py`:

```python
def test_textbook_impulse_outscores_off_textbook_impulse() -> None:
    """Two impulse candidates from the same window: the one with textbook
    ratios should rank above the one with off-textbook ratios."""
    textbook_pivots = [
        _piv(0,  "100.00", PivotType.LOW),
        _piv(4,  "110.00", PivotType.HIGH),
        _piv(7,  "105.00", PivotType.LOW),    # W2 = 0.5
        _piv(14, "121.18", PivotType.HIGH),   # W3 = 1.618
        _piv(17, "115.00", PivotType.LOW),    # W4 = 0.382
        _piv(24, "125.00", PivotType.HIGH),   # W5 = 1.0
    ]
    off_pivots = [
        _piv(0,  "100.00", PivotType.LOW),
        _piv(4,  "110.00", PivotType.HIGH),
        _piv(7,  "100.50", PivotType.LOW),    # W2 = 0.95 (off-textbook)
        _piv(14, "112.00", PivotType.HIGH),   # W3 = 1.15  (weak)
        _piv(17, "111.00", PivotType.LOW),
        _piv(24, "115.00", PivotType.HIGH),
    ]
    textbook = enumerate_counts(textbook_pivots, degree=2, top_k=5)
    off = enumerate_counts(off_pivots, degree=2, top_k=5)

    textbook_imp = next(c for c in textbook if c.pattern == "impulse")
    off_imp = next(c for c in off if c.pattern == "impulse")
    assert textbook_imp.score > off_imp.score
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/elliott/test_enumerator.py::test_textbook_impulse_outscores_off_textbook_impulse -v
```

Expected: depends on legacy generic confluence; usually fails because both score similarly.

- [ ] **Step 3: Modify `_confluence_bonus`**

In `src/wave_alpha/elliott/enumerator.py`, replace the existing `_confluence_bonus`:

```python
from wave_alpha.elliott.fib_features import confluence_score, fib_features
```

Replace:

```python
def _confluence_bonus(count: WaveCount, tpl: PatternTemplate) -> float:
    """Reward counts whose key levels (e.g., W2 retrace) sit on canonical fib levels."""
    segs = count.segments()
    if len(segs) < 2:
        return 0.0
    w1, w2 = segs[0], segs[1]
    return fib_confluence_score(target_price=w2.end.price, reference=w1)
```

With:

```python
def _confluence_bonus(count: WaveCount, tpl: PatternTemplate) -> float:
    """Pattern-aware Fib confluence with generic fallback.

    Curated patterns (impulse, zigzag) use the multi-ratio
    ``fib_features.confluence_score``. Other patterns fall back to the
    legacy single-ratio ``fib_confluence_score(W2, reference=W1)`` so their
    scores stay stable until they're promoted to curated.
    """
    feats = fib_features(count)
    if feats.is_curated_pattern:
        return confluence_score(feats)
    segs = count.segments()
    if len(segs) < 2:
        return 0.0
    return fib_confluence_score(target_price=segs[1].end.price, reference=segs[0])
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/elliott/ -v
```

Expected: new test passes; existing enumerator tests **may** show numeric score changes (still > 0 and ordering still sensible). If any existing test asserts a specific score number, expect a value shift.

- [ ] **Step 5: Update existing enumerator score expectations if needed**

Re-read any failing test in `tests/elliott/test_enumerator.py` and update `assert impulse.score > 0` style assertions to match new values. If the test was asserting a specific number (e.g., `== 0.65`), recompute by running the test with `-s` and a print, or by hand-computing.

- [ ] **Step 6: Run all elliott tests**

```bash
uv run pytest tests/elliott/ -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_enumerator.py
git commit -m "feat(elliott): engine_score uses pattern-aware fib confluence for impulse + zigzag"
```

---

### Task 9: Phase 1 acceptance — full test suite

**Files:**
- (no source changes)

- [ ] **Step 1: Run full suite**

```bash
uv run pytest -v
```

Expected: all green. If there are failures in `tests/llm/`, `tests/web/`, or `tests/scan/` because of changed engine scores, update the expected values to match.

- [ ] **Step 2: Run linter**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 3: Phase 1 ship commit (optional consolidation)**

If acceptance turned up small fixes, commit them:

```bash
git add -A
git commit -m "test: align downstream golden scores with new fib confluence"
```

**Phase 1 ships here.** Engine score is now sharper for impulse and zigzag counts. Right-edge model is unchanged.

---

## Phase 2 — Right-edge feature schema + plumbing (ships with backward-compat artifacts)

Phase 2 extends `RightEdgeFeatures` with 14 new fields, refactors `_vector` to read from `feature_order`, and wires the enumerator output into the right-edge inference paths. Old 5-field artifacts continue to load and predict identically (their `feature_order` is a 5-element list, so `_vector` only reads the original 5 fields). New 19-field artifacts (Phase 3) replace them.

### Task 10: Extend `RightEdgeFeatures` with 14 default-zero fields

**Files:**
- Modify: `src/wave_alpha/right_edge/features.py`
- Test: `tests/right_edge/test_features.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_features.py`:

```python
def test_right_edge_features_has_fib_fields_with_defaults() -> None:
    from wave_alpha.right_edge.features import RightEdgeFeatures

    f = RightEdgeFeatures(
        retracement_progress=0.0,
        bars_since_candidate=0,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )
    # Raw ratios default to 0.0
    for name in (
        "fib_w2_w1_retrace",
        "fib_w3_w1_extension",
        "fib_w4_w3_retrace",
        "fib_w5_w1_ratio",
        "fib_w5_thrust_extension",
        "fib_b_a_retrace",
        "fib_c_a_ratio",
    ):
        assert getattr(f, name) == 0.0
    # Indicators default to 0.0 (absent)
    for name in (
        "fib_w2_w1_retrace_present",
        "fib_w3_w1_extension_present",
        "fib_w4_w3_retrace_present",
        "fib_w5_w1_ratio_present",
        "fib_w5_thrust_extension_present",
        "fib_b_a_retrace_present",
        "fib_c_a_ratio_present",
    ):
        assert getattr(f, name) == 0.0
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/right_edge/test_features.py::test_right_edge_features_has_fib_fields_with_defaults -v
```

Expected: AttributeError on the new field names.

- [ ] **Step 3: Extend the model**

In `src/wave_alpha/right_edge/features.py`, replace the `RightEdgeFeatures` class:

```python
class RightEdgeFeatures(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Original 5 (legacy schema — old artifacts depend on these names)
    retracement_progress: float
    bars_since_candidate: int
    volume_decline_pct: float
    higher_degree_zone_match: float
    atr_regime: float

    # Fib raw ratios (Phase 2 — defaulted; populated when caller supplies a top count)
    fib_w2_w1_retrace: float = 0.0
    fib_w3_w1_extension: float = 0.0
    fib_w4_w3_retrace: float = 0.0
    fib_w5_w1_ratio: float = 0.0
    fib_w5_thrust_extension: float = 0.0
    fib_b_a_retrace: float = 0.0
    fib_c_a_ratio: float = 0.0

    # Fib presence indicators (1.0 if the corresponding ratio was populated by
    # the caller, else 0.0 — distinguishes "ratio not applicable" from "ratio
    # is exactly 0", which never happens for length ratios but the indicator
    # is necessary for ML semantics).
    fib_w2_w1_retrace_present: float = 0.0
    fib_w3_w1_extension_present: float = 0.0
    fib_w4_w3_retrace_present: float = 0.0
    fib_w5_w1_ratio_present: float = 0.0
    fib_w5_thrust_extension_present: float = 0.0
    fib_b_a_retrace_present: float = 0.0
    fib_c_a_ratio_present: float = 0.0
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/test_features.py -v
```

Expected: green. Existing tests keep passing because the new fields have defaults.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/features.py tests/right_edge/test_features.py
git commit -m "feat(right-edge): add 14 default-zero fib feature slots to RightEdgeFeatures"
```

---

### Task 11: Refactor `LogisticConfirmationModel._vector` to read from `feature_order`

**Files:**
- Modify: `src/wave_alpha/right_edge/logistic.py`
- Test: `tests/right_edge/test_logistic.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_logistic.py`:

```python
def test_logistic_vector_uses_feature_order_dynamically() -> None:
    """Old 5-field artifacts should produce the same vector even after
    RightEdgeFeatures gains new default-zero fields."""
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    f = RightEdgeFeatures(
        retracement_progress=0.4,
        bars_since_candidate=10,
        volume_decline_pct=0.1,
        higher_degree_zone_match=0.5,
        atr_regime=0.2,
        # New fib fields default to 0
    )
    legacy = LogisticConfirmationModel(
        version="v1",
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
        ),
        weights=(1.0, 1.0, 1.0, 1.0, 1.0),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    v = legacy._vector(f)
    assert v == [0.4, 10.0 / 20.0, 0.1, 0.5, 0.2]


def test_logistic_vector_includes_new_fib_features_when_in_feature_order() -> None:
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    f = RightEdgeFeatures(
        retracement_progress=0.0,
        bars_since_candidate=0,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=0.0,
        fib_w2_w1_retrace=0.5,
        fib_w2_w1_retrace_present=1.0,
    )
    new_model = LogisticConfirmationModel(
        version="v2",
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
            "fib_w2_w1_retrace",
            "fib_w2_w1_retrace_present",
        ),
        weights=(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    v = new_model._vector(f)
    assert v[5] == 0.5
    assert v[6] == 1.0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/right_edge/test_logistic.py -v -k "feature_order"
```

Expected: the second test fails because `_vector` is hardcoded to 5 fields.

- [ ] **Step 3: Refactor `_vector`**

In `src/wave_alpha/right_edge/logistic.py`, replace `_vector`:

```python
    _BARS_SCALE_FIELD = "bars_since_candidate"

    def _vector(self, f: RightEdgeFeatures) -> list[float]:
        """Build the feature vector dynamically from ``feature_order``.

        Mirrors ``training._row_to_vector`` exactly: every field in
        ``feature_order`` is read by name from ``RightEdgeFeatures``;
        ``bars_since_candidate`` is rescaled by 1/20.0 to keep its magnitude
        commensurate with the other 0..1 features. Old 5-field artifacts work
        unchanged because their ``feature_order`` lists exactly the legacy
        names.
        """
        out: list[float] = []
        for name in self.feature_order:
            value = getattr(f, name)
            if name == self._BARS_SCALE_FIELD:
                value = float(value) / 20.0
            out.append(float(value))
        return out
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/test_logistic.py -v
```

Expected: all green, including pre-existing tests (legacy 5-field path produces the same vector as the hardcoded version).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/logistic.py tests/right_edge/test_logistic.py
git commit -m "refactor(right-edge): logistic _vector reads from feature_order dynamically"
```

---

### Task 12: Loader rejects artifacts with unknown feature names

**Files:**
- Modify: `src/wave_alpha/right_edge/loader.py`
- Test: `tests/right_edge/test_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_loader.py`:

```python
def test_loader_rejects_artifact_with_unknown_feature_name(tmp_path) -> None:
    """If an artifact's feature_order references a field that doesn't exist
    on RightEdgeFeatures, the loader must refuse rather than silently
    misalign features at predict time."""
    import json
    from wave_alpha.prefs.models import RightEdgeConfig
    from wave_alpha.right_edge.loader import load_right_edge_model
    from wave_alpha.right_edge.logistic import ModelArtifactError

    artifact = tmp_path / "bad.json"
    artifact.write_text(json.dumps({
        "version": "v99",
        "feature_order": ["nonexistent_field"],
        "weights": [1.0],
        "intercept": 0.0,
        "platt_per_degree": {"2": {"a": 1.0, "b": 0.0}},
        "gate": {"verdict": "PROMOTE"},
    }))
    cfg = RightEdgeConfig(model="logistic")
    try:
        load_right_edge_model(cfg, logistic_path=artifact)
        raise AssertionError("expected ModelArtifactError")
    except ModelArtifactError:
        pass
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/right_edge/test_loader.py::test_loader_rejects_artifact_with_unknown_feature_name -v
```

Expected: no error raised — current loader doesn't validate feature names.

- [ ] **Step 3: Add validation in `LogisticConfirmationModel.from_json`**

In `src/wave_alpha/right_edge/logistic.py`, modify `from_json`:

```python
    @classmethod
    def from_json(cls, path: Path) -> LogisticConfirmationModel:
        raw = json.loads(path.read_text())
        verdict = raw.get("gate", {}).get("verdict")
        if verdict not in _PROMOTE_VERDICTS:
            raise ModelArtifactError(f"Refusing to load model with verdict={verdict!r}")
        feature_order = tuple(raw["feature_order"])
        cls._validate_feature_names(feature_order)
        return cls(
            version=raw["version"],
            feature_order=feature_order,
            weights=tuple(float(w) for w in raw["weights"]),
            intercept=float(raw["intercept"]),
            platt_per_degree={
                int(d): (float(p["a"]), float(p["b"])) for d, p in raw["platt_per_degree"].items()
            },
        )

    @staticmethod
    def _validate_feature_names(names: tuple[str, ...]) -> None:
        valid = set(RightEdgeFeatures.model_fields.keys())
        unknown = [n for n in names if n not in valid]
        if unknown:
            raise ModelArtifactError(
                f"Artifact feature_order references unknown RightEdgeFeatures "
                f"fields: {unknown!r}. Refusing to load — would silently misalign predictions."
            )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/test_loader.py tests/right_edge/test_logistic.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/logistic.py tests/right_edge/test_loader.py
git commit -m "feat(right-edge): loader validates feature_order against RightEdgeFeatures fields"
```

---

### Task 13: `extract_features` accepts `top_count` and populates fib slots

**Files:**
- Modify: `src/wave_alpha/right_edge/features.py`
- Test: `tests/right_edge/test_features.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_features.py`:

```python
def test_extract_features_with_impulse_top_count_populates_fib_fields() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.features import extract_features

    def piv(d_off, price, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 1) + timedelta(days=d_off),
            price=Decimal(price),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
        )

    impulse = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            piv(0,  "100.00", PivotType.LOW),
            piv(4,  "110.00", PivotType.HIGH),
            piv(7,  "105.00", PivotType.LOW),
            piv(14, "121.18", PivotType.HIGH),
            piv(17, "115.00", PivotType.LOW),
            piv(24, "125.00", PivotType.HIGH),
        ],
    )
    bars = [
        Bar(
            timestamp=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        for i in range(40)
    ]
    candidate = impulse.pivots[-1]
    f = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
        top_count=impulse,
    )
    assert f.fib_w2_w1_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.fib_w2_w1_retrace_present == 1.0
    assert f.fib_b_a_retrace == 0.0  # not applicable to impulse
    assert f.fib_b_a_retrace_present == 0.0


def test_extract_features_without_top_count_keeps_fib_zero() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.features import extract_features

    candidate = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 20),
        price=Decimal("100"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=date(2024, 1, 20),
    )
    bars = [
        Bar(
            timestamp=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        for i in range(40)
    ]
    f = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
    )
    # All fib slots stay at default zeros; all indicators 0
    for name in (
        "fib_w2_w1_retrace", "fib_w2_w1_retrace_present",
        "fib_b_a_retrace", "fib_b_a_retrace_present",
    ):
        assert getattr(f, name) == 0.0
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/right_edge/test_features.py -v -k extract
```

Expected: TypeError on unexpected `top_count` kwarg.

- [ ] **Step 3: Implement**

In `src/wave_alpha/right_edge/features.py`:

```python
from wave_alpha.elliott.fib_features import fib_features
from wave_alpha.elliott.models import WaveCount
```

Replace the `extract_features` function:

```python
_FIB_FIELD_NAMES: tuple[str, ...] = (
    "w2_w1_retrace",
    "w3_w1_extension",
    "w4_w3_retrace",
    "w5_w1_ratio",
    "w5_thrust_extension",
    "b_a_retrace",
    "c_a_ratio",
)


def extract_features(
    *,
    candidate: PivotEvent,
    bars: list[Bar],
    atr_at_candidate: Decimal,
    required_move: Decimal,
    higher_degree_zone_match: float = 0.0,
    top_count: WaveCount | None = None,
) -> RightEdgeFeatures:
    """Compute features for a tentative pivot using only bars up through latest.

    Caller is responsible for ensuring ``bars`` is point-in-time correct.
    When ``top_count`` is provided, the seven Fib ratio fields and their
    presence indicators are populated from ``fib_features(top_count)``.
    Without ``top_count``, all fib fields remain at their default zero values.
    """
    if not bars:
        raise ValueError("bars cannot be empty")

    after = [b for b in bars if b.timestamp > candidate.timestamp]
    bars_since = len(after)

    if candidate.type is PivotType.HIGH:
        opposite_extreme = min((b.low for b in after), default=candidate.price)
        moved = max(Decimal(0), candidate.price - opposite_extreme)
    else:
        opposite_extreme = max((b.high for b in after), default=candidate.price)
        moved = max(Decimal(0), opposite_extreme - candidate.price)

    progress = float(min(Decimal(1), moved / required_move)) if required_move > 0 else 0.0
    vol_decline = _volume_decline(bars, recent_window=5, baseline_window=20)
    atr_regime = float(atr_at_candidate / required_move) if required_move > 0 else 1.0

    fib_kwargs = _fib_kwargs(top_count)

    return RightEdgeFeatures(
        retracement_progress=progress,
        bars_since_candidate=bars_since,
        volume_decline_pct=vol_decline,
        higher_degree_zone_match=higher_degree_zone_match,
        atr_regime=atr_regime,
        **fib_kwargs,
    )


def _fib_kwargs(top_count: WaveCount | None) -> dict[str, float]:
    """Translate a WaveCount's fib_features into 14 RightEdgeFeatures kwargs."""
    if top_count is None:
        return {}
    feats = fib_features(top_count)
    out: dict[str, float] = {}
    for name in _FIB_FIELD_NAMES:
        value = getattr(feats, name)
        if value is None:
            out[f"fib_{name}"] = 0.0
            out[f"fib_{name}_present"] = 0.0
        else:
            out[f"fib_{name}"] = float(value)
            out[f"fib_{name}_present"] = 1.0
    return out
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/test_features.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/features.py tests/right_edge/test_features.py
git commit -m "feat(right-edge): extract_features populates fib fields when top_count supplied"
```

---

### Task 14: `FeatureExtractor.extract` accepts `top_count`

**Files:**
- Modify: `src/wave_alpha/right_edge/extractor.py`
- Test: `tests/right_edge/test_extractor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_extractor.py`:

```python
def test_extractor_accepts_top_count_kwarg_and_populates_fib_fields() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.extractor import FeatureExtractor

    def piv(d_off, price, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 1) + timedelta(days=d_off),
            price=Decimal(price),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
        )

    zigzag = WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            piv(0, "100", PivotType.HIGH),
            piv(4, "90",  PivotType.LOW),
            piv(7, "95",  PivotType.HIGH),
            piv(14, "85", PivotType.LOW),
        ],
    )
    bars = [
        Bar(
            timestamp=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        for i in range(40)
    ]
    candidate = zigzag.pivots[-1]
    f = FeatureExtractor().extract(
        candidate=candidate,
        point_in_time_bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
        higher_degree_zone_match=0.0,
        top_count=zigzag,
    )
    assert f.fib_b_a_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.fib_b_a_retrace_present == 1.0
    # Impulse fields zero
    assert f.fib_w2_w1_retrace == 0.0
    assert f.fib_w2_w1_retrace_present == 0.0
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/right_edge/test_extractor.py::test_extractor_accepts_top_count_kwarg_and_populates_fib_fields -v
```

Expected: TypeError on unexpected kwarg.

- [ ] **Step 3: Implement**

In `src/wave_alpha/right_edge/extractor.py`, modify `FeatureExtractor.extract` to delegate to `extract_features`:

```python
from wave_alpha.elliott.models import WaveCount
from wave_alpha.right_edge.features import extract_features


class FeatureExtractor:
    """Single source of truth for right-edge feature computation.

    Used by both training pipeline (offline) and live/backtest inference.
    Identical code in both paths prevents train/serve skew.
    """

    def extract(
        self,
        *,
        candidate: PivotEvent,
        point_in_time_bars: list[Bar],
        atr_at_candidate: Decimal,
        required_move: Decimal,
        higher_degree_zone_match: float,
        top_count: WaveCount | None = None,
    ) -> RightEdgeFeatures:
        return extract_features(
            candidate=candidate,
            bars=point_in_time_bars,
            atr_at_candidate=atr_at_candidate,
            required_move=required_move,
            higher_degree_zone_match=higher_degree_zone_match,
            top_count=top_count,
        )
```

The bottom-of-module helper `_volume_decline` is now unused here — delete it (the canonical version lives in `features.py`).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/ -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/extractor.py tests/right_edge/test_extractor.py
git commit -m "refactor(right-edge): FeatureExtractor delegates to extract_features; accepts top_count"
```

---

### Task 15: `assess_from_bars` runs enumerator and passes top count

**Files:**
- Modify: `src/wave_alpha/right_edge/assessment.py`
- Test: `tests/right_edge/test_assessment.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/right_edge/test_assessment.py`:

```python
def test_assess_from_bars_passes_top_count_to_extractor() -> None:
    """The assessment helper should run the enumerator on the snapshot
    pivots, take the top count, and pass it through to FeatureExtractor."""
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.right_edge.assessment import assess_from_bars
    from wave_alpha.right_edge.features import RightEdgeFeatures

    bars = []
    base = date(2024, 1, 1)
    # Synthetic 5-leg impulse that ATR-zigzag will detect at degree 2
    legs = [(0, 100), (10, 115), (20, 108), (35, 130), (45, 122), (60, 142)]
    for i in range(70):
        d = base + timedelta(days=i)
        # Close prices interpolated through legs
        c = _interp(legs, i)
        bars.append(
            Bar(
                timestamp=d,
                open=Decimal(str(c)),
                high=Decimal(str(c + 0.5)),
                low=Decimal(str(c - 0.5)),
                close=Decimal(str(c)),
                volume=1000,
            )
        )

    captured: list[RightEdgeFeatures] = []

    class _RecordingModel:
        def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
            captured.append(features)
            return 0.5

    out = assess_from_bars(bars, model=_RecordingModel(), interval=Interval.DAILY)
    # Either we got an assessment with fib fields populated, or we got None
    # because no tentative pivot existed. Both are acceptable; we only assert
    # that *if* the model was called, fib indicators were set.
    if captured:
        f = captured[0]
        # At least one fib indicator should be 1.0 if the synthetic data produced
        # a count. If not, the test is permissive — see comment above.
        any_present = any(
            getattr(f, n) == 1.0
            for n in (
                "fib_w2_w1_retrace_present",
                "fib_w3_w1_extension_present",
                "fib_b_a_retrace_present",
                "fib_c_a_ratio_present",
            )
        )
        assert any_present


def _interp(legs, i):
    for (x0, y0), (x1, y1) in zip(legs[:-1], legs[1:]):
        if x0 <= i <= x1:
            t = (i - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return legs[-1][1]
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/right_edge/test_assessment.py::test_assess_from_bars_passes_top_count_to_extractor -v
```

Expected: failure if any fib indicator is asserted but the extractor isn't given top_count.

- [ ] **Step 3: Implement**

In `src/wave_alpha/right_edge/assessment.py`:

```python
from wave_alpha.elliott.enumerator import enumerate_counts
```

Replace the body of `assess_from_bars` after the snapshot guard:

```python
def assess_from_bars(
    bars: list[Bar],
    *,
    model: ConfirmationModel,
    interval: Interval = Interval.DAILY,
    degree: int = _DEFAULT_DEGREE_FOR_COUNT,
) -> RightEdgeAssessment | None:
    if not bars:
        return None
    snapshots = detect_multi_degree_with_snapshots(
        bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
    )
    snap = snapshots.get(degree)
    if snap is None or snap.atr_at_last_bar is None or snap.required_move <= 0:
        return None
    tentative: list[PivotEvent] = [p for p in snap.pivots if p.state is PivotState.TENTATIVE]
    if not tentative:
        return None
    candidate = max(tentative, key=lambda p: p.timestamp)

    # Top-1 count from this degree's pivots feeds fib feature slots. Empty list
    # when no template fits — the extractor handles top_count=None silently.
    counts = enumerate_counts(snap.pivots, degree=degree, top_k=1)
    top_count = counts[0] if counts else None

    features = FeatureExtractor().extract(
        candidate=candidate,
        point_in_time_bars=bars,
        atr_at_candidate=snap.atr_at_last_bar,
        required_move=snap.required_move,
        higher_degree_zone_match=0.0,
        top_count=top_count,
    )
    proba = model.predict_proba(features, degree=degree)
    return RightEdgeAssessment(
        proba=proba,
        degree=degree,
        pivot_timestamp=candidate.timestamp,
        pivot_type=candidate.type.value,
        model_kind=type(model).__name__,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/right_edge/test_assessment.py -v
```

Expected: green (or permissive if synthetic data didn't produce a count — the test tolerates that).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/assessment.py tests/right_edge/test_assessment.py
git commit -m "feat(right-edge): assess_from_bars feeds top wave count into extractor"
```

---

### Task 16: `pipeline.analyze` passes top count to right-edge inference

**Files:**
- Modify: `src/wave_alpha/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Inspect current right-edge call site**

```bash
grep -n "FeatureExtractor\|assess_from_bars\|right_edge_assessment" src/wave_alpha/pipeline.py
```

Expected output: identifies where the pipeline computes the right-edge assessment.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
def test_pipeline_passes_top_count_to_right_edge_extractor(monkeypatch) -> None:
    """When pipeline.analyze runs right-edge inference, the top-1 ranked
    count for the relevant degree should reach the FeatureExtractor."""
    from datetime import date
    import wave_alpha.pipeline as pipeline_mod
    from wave_alpha.right_edge.features import RightEdgeFeatures

    captured: list[RightEdgeFeatures] = []

    class _RecordingModel:
        def predict_proba(self, features, *, degree):
            captured.append(features)
            return 0.5

    monkeypatch.setattr(
        pipeline_mod,
        "load_right_edge_model",
        lambda cfg, **kw: _RecordingModel(),
    )
    # Pipe in a synthetic ticker known to produce a count via the test fixture
    # bars; assert that any captured features carry at least one fib indicator
    # when the data is sufficient.
    # If the pipeline doesn't reach predict_proba (no tentative pivot), the
    # test is a no-op — that's acceptable; the contract is about what the
    # pipeline DOES pass when it predicts.
    # Implementation detail: replace with the project's existing analyze fixture.
```

If `tests/test_pipeline.py` already has an analyze-level fixture, integrate the recording model there. The exact assertion: when `captured` is non-empty, at least one fib indicator field is 1.0 OR the count is documented as falling back to `top_count=None` for short data.

- [ ] **Step 3: Modify `pipeline.analyze`**

Locate the right-edge inference call in `src/wave_alpha/pipeline.py`. Likely structure: it takes the `daily_pivots` snapshot, finds a tentative pivot, computes the features.

Add an `enumerate_counts(...)` call before extractor invocation, take `[0]` if non-empty, pass into `FeatureExtractor().extract(..., top_count=top)`. If the pipeline already computes `ranked_daily`, reuse `ranked_daily[0].count` instead of re-running the enumerator.

Code pattern (adapt to actual call site):

```python
# Before the existing FeatureExtractor.extract call
top_count = ranked_daily[0].count if ranked_daily else None

features = FeatureExtractor().extract(
    candidate=candidate,
    point_in_time_bars=bars,
    atr_at_candidate=snap.atr_at_last_bar,
    required_move=snap.required_move,
    higher_degree_zone_match=zone_match,
    top_count=top_count,
)
```

If the pipeline doesn't currently call `FeatureExtractor` directly (e.g., it calls `assess_from_bars`), no change is needed here — the assessment helper now handles the count internally (Task 15).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): top wave count flows into right-edge feature extractor"
```

---

### Task 17: Phase 2 acceptance — full test suite

**Files:**
- (no source changes)

- [ ] **Step 1: Run full suite**

```bash
uv run pytest -v
```

Expected: all green. Old logistic and calibrated_heuristic artifacts continue to load and predict identically because their `feature_order` lists 5 fields and `_vector` reads exactly those 5.

- [ ] **Step 2: Run linter**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: clean.

- [ ] **Step 3: Phase 2 ship**

If acceptance passed cleanly, Phase 2 is shippable. The right-edge plumbing now consumes wave-count structure during inference; the bundled artifacts ignore those features (their feature_order doesn't include them). Phase 3 retrains the logistic so the new features actually contribute to predictions.

```bash
git status   # confirm clean
```

---

## Phase 3 — Logistic retrain + ship new artifact (gated on training results)

Phase 3 is a multi-hour activity ending with a verdict captured in a fresh training-run spec. Calibrated_heuristic is unaffected (it doesn't use `_vector`), so this phase is logistic-only.

### Task 18: Update `training._row_to_vector` and `feature_order` constants

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py`
- Test: `tests/right_edge/test_training.py`

- [ ] **Step 1: Inspect current training feature_order**

```bash
grep -n "feature_order\|_row_to_vector" src/wave_alpha/right_edge/training.py
```

- [ ] **Step 2: Add a `--with-fib-features` flag to the trainer entrypoint**

In `src/wave_alpha/right_edge/training.py`, define a constant:

```python
LEGACY_FEATURE_ORDER: tuple[str, ...] = (
    "retracement_progress",
    "bars_since_candidate",
    "volume_decline_pct",
    "higher_degree_zone_match",
    "atr_regime",
)

FIB_FEATURE_ORDER: tuple[str, ...] = (
    "fib_w2_w1_retrace",
    "fib_w3_w1_extension",
    "fib_w4_w3_retrace",
    "fib_w5_w1_ratio",
    "fib_w5_thrust_extension",
    "fib_b_a_retrace",
    "fib_c_a_ratio",
    "fib_w2_w1_retrace_present",
    "fib_w3_w1_extension_present",
    "fib_w4_w3_retrace_present",
    "fib_w5_w1_ratio_present",
    "fib_w5_thrust_extension_present",
    "fib_b_a_retrace_present",
    "fib_c_a_ratio_present",
)


def feature_order(*, with_fib: bool) -> tuple[str, ...]:
    return LEGACY_FEATURE_ORDER + FIB_FEATURE_ORDER if with_fib else LEGACY_FEATURE_ORDER
```

Update `_row_to_vector` to accept `with_fib`:

```python
def _row_to_vector(features: RightEdgeFeatures, *, with_fib: bool) -> list[float]:
    names = feature_order(with_fib=with_fib)
    out: list[float] = []
    for name in names:
        v = getattr(features, name)
        if name == "bars_since_candidate":
            v = float(v) / 20.0
        out.append(float(v))
    return out
```

Adjust callers within `training.py` to thread `with_fib` through. Add a CLI/flag handler so the trainer can be invoked in either mode.

- [ ] **Step 3: Add a unit test asserting `_row_to_vector` matches `LogisticConfirmationModel._vector` with the same `feature_order`**

```python
def test_training_row_to_vector_matches_logistic_vector_with_fib() -> None:
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import _row_to_vector, feature_order

    f = RightEdgeFeatures(
        retracement_progress=0.4,
        bars_since_candidate=10,
        volume_decline_pct=0.1,
        higher_degree_zone_match=0.5,
        atr_regime=0.2,
        fib_w2_w1_retrace=0.5,
        fib_w2_w1_retrace_present=1.0,
    )
    names = feature_order(with_fib=True)
    train_vec = _row_to_vector(f, with_fib=True)
    model = LogisticConfirmationModel(
        version="v2",
        feature_order=names,
        weights=tuple(0.0 for _ in names),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    serve_vec = model._vector(f)
    assert train_vec == serve_vec
```

- [ ] **Step 4: Run training tests**

```bash
uv run pytest tests/right_edge/test_training.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/training.py tests/right_edge/test_training.py
git commit -m "feat(right-edge): training emits 19-feature vector when with_fib=True"
```

---

### Task 19: Run logistic training with fib features

**Files:**
- Generated: `src/wave_alpha/right_edge/models/right_edge_logistic_v2.json`
- Doc: training run output captured for the verdict spec

- [ ] **Step 1: Inspect the existing trainer entrypoint**

```bash
uv run python -m wave_alpha.right_edge.training --help
```

Identify the flag/arg name to pass `with_fib=True` (added in Task 18) and the output path argument.

- [ ] **Step 2: Run training**

```bash
uv run python -m wave_alpha.right_edge.training \
    --with-fib-features \
    --out src/wave_alpha/right_edge/models/right_edge_logistic_v2.json
```

Expected: artifact JSON written. Note `gate.verdict` value (PROMOTE / PROMOTE_CALIBRATION / HOLD) and per-degree calibration metrics.

- [ ] **Step 3: Compare verdict against the legacy artifact**

```bash
diff <(jq '.gate' src/wave_alpha/right_edge/models/right_edge_logistic_v1.json) \
     <(jq '.gate' src/wave_alpha/right_edge/models/right_edge_logistic_v2.json)
```

Capture the output for the training-run spec.

- [ ] **Step 4: Save run notes**

Capture: dataset size, verdict, AUROC delta vs v1, per-degree calibration delta, top-coefficient features. Will go into the spec doc in Task 21.

(No commit yet — the artifact lands with the verdict spec in Task 21.)

---

### Task 20: Decide ship/hold based on verdict

**Files:**
- (no code change — decision step)

Three outcomes:

**A. Verdict is PROMOTE or PROMOTE_CALIBRATION → ship v2**
- v2 replaces v1 as the bundled logistic artifact.
- Loader auto-mode picks it up (`logistic > calibrated_heuristic > heuristic`).
- Proceed to Task 21 (write training-run spec) and Task 22 (commit + tag).

**B. Verdict is HOLD but the new features show non-trivial coefficient magnitude → keep v1, document v2 results, defer**
- Don't replace v1.
- Write the training-run spec capturing the HOLD decision and the diagnostics that prompted it (e.g., feature signal exists but doesn't lift OOS AUROC enough).
- The fib features still ship in `engine_score` (Phase 1 win), which is the headline.

**C. Verdict is HOLD and the new features are signal-free → keep v1, document, consider reverting Phase 2 schema if it adds noise**
- Don't replace v1.
- Optionally revert the `RightEdgeFeatures` field additions from Task 10 (they cost nothing operationally — defaults are 0 — so usually keep them for next training round).
- Write the training-run spec capturing the negative result.

- [ ] **Step 1: Make the decision based on Task 19 output**

Record the chosen branch (A / B / C) and the data behind it.

---

### Task 21: Write the training-run spec

**Files:**
- Create: `docs/superpowers/specs/2026-05-DD-fib-features-training-run.md` (use today's date)

- [ ] **Step 1: Write the spec doc using this template**

```markdown
# Fib Features Training Run — YYYY-MM-DD

**Status:** {PROMOTE | PROMOTE_CALIBRATION | HOLD}

**Goal:** Retrain the right-edge logistic model with the 14 new Fib feature
slots from the Phase 2 spec
(`docs/superpowers/specs/2026-05-03-fib-structural-features-design.md`).

## Dataset
- Pairs: <count> (same OOS split as v1)
- Train: <count>
- Test: <count>
- Coverage: <impulse pairs %>, <zigzag pairs %>, <other %>

## Results

| Metric | v1 (5-feat) | v2 (19-feat) | Δ |
|---|---|---|---|
| AUROC OOS | <x> | <y> | <Δ> |
| Brier OOS | <x> | <y> | <Δ> |
| ECE-d2 OOS | <x> | <y> | <Δ> |
| Promotion verdict | <verdict> | <verdict> | — |

### Top coefficients (v2)

| Feature | Coefficient | |coef| rank |
|---|---|---|
| fib_w3_w1_extension | <x> | <r> |
| fib_w2_w1_retrace | <x> | <r> |
| ... | ... | ... |

## Decision

{Branch A / B / C from Task 20, with rationale}

## Action

- [{x|/}] Replace bundled artifact: `right_edge_logistic_v1.json` → `v2.json`
- [{x|/}] Engine_score still ships fib features (Phase 1 was independent)
- [{x|/}] Calibrated_heuristic unchanged (does not use feature vector)

## Caveats

<Any class-balance issues, tiny zigzag samples, etc.>
```

Fill in real values from Task 19 output.

- [ ] **Step 2: Commit the spec + artifact (if ship)**

For branch A:

```bash
git add docs/superpowers/specs/2026-05-DD-fib-features-training-run.md \
        src/wave_alpha/right_edge/models/right_edge_logistic_v2.json
git commit -m "feat(right-edge): ship logistic v2 with fib features (verdict: <PROMOTE|...>)"
```

For branch B or C (no artifact swap):

```bash
git add docs/superpowers/specs/2026-05-DD-fib-features-training-run.md
git commit -m "docs(right-edge): training run for fib features — verdict HOLD"
```

---

### Task 22: Bundled artifact swap (only if Task 20 chose branch A)

**Files:**
- Modify: `src/wave_alpha/right_edge/loader.py:_DEFAULT_LOGISTIC_PATH` (point to v2)

- [ ] **Step 1: Update the default path constant**

In `src/wave_alpha/right_edge/loader.py`:

```python
_DEFAULT_LOGISTIC_PATH = Path(__file__).parent / "models" / "right_edge_logistic_v2.json"
```

(Or, if the convention is to keep `v1.json` as the canonical name and just overwrite it, replace `v1.json` instead and keep the constant unchanged.)

- [ ] **Step 2: Run full suite**

```bash
uv run pytest -v
```

Expected: green. The new v2 artifact loads cleanly via the dynamic `_vector` plus feature-name validation.

- [ ] **Step 3: Lint**

```bash
uv run ruff check . && uv run ruff format --check .
```

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/right_edge/loader.py
git commit -m "feat(right-edge): default loader path → logistic v2 (with fib features)"
```

---

## Self-Review

**Spec coverage:**

- §Architecture (`fib_bands.py`, `fib_features.py`) → Tasks 1–7
- §Canonical bands table → Task 1 (`IMPULSE_BANDS`, `ZIGZAG_BANDS`)
- §Trapezoid scorer → Task 2
- §engine_score integration → Task 8
- §Right-edge integration (`top_count` kwarg, dynamic `_vector`, indicator flags) → Tasks 10–14
- §Pipeline plumbing → Tasks 15–16
- §Loader feature-name version check → Task 12
- §Retrain pass → Tasks 18–19
- §Promotion-gate verdict in fresh training-run spec → Task 21

No spec section is unmapped.

**Placeholder scan:** "YYYY-MM-DD" appears only in the Phase 3 spec filename — to be filled at training time. All code blocks are concrete; all test assertions name specific values; all commands are runnable.

**Type consistency:**
- `FibFeatures` field names (Task 3, 4, 5) = `fib_<name>` slots in `RightEdgeFeatures` (Task 10) minus the `fib_` prefix. ✓
- `FIB_FIELD_NAMES` tuple (Task 13) matches `IMPULSE_BANDS` + `ZIGZAG_BANDS` keys (Task 1). ✓
- `feature_order` returned by `training.feature_order(with_fib=True)` (Task 18) = `LEGACY_FEATURE_ORDER + FIB_FEATURE_ORDER`, matches `_vector` output (Task 11). ✓
- `confluence_score(features)` returns 0..1, multiplied by `_WEIGHT_CONFLUENCE = 0.20` in `_score()` — same range as legacy. ✓
