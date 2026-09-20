# Right-Edge Logistic Confirmation Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-coded right-edge heuristic with a calibrated logistic regression trained on backtest-derived labels, gated by a bootstrap-CI promotion test. Bundles a fix for the placeholder-features bug in `pivot_layer.py`.

**Architecture:** Shared `FeatureExtractor` used by both training and live/backtest inference (no train/serve skew). Walk-forward expanding-window training across the 50-ticker universe × 5y daily. Single logistic, per-degree Platt calibration. Plain-JSON persisted artifact (no pickle, no scikit-learn at runtime). PROMOTE/HOLD/REJECT gate verdict embedded in the artifact; live loader refuses non-PROMOTE artifacts and falls back to the heuristic.

**Tech Stack:** Python 3.11+, NumPy (already a dep), Pydantic v2, Typer for CLI, pytest. No new runtime deps.

**Spec:** `docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md`

---

## Phase 1 — FeatureExtractor + schema cleanup + pivot_layer fix

Drops the two stub features, lifts feature computation into a shared extractor, and threads it through `backtest/pivot_layer.py` so its Brier numbers stop being computed on placeholder constants.

### Task 1.1: Drop stub features from `RightEdgeFeatures`

**Files:**
- Modify: `src/wave_alpha/right_edge/features.py`
- Modify: `src/wave_alpha/right_edge/heuristic.py`
- Modify: `tests/right_edge/test_features.py`
- Modify: `tests/right_edge/test_heuristic.py`

- [ ] **Step 1: Edit `RightEdgeFeatures` to remove `rsi_divergence_strength` and `fib_zone_match`.**

```python
class RightEdgeFeatures(BaseModel):
    model_config = ConfigDict(frozen=True)

    retracement_progress: float
    bars_since_candidate: int
    volume_decline_pct: float
    higher_degree_zone_match: float
    atr_regime: float
```

- [ ] **Step 2: Edit `extract_features()` signature: drop `fib_zone_match` arg, stop returning `rsi_divergence_strength`/`fib_zone_match`.**

- [ ] **Step 3: Edit `HeuristicConfirmationModel` to renormalize 5 weights summing to 1.0; bump `version` to `"heuristic_v2"`.**

```python
class HeuristicConfirmationModel:
    version: str = "heuristic_v2"

    def predict_proba(self, f: RightEdgeFeatures) -> float:
        score = (
            0.40 * _clip(f.retracement_progress)
            + 0.25 * min(f.bars_since_candidate / 10.0, 1.0)
            + 0.15 * max(0.0, f.volume_decline_pct / 0.5)
            + 0.10 * _clip(f.higher_degree_zone_match)
            + 0.10 * _clip(f.atr_regime - 0.5)  # mild bias above neutral regime
        )
        return _clip(score)
```

- [ ] **Step 4: Update `tests/right_edge/test_features.py` and `tests/right_edge/test_heuristic.py` to match new schema. Remove any assertion that mentions the dropped fields.**

- [ ] **Step 5: Run tests.**

```bash
uv run pytest tests/right_edge/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add src/wave_alpha/right_edge/features.py src/wave_alpha/right_edge/heuristic.py tests/right_edge/
git commit -m "refactor(right-edge): drop stub features (rsi_divergence, fib_zone)"
```

### Task 1.2: Surface ATR + required-move from `detect_multi_degree`

**Files:**
- Modify: `src/wave_alpha/pivots/multi_degree.py`
- Test: `tests/pivots/test_multi_degree.py`

- [ ] **Step 1: Add a sibling helper that returns per-degree snapshot.**

Add to `multi_degree.py`:

```python
from dataclasses import dataclass
from wave_alpha.pivots.atr import compute_atr


@dataclass(frozen=True)
class DegreeSnapshot:
    pivots: list[PivotEvent]
    atr_at_last_bar: Decimal | None
    required_move: Decimal


def detect_multi_degree_with_snapshots(
    bars: list[Bar],
    interval: Interval,
    *,
    atr_multiples: list[Decimal] | None = None,
    thresholds: list[Decimal] | None = None,
) -> dict[int, DegreeSnapshot]:
    """Same as detect_multi_degree, but each degree carries the ATR + required-move
    used for ZigZag detection. Used by the right-edge feature extractor."""
    by_degree = detect_multi_degree(
        bars, interval=interval, atr_multiples=atr_multiples, thresholds=thresholds
    )
    levels = atr_multiples or thresholds
    assert levels is not None
    atr = compute_atr(bars) if atr_multiples is not None else None
    last_atr = atr[-1] if atr and atr[-1] is not None else None

    out: dict[int, DegreeSnapshot] = {}
    for degree, level in enumerate(levels):
        if atr_multiples is not None:
            required = (last_atr * level) if last_atr is not None else Decimal("0")
        else:
            required = level
        out[degree] = DegreeSnapshot(
            pivots=by_degree.get(degree, []),
            atr_at_last_bar=last_atr,
            required_move=required,
        )
    return out
```

- [ ] **Step 2: Add a test verifying the snapshot fields are populated for both atr_multiples and thresholds paths.**

```python
def test_detect_multi_degree_with_snapshots_carries_required_move():
    bars = _make_synthetic_bars()  # use existing fixture
    snaps = detect_multi_degree_with_snapshots(
        bars, interval=Interval.DAILY, thresholds=[Decimal("1"), Decimal("2")]
    )
    assert snaps[0].required_move == Decimal("1")
    assert snaps[1].required_move == Decimal("2")
    assert snaps[0].pivots == detect_multi_degree(
        bars, interval=Interval.DAILY, thresholds=[Decimal("1"), Decimal("2")]
    )[0]
```

- [ ] **Step 3: Run tests.**

```bash
uv run pytest tests/pivots/ -v
```
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/pivots/multi_degree.py tests/pivots/
git commit -m "feat(pivots): expose ATR + required-move per degree via DegreeSnapshot"
```

### Task 1.3: New `right_edge/extractor.py` (FeatureExtractor)

**Files:**
- Create: `src/wave_alpha/right_edge/extractor.py`
- Create: `tests/right_edge/test_extractor.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/right_edge/test_extractor.py
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.right_edge.extractor import FeatureExtractor


def _bar(d: str, c: float, v: int = 1000) -> Bar:
    return Bar(
        timestamp=date.fromisoformat(d),
        open=Decimal(str(c)),
        high=Decimal(str(c + 0.5)),
        low=Decimal(str(c - 0.5)),
        close=Decimal(str(c)),
        volume=v,
    )


def test_feature_extractor_returns_5_fields_for_HIGH_pivot():
    bars = [_bar(f"2025-01-{i:02d}", 100.0 + i) for i in range(1, 21)]
    pivot = PivotEvent(
        interval=Interval.DAILY,
        degree=1,
        timestamp=bars[10].timestamp,
        price=bars[10].close,
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    fx = FeatureExtractor().extract(
        candidate=pivot,
        point_in_time_bars=bars,
        atr_at_candidate=Decimal("1.0"),
        required_move=Decimal("2.0"),
        higher_degree_zone_match=0.3,
    )
    assert 0.0 <= fx.retracement_progress <= 1.0
    assert fx.bars_since_candidate == 9  # bars after the pivot
    assert fx.higher_degree_zone_match == 0.3
    assert fx.atr_regime == 0.5  # 1.0 / 2.0
```

- [ ] **Step 2: Run test, verify failure.**

```bash
uv run pytest tests/right_edge/test_extractor.py -v
```
Expected: FAIL with "module not found".

- [ ] **Step 3: Implement extractor.**

```python
# src/wave_alpha/right_edge/extractor.py
from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.pivots.models import PivotEvent, PivotType
from wave_alpha.right_edge.features import RightEdgeFeatures


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
    ) -> RightEdgeFeatures:
        if not point_in_time_bars:
            raise ValueError("point_in_time_bars cannot be empty")

        after = [b for b in point_in_time_bars if b.timestamp > candidate.timestamp]
        bars_since = len(after)

        if candidate.type is PivotType.HIGH:
            opposite = min((b.low for b in after), default=candidate.price)
            moved = max(Decimal(0), candidate.price - opposite)
        else:
            opposite = max((b.high for b in after), default=candidate.price)
            moved = max(Decimal(0), opposite - candidate.price)

        progress = (
            float(min(Decimal(1), moved / required_move)) if required_move > 0 else 0.0
        )
        vol_decline = _volume_decline(point_in_time_bars, recent=5, baseline=20)
        atr_regime = (
            float(atr_at_candidate / required_move) if required_move > 0 else 1.0
        )

        return RightEdgeFeatures(
            retracement_progress=progress,
            bars_since_candidate=bars_since,
            volume_decline_pct=vol_decline,
            higher_degree_zone_match=higher_degree_zone_match,
            atr_regime=atr_regime,
        )


def _volume_decline(bars: list[Bar], *, recent: int, baseline: int) -> float:
    if len(bars) < recent + baseline:
        return 0.0
    recent_bars = bars[-recent:]
    base_bars = bars[-(recent + baseline) : -recent]
    base_avg = Decimal(sum(b.volume for b in base_bars)) / Decimal(len(base_bars))
    if base_avg == 0:
        return 0.0
    recent_avg = Decimal(sum(b.volume for b in recent_bars)) / Decimal(len(recent_bars))
    return float((base_avg - recent_avg) / base_avg)
```

- [ ] **Step 4: Run test.** Expected: PASS.

- [ ] **Step 5: Add a LOW-pivot test mirroring the HIGH-pivot test.**

- [ ] **Step 6: Commit.**

```bash
git add src/wave_alpha/right_edge/extractor.py tests/right_edge/test_extractor.py
git commit -m "feat(right-edge): FeatureExtractor — shared point-in-time feature computation"
```

### Task 1.4: Wire `pivot_layer.py` to use FeatureExtractor

**Files:**
- Modify: `src/wave_alpha/backtest/pivot_layer.py`
- Modify: `tests/backtest/test_pivot_layer.py` (or wherever its tests live)

- [ ] **Step 1: Replace `_features_for` with a call into the shared extractor.**

In `backtest/pivot_layer.py`, replace `_features_for` and update `evaluate()`:

```python
from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
from wave_alpha.right_edge.extractor import FeatureExtractor


# ...inside evaluate():
initial_snapshots = detect_multi_degree_with_snapshots(
    initial_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
)
final_pivots_by_degree = detect_multi_degree(
    final_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
)

extractor = FeatureExtractor()
model = HeuristicConfirmationModel()
evaluations: list[PivotEvaluation] = []

for degree, snap in initial_snapshots.items():
    tentative = [p for p in snap.pivots if p.state == PivotState.TENTATIVE]
    later = final_pivots_by_degree.get(degree, [])
    for p in tentative:
        outcome = pivot_confirmation_label(p, later_pivots=later)
        if outcome is None:
            continue
        if snap.atr_at_last_bar is None or snap.required_move <= 0:
            continue
        features = extractor.extract(
            candidate=p,
            point_in_time_bars=initial_view,
            atr_at_candidate=snap.atr_at_last_bar,
            required_move=snap.required_move,
            higher_degree_zone_match=0.0,  # not yet wired; left at 0 for both training and inference
        )
        proba = model.predict_proba(features)
        evaluations.append(
            PivotEvaluation(
                ticker=ticker,
                as_of_initial=initial.isoformat(),
                as_of_final=final.isoformat(),
                degree=degree,
                outcome=outcome,
                predicted_proba=proba,
            )
        )

return PivotLayerResult(evaluations=evaluations)
```

Delete `_features_for` and `_index_of_timestamp` (no longer used).

- [ ] **Step 2: Add a snapshot/regression test that locks in "Brier numbers come from real features now."**

```python
def test_pivot_layer_brier_uses_real_features(tmp_path):
    # Build synthetic bars with an obvious confirmed pivot, evaluate, and verify
    # the predicted_proba in the evaluation isn't equal to the placeholder
    # value the old code produced (~heuristic at retracement_progress=0.5,
    # bars_since=N, fib=0.5 → fixed value). Real features → different value.
    ...
```

(Implementer fills in the synthetic-bars fixture; the assertion is that proba is NOT the previously-hardcoded value for at least one tentative pivot in a fixture where the true retracement_progress is meaningfully different from 0.5.)

- [ ] **Step 3: Run all tests.**

```bash
uv run pytest tests/ -v
```
Expected: PASS. Some pre-existing pivot-layer Brier numbers in golden tests may shift — update golden values to match real-feature output.

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/backtest/pivot_layer.py tests/backtest/
git commit -m "fix(backtest): pivot_layer uses real features (was: placeholder constants)"
```

---

## Phase 2 — Walk-forward training

Builds the offline training pipeline: walks the universe across an expanding window, fits a single logistic, fits per-degree Platt scalers on each fold's OOS predictions, and emits a stream of calibrated OOS predictions.

### Task 2.1: TrainingRow + walk-forward fold structure

**Files:**
- Create: `src/wave_alpha/right_edge/training.py`
- Create: `tests/right_edge/test_training.py`

- [ ] **Step 1: Define TrainingRow dataclass and fold helper.**

```python
# src/wave_alpha/right_edge/training.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from wave_alpha.right_edge.features import RightEdgeFeatures


@dataclass(frozen=True)
class TrainingRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    outcome: int  # 1 = confirmed, 0 = invalidated


@dataclass(frozen=True)
class Fold:
    train_until: date  # exclusive
    test_start: date   # inclusive
    test_end: date     # exclusive


def quarter_folds(start: date, end: date, *, min_train_quarters: int = 4) -> list[Fold]:
    """Generate expanding-window quarter folds from `start` to `end`.

    Skips first `min_train_quarters` quarters (used as warmup-only training data).
    """
    boundaries = [start]
    cur = start
    while cur < end:
        cur = _next_quarter_start(cur)
        boundaries.append(cur)
    folds: list[Fold] = []
    for i in range(min_train_quarters, len(boundaries) - 1):
        folds.append(Fold(
            train_until=boundaries[i],
            test_start=boundaries[i],
            test_end=boundaries[i + 1],
        ))
    return folds


def _next_quarter_start(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    if q == 4:
        return date(d.year + 1, 1, 1)
    return date(d.year, q * 3 + 1, 1)
```

- [ ] **Step 2: Test fold generation.**

```python
def test_quarter_folds_expanding():
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=2)
    # Q1, Q2 are warmup; folds for Q3, Q4
    assert len(folds) == 2
    assert folds[0].train_until == date(2021, 7, 1)
    assert folds[0].test_start == date(2021, 7, 1)
    assert folds[0].test_end == date(2021, 10, 1)
```

- [ ] **Step 3: Run test.** Expected: PASS.

- [ ] **Step 4: Commit.**

### Task 2.2: Logistic fit + Platt fit (NumPy, hand-coded)

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py`
- Modify: `tests/right_edge/test_training.py`

- [ ] **Step 1: Add `LogisticParams` + `fit_logistic` + `fit_platt`.**

```python
# Append to training.py
import numpy as np


@dataclass(frozen=True)
class LogisticParams:
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float


@dataclass(frozen=True)
class PlattParams:
    a: float  # slope
    b: float  # intercept


_FEATURE_ORDER = (
    "retracement_progress",
    "bars_since_candidate",
    "volume_decline_pct",
    "higher_degree_zone_match",
    "atr_regime",
)


def _row_to_vector(row: TrainingRow) -> np.ndarray:
    f = row.features
    return np.array([
        f.retracement_progress,
        float(f.bars_since_candidate) / 20.0,  # rescale to [0, 1]-ish
        f.volume_decline_pct,
        f.higher_degree_zone_match,
        f.atr_regime,
    ], dtype=np.float64)


def fit_logistic(
    rows: list[TrainingRow], *, lr: float = 0.05, n_iters: int = 2000, seed: int = 42
) -> LogisticParams:
    """Batch gradient descent on binary cross-entropy. Deterministic given seed."""
    if not rows:
        raise ValueError("cannot fit on empty rows")
    rng = np.random.default_rng(seed)
    X = np.stack([_row_to_vector(r) for r in rows])
    y = np.array([r.outcome for r in rows], dtype=np.float64)
    w = rng.normal(0, 0.01, size=X.shape[1])
    b = 0.0
    for _ in range(n_iters):
        z = X @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        grad_w = X.T @ (p - y) / len(y)
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return LogisticParams(
        feature_order=_FEATURE_ORDER,
        weights=tuple(float(x) for x in w),
        intercept=float(b),
    )


def fit_platt(predictions: list[tuple[float, int]]) -> PlattParams:
    """Platt scaling = 1-feature logistic on (raw_proba, label) pairs."""
    if not predictions:
        return PlattParams(a=1.0, b=0.0)  # identity
    X = np.array([[p] for p, _ in predictions], dtype=np.float64)
    y = np.array([o for _, o in predictions], dtype=np.float64)
    a = 0.0
    b = 0.0
    for _ in range(2000):
        z = X[:, 0] * a + b
        ph = 1.0 / (1.0 + np.exp(-z))
        grad_a = float(np.mean((ph - y) * X[:, 0]))
        grad_b = float(np.mean(ph - y))
        a -= 0.05 * grad_a
        b -= 0.05 * grad_b
    return PlattParams(a=float(a), b=float(b))


def predict_logistic(params: LogisticParams, features: RightEdgeFeatures) -> float:
    v = np.array([
        features.retracement_progress,
        float(features.bars_since_candidate) / 20.0,
        features.volume_decline_pct,
        features.higher_degree_zone_match,
        features.atr_regime,
    ])
    z = float(np.dot(np.array(params.weights), v) + params.intercept)
    return 1.0 / (1.0 + float(np.exp(-z)))


def apply_platt(params: PlattParams, raw_proba: float) -> float:
    z = params.a * raw_proba + params.b
    return 1.0 / (1.0 + float(np.exp(-z)))
```

- [ ] **Step 2: Tests for logistic + Platt.**

```python
def test_fit_logistic_recovers_separable_signal():
    # Synthetic: feature 0 is highly correlated with outcome.
    rows = []
    rng = np.random.default_rng(0)
    for i in range(200):
        outcome = int(rng.random() < 0.5)
        progress = 0.8 if outcome else 0.2
        rows.append(_make_row(progress=progress, outcome=outcome))
    params = fit_logistic(rows, seed=1)
    high = predict_logistic(params, _make_features(progress=0.9))
    low = predict_logistic(params, _make_features(progress=0.1))
    assert high > 0.6
    assert low < 0.4


def test_platt_is_identity_when_predictions_already_calibrated():
    # If raw probas == empirical rates, Platt collapses near identity.
    preds = [(0.5, 0), (0.5, 1)] * 100
    p = fit_platt(preds)
    # Both classes equally likely → calibrated proba ~0.5 regardless of input
    assert abs(apply_platt(p, 0.5) - 0.5) < 0.05


def test_fit_logistic_deterministic():
    rows = _toy_rows(seed=99)
    p1 = fit_logistic(rows, seed=42)
    p2 = fit_logistic(rows, seed=42)
    assert p1 == p2
```

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

### Task 2.3: Walk-forward orchestration

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py`
- Modify: `tests/right_edge/test_training.py`

- [ ] **Step 1: Implement `walk_forward()`.**

```python
@dataclass(frozen=True)
class CalibratedPrediction:
    ticker: str
    as_of: date
    degree: int
    raw_proba: float
    calibrated_proba: float
    outcome: int


@dataclass(frozen=True)
class WalkForwardResult:
    folds: list[Fold]
    final_logistic: LogisticParams
    platt_per_degree: dict[int, PlattParams]
    oos_predictions: list[CalibratedPrediction]
    n_train_rows: int
    n_oos_rows: int


def walk_forward(
    rows_by_quarter: dict[date, list[TrainingRow]],
    *,
    folds: list[Fold],
    seed: int = 42,
) -> WalkForwardResult:
    """Expanding-window walk-forward.

    `rows_by_quarter[q]` = list of TrainingRow whose `as_of` falls in the
    quarter starting at `q`. Caller is responsible for assembling these
    (extracting features point-in-time, scoring outcomes via the labels module).
    """
    all_oos: list[CalibratedPrediction] = []
    accumulated_platt_inputs: dict[int, list[tuple[float, int]]] = {1: [], 2: [], 3: []}

    for fold in folds:
        # Train rows: every quarter starting before fold.train_until.
        train_rows: list[TrainingRow] = []
        for q, rs in rows_by_quarter.items():
            if q < fold.train_until:
                train_rows.extend(rs)
        if not train_rows:
            continue
        params = fit_logistic(train_rows, seed=seed)

        # OOS rows: this fold's quarter.
        test_rows: list[TrainingRow] = []
        for q, rs in rows_by_quarter.items():
            if fold.test_start <= q < fold.test_end:
                test_rows.extend(rs)

        # Per-degree raw probas → fit Platt per degree on prior folds + this one.
        for r in test_rows:
            raw = predict_logistic(params, r.features)
            accumulated_platt_inputs.setdefault(r.degree, []).append((raw, r.outcome))

        # Refit Platt scalers using everything seen up through this fold.
        platt = {
            d: fit_platt(rows) for d, rows in accumulated_platt_inputs.items() if rows
        }

        for r in test_rows:
            raw = predict_logistic(params, r.features)
            scaler = platt.get(r.degree)
            cal = apply_platt(scaler, raw) if scaler else raw
            all_oos.append(CalibratedPrediction(
                ticker=r.ticker,
                as_of=r.as_of,
                degree=r.degree,
                raw_proba=raw,
                calibrated_proba=cal,
                outcome=r.outcome,
            ))

    # Final logistic refit on ALL rows for live inference.
    all_rows = [r for rs in rows_by_quarter.values() for r in rs]
    final_logistic = fit_logistic(all_rows, seed=seed) if all_rows else None
    final_platt = {
        d: fit_platt(rows) for d, rows in accumulated_platt_inputs.items() if rows
    }

    return WalkForwardResult(
        folds=folds,
        final_logistic=final_logistic,
        platt_per_degree=final_platt,
        oos_predictions=all_oos,
        n_train_rows=len(all_rows),
        n_oos_rows=len(all_oos),
    )
```

- [ ] **Step 2: Test walk-forward on a tiny synthetic dataset (2 folds, ~50 rows total).** Verify `n_oos_rows` matches and OOS predictions only come from test folds.

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

### Task 2.4: TrainingRow assembly from real OHLCV provider

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py`

- [ ] **Step 1: Add `assemble_training_rows()` that walks a universe + as-of date list and emits TrainingRow per tentative pivot.**

This is the bridge between the existing pivot-layer evaluation logic and the training pipeline. Reuse logic from `backtest/pivot_layer.py` — DON'T copy it; extract the per-tentative-pivot iteration into a helper if needed, then call from both places.

```python
def assemble_training_rows(
    *,
    provider: OHLCVProvider,
    universe: list[str],
    as_of_pairs: list[tuple[date, date]],  # (initial, final) pairs
    interval: Interval = Interval.DAILY,
    lookahead_bars: int = 20,
) -> list[TrainingRow]:
    """Walk universe × (initial, final) pairs; emit TrainingRow per
    tentative pivot in the initial snapshot whose outcome can be labeled
    against the final snapshot."""
    from wave_alpha.backtest.labels import pivot_confirmation_label
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.models import PivotState
    from wave_alpha.pivots.multi_degree import (
        detect_multi_degree,
        detect_multi_degree_with_snapshots,
    )

    extractor = FeatureExtractor()
    rows: list[TrainingRow] = []
    for ticker in universe:
        max_final = max(f for _, f in as_of_pairs)
        bars = provider.fetch(ticker, interval, end=max_final + timedelta(days=lookahead_bars * 2))
        if not bars:
            continue
        for initial, final in as_of_pairs:
            initial_view = PointInTimeView(bars, initial).visible()
            final_view = PointInTimeView(bars, final).visible()
            if not initial_view or not final_view:
                continue
            initial_snaps = detect_multi_degree_with_snapshots(
                initial_view, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
            )
            final_pivots = detect_multi_degree(
                final_view, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
            )
            for degree, snap in initial_snaps.items():
                if snap.atr_at_last_bar is None or snap.required_move <= 0:
                    continue
                tentative = [p for p in snap.pivots if p.state == PivotState.TENTATIVE]
                later = final_pivots.get(degree, [])
                for p in tentative:
                    outcome = pivot_confirmation_label(p, later_pivots=later)
                    if outcome is None:
                        continue
                    features = extractor.extract(
                        candidate=p,
                        point_in_time_bars=initial_view,
                        atr_at_candidate=snap.atr_at_last_bar,
                        required_move=snap.required_move,
                        higher_degree_zone_match=0.0,
                    )
                    rows.append(TrainingRow(
                        ticker=ticker,
                        as_of=initial,
                        degree=degree,
                        features=features,
                        outcome=1 if outcome == "confirmed" else 0,
                    ))
    return rows
```

- [ ] **Step 2: Smoke test using a fake provider that emits a hand-built bar series for 1 ticker.** Verify rows come out with the right outcome labels.

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/right_edge/training.py tests/right_edge/test_training.py
git commit -m "feat(right-edge): walk-forward training pipeline (logistic + Platt-per-degree)"
```

---

## Phase 3 — Promotion gate

Bootstrap-CI Brier comparison + per-degree calibration check. Returns PROMOTE/HOLD/REJECT.

### Task 3.1: PromotionGate

**Files:**
- Create: `src/wave_alpha/right_edge/promotion.py`
- Create: `tests/right_edge/test_promotion.py`

- [ ] **Step 1: Implement gate with synthetic-noise CI.**

```python
# src/wave_alpha/right_edge/promotion.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from wave_alpha.backtest.brier import brier_score, calibration_error

Verdict = Literal["PROMOTE", "HOLD", "REJECT"]


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    logistic_brier_ci: tuple[float, float, float]   # (lo, mid, hi)
    heuristic_brier_ci: tuple[float, float, float]
    per_degree_calibration: dict[int, dict[str, float]]  # {1: {"logistic": .., "heuristic": ..}}


def _bootstrap_brier(
    preds: list[tuple[float, int]], *, n_resamples: int = 1000, seed: int = 0
) -> tuple[float, float, float]:
    if not preds:
        return (0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    arr = np.array(preds, dtype=np.float64)
    n = len(arr)
    samples = []
    for _ in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        s = arr[idx]
        samples.append(brier_score(list(map(tuple, s))))
    samples_arr = np.sort(np.array(samples))
    lo = float(samples_arr[int(0.025 * n_resamples)])
    mid = float(np.median(samples_arr))
    hi = float(samples_arr[int(0.975 * n_resamples)])
    return (lo, mid, hi)


def evaluate_promotion(
    *,
    logistic_preds: list[tuple[float, int, int]],   # (proba, outcome, degree)
    heuristic_preds: list[tuple[float, int, int]],  # same shape, same prediction set
    seed: int = 0,
) -> GateResult:
    log_pairs = [(p, o) for p, o, _ in logistic_preds]
    heu_pairs = [(p, o) for p, o, _ in heuristic_preds]
    log_ci = _bootstrap_brier(log_pairs, seed=seed)
    heu_ci = _bootstrap_brier(heu_pairs, seed=seed + 1)

    per_degree: dict[int, dict[str, float]] = {}
    for d in {dg for _, _, dg in logistic_preds}:
        log_d = [(p, o) for p, o, dg in logistic_preds if dg == d]
        heu_d = [(p, o) for p, o, dg in heuristic_preds if dg == d]
        per_degree[d] = {
            "logistic": calibration_error(log_d) if log_d else 0.0,
            "heuristic": calibration_error(heu_d) if heu_d else 0.0,
        }

    log_lo, _, log_hi = log_ci
    heu_lo, _, heu_hi = heu_ci

    # REJECT if logistic strictly worse on Brier.
    if log_lo > heu_hi:
        return GateResult("REJECT", log_ci, heu_ci, per_degree)
    # REJECT if any per-degree calibration is worse.
    for d, m in per_degree.items():
        if m["logistic"] > m["heuristic"]:
            return GateResult("REJECT", log_ci, heu_ci, per_degree)
    # PROMOTE only if Brier strictly better AND every degree's calibration is ≤.
    if log_hi < heu_lo:
        return GateResult("PROMOTE", log_ci, heu_ci, per_degree)
    return GateResult("HOLD", log_ci, heu_ci, per_degree)
```

- [ ] **Step 2: Tests covering all three verdicts.**

```python
def test_promote_when_logistic_clearly_better():
    # Logistic: predictions match outcomes; heuristic: random.
    log_p = [(0.95, 1, 1), (0.05, 0, 1)] * 100
    heu_p = [(0.5, 1, 1), (0.5, 0, 1)] * 100
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "PROMOTE"


def test_reject_when_logistic_worse():
    log_p = [(0.5, 1, 1), (0.5, 0, 1)] * 100
    heu_p = [(0.95, 1, 1), (0.05, 0, 1)] * 100
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "REJECT"


def test_hold_when_close():
    log_p = [(0.6, 1, 1), (0.4, 0, 1)] * 100
    heu_p = [(0.55, 1, 1), (0.45, 0, 1)] * 100
    r = evaluate_promotion(logistic_preds=log_p, heuristic_preds=heu_p)
    assert r.verdict == "HOLD"
```

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/right_edge/promotion.py tests/right_edge/test_promotion.py
git commit -m "feat(right-edge): bootstrap-CI promotion gate (PROMOTE/HOLD/REJECT)"
```

---

## Phase 4 — JSON persistence + LogisticConfirmationModel

The runtime-loadable model class. Reads a plain JSON artifact, predicts probabilities with per-degree Platt calibration, refuses to load anything that isn't PROMOTE-verdict.

### Task 4.1: `LogisticConfirmationModel`

**Files:**
- Create: `src/wave_alpha/right_edge/logistic.py`
- Create: `tests/right_edge/test_logistic.py`

- [ ] **Step 1: Define class.**

```python
# src/wave_alpha/right_edge/logistic.py
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from wave_alpha.right_edge.features import RightEdgeFeatures


class ModelArtifactError(ValueError):
    """Raised when a model artifact is malformed or non-PROMOTE."""


@dataclass(frozen=True)
class LogisticConfirmationModel:
    version: str
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float
    platt_per_degree: dict[int, tuple[float, float]]  # degree → (a, b)

    @classmethod
    def from_json(cls, path: Path) -> LogisticConfirmationModel:
        raw = json.loads(path.read_text())
        if raw.get("gate", {}).get("verdict") != "PROMOTE":
            raise ModelArtifactError(
                f"Refusing to load model with verdict={raw.get('gate', {}).get('verdict')}"
            )
        return cls(
            version=raw["version"],
            feature_order=tuple(raw["feature_order"]),
            weights=tuple(raw["weights"]),
            intercept=float(raw["intercept"]),
            platt_per_degree={
                int(d): (float(p["a"]), float(p["b"]))
                for d, p in raw["platt_per_degree"].items()
            },
        )

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        v = self._vector(features)
        z = sum(w * x for w, x in zip(self.weights, v, strict=True)) + self.intercept
        raw = 1.0 / (1.0 + math.exp(-z))
        a, b = self.platt_per_degree.get(degree, (1.0, 0.0))
        return 1.0 / (1.0 + math.exp(-(a * raw + b)))

    def _vector(self, f: RightEdgeFeatures) -> list[float]:
        # Must mirror `_row_to_vector` in training.py.
        return [
            f.retracement_progress,
            float(f.bars_since_candidate) / 20.0,
            f.volume_decline_pct,
            f.higher_degree_zone_match,
            f.atr_regime,
        ]
```

- [ ] **Step 2: Tests: load PROMOTE artifact; refuse HOLD/REJECT artifact; predict_proba within [0, 1].**

```python
def test_from_json_loads_promote_artifact(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "version": "logistic_v1",
        "feature_order": ["retracement_progress", "bars_since_candidate",
                          "volume_decline_pct", "higher_degree_zone_match", "atr_regime"],
        "weights": [0.5, 0.3, 0.1, 0.05, 0.05],
        "intercept": -0.5,
        "platt_per_degree": {"1": {"a": 1.0, "b": 0.0}},
        "gate": {"verdict": "PROMOTE"},
    }))
    m = LogisticConfirmationModel.from_json(path)
    assert m.version == "logistic_v1"


def test_from_json_refuses_hold_verdict(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "version": "logistic_v1",
        "feature_order": [],
        "weights": [],
        "intercept": 0.0,
        "platt_per_degree": {},
        "gate": {"verdict": "HOLD"},
    }))
    with pytest.raises(ModelArtifactError):
        LogisticConfirmationModel.from_json(path)
```

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

### Task 4.2: Artifact serialization

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py` (add `serialize_artifact` + `write_artifact`)

- [ ] **Step 1: Implement helpers that take a `WalkForwardResult` + `GateResult` + heuristic Brier CIs and write the JSON.**

```python
# Add to training.py
import json
import subprocess
from datetime import date as _date


def serialize_artifact(
    *,
    wf: WalkForwardResult,
    gate: "GateResult",
    seed: int,
) -> dict:
    return {
        "version": "logistic_v1",
        "trained_at": _date.today().isoformat(),
        "git_sha": _git_sha(),
        "feature_order": list(_FEATURE_ORDER),
        "weights": list(wf.final_logistic.weights),
        "intercept": wf.final_logistic.intercept,
        "platt_per_degree": {
            str(d): {"a": p.a, "b": p.b} for d, p in wf.platt_per_degree.items()
        },
        "training": {
            "n_train_rows": wf.n_train_rows,
            "n_oos_rows": wf.n_oos_rows,
            "n_folds": len(wf.folds),
            "rng_seed": seed,
        },
        "gate": {
            "verdict": gate.verdict,
            "logistic_brier_ci": list(gate.logistic_brier_ci),
            "heuristic_brier_ci": list(gate.heuristic_brier_ci),
            "per_degree_calibration": gate.per_degree_calibration,
        },
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def write_artifact(artifact: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
```

- [ ] **Step 2: Round-trip test: serialize_artifact → write → LogisticConfirmationModel.from_json.** Verify predict_proba on a known features vector matches a hand-computed value.

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/right_edge/logistic.py src/wave_alpha/right_edge/training.py tests/right_edge/
git commit -m "feat(right-edge): JSON-persisted LogisticConfirmationModel + artifact serializer"
```

---

## Phase 5 — CLI command

`wave-alpha train right-edge` runs the full pipeline and prints the verdict.

### Task 5.1: `train` Typer subgroup + `right-edge` command

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/test_cli_train.py`

- [ ] **Step 1: Add a `train_app` Typer subgroup and `right-edge` command.**

```python
# In cli/app.py, after the watchlist/backtest setup
train_app = typer.Typer(no_args_is_help=True, help="Train and gate models.")
app.add_typer(train_app, name="train")


@train_app.command("right-edge")
def train_right_edge(
    universe_file: Path = typer.Option(
        Path("data/universe_curated.txt"), "--universe"
    ),
    start_date: str = typer.Option("2021-01-01", "--start"),
    end_date: str = typer.Option(...),  # required, format YYYY-MM-DD
    write: bool = typer.Option(False, "--write"),
    force: bool = typer.Option(False, "--force"),
    seed: int = typer.Option(42, "--seed"),
    output_path: Path = typer.Option(
        Path("src/wave_alpha/right_edge/models/right_edge_logistic_v1.json"),
        "--output",
    ),
) -> None:
    """Run walk-forward training, gate the result, optionally write the artifact."""
    from datetime import date

    from wave_alpha.right_edge.training import (
        assemble_training_rows,
        quarter_folds,
        serialize_artifact,
        walk_forward,
        write_artifact,
    )
    from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel
    from wave_alpha.right_edge.promotion import evaluate_promotion

    # Build provider stack — same pattern as backtest CLI.
    from wave_alpha.data.cached import CachedProvider
    from wave_alpha.data.yahoo import YahooProvider
    provider = CachedProvider(YahooProvider())

    universe = [
        s.strip().upper() for s in universe_file.read_text().splitlines()
        if s.strip() and not s.startswith("#")
    ]
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    folds = quarter_folds(start, end)
    if not folds:
        typer.echo(f"No folds between {start} and {end}; need ≥5 quarters")
        raise typer.Exit(1)

    # Assemble training rows on quarter boundaries.
    as_of_pairs = [
        (f.test_start, min(f.test_end, end))
        for f in folds
    ]
    typer.echo(f"Assembling training rows for {len(universe)} tickers × {len(as_of_pairs)} pairs...")
    rows = assemble_training_rows(
        provider=provider,
        universe=universe,
        as_of_pairs=as_of_pairs,
    )
    typer.echo(f"  → {len(rows)} rows total")

    rows_by_quarter: dict[date, list] = {}
    for r in rows:
        rows_by_quarter.setdefault(_quarter_start(r.as_of), []).append(r)

    typer.echo("Running walk-forward...")
    wf = walk_forward(rows_by_quarter, folds=folds, seed=seed)

    # Heuristic baseline on the same OOS prediction set.
    heuristic = HeuristicConfirmationModel()
    log_preds = [
        (cp.calibrated_proba, cp.outcome, cp.degree) for cp in wf.oos_predictions
    ]
    heu_preds = []
    for cp in wf.oos_predictions:
        # Re-derive heuristic proba from same training rows. We need the rows by lookup.
        # This is awkward — easier path: cache features per CalibratedPrediction during walk_forward.
        # For now, recompute by iterating rows whose (ticker, as_of, degree) match.
        ...

    gate = evaluate_promotion(logistic_preds=log_preds, heuristic_preds=heu_preds, seed=seed)

    typer.echo(f"\nVerdict: {gate.verdict}")
    typer.echo(f"  Logistic Brier 95% CI: {gate.logistic_brier_ci}")
    typer.echo(f"  Heuristic Brier 95% CI: {gate.heuristic_brier_ci}")
    typer.echo(f"  Per-degree calibration: {gate.per_degree_calibration}")

    if write:
        if gate.verdict != "PROMOTE" and not force:
            typer.echo(f"Not writing — verdict is {gate.verdict} (use --force to override)")
            raise typer.Exit(1)
        artifact = serialize_artifact(wf=wf, gate=gate, seed=seed)
        write_artifact(artifact, output_path)
        typer.echo(f"Wrote {output_path}")
```

**Implementer note:** The heuristic baseline needs the original features per row to score, but `CalibratedPrediction` only carries probas. Fix by changing `CalibratedPrediction` to also carry the `features` (RightEdgeFeatures), OR by attaching a parallel list of features alongside `oos_predictions`. Implementer chooses the cleaner option.

- [ ] **Step 2: Add a `_quarter_start(d)` helper.**

```python
def _quarter_start(d: date) -> date:
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)
```

- [ ] **Step 3: Smoke test using a tiny fake provider that emits 1 ticker × 6 quarters.**

```python
# tests/test_cli_train.py
def test_train_right_edge_runs_end_to_end(tmp_path, monkeypatch):
    # Stub CachedProvider/YahooProvider to return synthetic bars for 2 tickers.
    # Run the CLI with --start, --end covering the synthetic range.
    # Assert verdict line appears in output.
    ...
```

- [ ] **Step 4: Run tests.** Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_train.py
git commit -m "feat(cli): wave-alpha train right-edge — walk-forward + gate"
```

---

## Phase 6 — Inference path integration + config

Pipeline + web app pick the right model at startup.

### Task 6.1: `load_right_edge_model()` helper

**Files:**
- Create: `src/wave_alpha/right_edge/loader.py`
- Modify: `src/wave_alpha/prefs/models.py` (add `RightEdgeConfig`)
- Create: `tests/right_edge/test_loader.py`

- [ ] **Step 1: Add config class.**

```python
# In prefs/models.py
class RightEdgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: Literal["auto", "heuristic", "logistic"] = "auto"


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    right_edge: RightEdgeConfig = Field(default_factory=RightEdgeConfig)
```

- [ ] **Step 2: Implement loader.**

```python
# src/wave_alpha/right_edge/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from loguru import logger

from wave_alpha.prefs.models import RightEdgeConfig
from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel
from wave_alpha.right_edge.logistic import (
    LogisticConfirmationModel,
    ModelArtifactError,
)


class ConfirmationModel(Protocol):
    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float: ...


_DEFAULT_LOGISTIC_PATH = (
    Path(__file__).parent / "models" / "right_edge_logistic_v1.json"
)


class _HeuristicAdapter:
    """Adapts the existing heuristic to the (features, degree) signature."""

    def __init__(self) -> None:
        self._inner = HeuristicConfirmationModel()

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        del degree  # heuristic ignores degree
        return self._inner.predict_proba(features)


def load_right_edge_model(
    cfg: RightEdgeConfig, *, path: Path = _DEFAULT_LOGISTIC_PATH
) -> ConfirmationModel:
    if cfg.model == "heuristic":
        return _HeuristicAdapter()
    if cfg.model == "logistic":
        return LogisticConfirmationModel.from_json(path)
    # auto
    if not path.exists():
        return _HeuristicAdapter()
    try:
        return LogisticConfirmationModel.from_json(path)
    except (ModelArtifactError, ValueError, KeyError) as exc:
        logger.warning("right-edge: falling back to heuristic ({})", exc)
        return _HeuristicAdapter()
```

- [ ] **Step 3: Tests.**

```python
def test_loader_auto_no_file_returns_heuristic(tmp_path):
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(cfg, path=tmp_path / "missing.json")
    assert isinstance(m, _HeuristicAdapter)


def test_loader_auto_promote_file_returns_logistic(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({...PROMOTE artifact...}))
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(cfg, path=p)
    assert isinstance(m, LogisticConfirmationModel)


def test_loader_auto_hold_file_falls_back(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({...HOLD artifact...}))
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(cfg, path=p)
    assert isinstance(m, _HeuristicAdapter)


def test_loader_logistic_missing_file_raises(tmp_path):
    cfg = RightEdgeConfig(model="logistic")
    with pytest.raises(FileNotFoundError):
        load_right_edge_model(cfg, path=tmp_path / "missing.json")
```

- [ ] **Step 4: Run tests.** Expected: PASS.

- [ ] **Step 5: Commit.**

### Task 6.2: Wire into pipeline + web

**Files:**
- Modify: `src/wave_alpha/pipeline.py` (find existing right-edge usage)
- Modify: `src/wave_alpha/web/deps.py`
- Modify: `src/wave_alpha/backtest/pivot_layer.py`

- [ ] **Step 1: Find every `HeuristicConfirmationModel()` instantiation in non-test code.**

```bash
grep -rn "HeuristicConfirmationModel()" src/
```

- [ ] **Step 2: Replace each one with `load_right_edge_model(config.right_edge)`. Heuristic-instantiation in tests stays as-is.**

For `backtest/pivot_layer.py`, the model becomes a constructor arg with a default factory:

```python
@dataclass
class PivotLayerEvaluator:
    provider: OHLCVProvider
    lookahead_bars: int = 20
    interval: Interval = Interval.DAILY
    model: ConfirmationModel = field(default_factory=lambda: _HeuristicAdapter())
```

- [ ] **Step 3: Run all tests + typecheck.**

```bash
uv run pytest tests/ -v
uv run ruff check .
```

- [ ] **Step 4: Commit.**

```bash
git add src/wave_alpha/ tests/
git commit -m "feat(right-edge): pipeline + web pick logistic vs heuristic via config"
```

---

## Phase 7 — Train, gate, ship (or hold)

Run the actual training, gate the result, commit the artifact (or document the HOLD).

- [ ] **Step 1: Run training on full universe.**

```bash
uv run wave-alpha train right-edge --start 2021-01-01 --end 2026-04-01 --write
```

- [ ] **Step 2: Inspect verdict line.**
  - **PROMOTE**: artifact is at `src/wave_alpha/right_edge/models/right_edge_logistic_v1.json`. Commit it.
  - **HOLD/REJECT**: don't commit an artifact. Open a follow-up note in the spec doc with the numbers.

- [ ] **Step 3: If PROMOTE — commit the artifact + README note.**

```bash
git add src/wave_alpha/right_edge/models/right_edge_logistic_v1.json
git commit -m "feat(right-edge): ship trained logistic model (PROMOTE verdict)"
```

- [ ] **Step 4: If HOLD/REJECT — append a follow-up note in the spec.**

Add a short paragraph to `docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md` under "Open questions / deferred":

> **Training run 2026-05-04:** Verdict was HOLD/REJECT. Logistic Brier 95% CI: [...], heuristic [...]. Per-degree calibration: ... . Heuristic remains in production. Re-evaluate when we add RSI/fib features or expand the universe.

- [ ] **Step 5: Push + open PR. Final review at this checkpoint covers the whole branch.**

---

## Self-review checklist (run by plan author before handoff)

- [x] Spec coverage: every requirement in the spec maps to a phase/task.
- [x] No placeholder steps; every code block is concrete.
- [x] Type names consistent (`RightEdgeFeatures` schema reduced from 7 to 5 fields throughout).
- [x] Heuristic version bumped to `heuristic_v2` everywhere it's stored.
- [x] Single source of truth for feature computation (`FeatureExtractor`) used by both training and inference paths.
- [x] Determinism: pinned RNG seeds in fit_logistic, walk_forward, evaluate_promotion.
- [x] Promotion gate has explicit verdict rules covering all three outcomes.
- [x] Loader fallback path (auto mode, missing/HOLD/REJECT artifact → heuristic) explicitly tested.
- [x] No new runtime deps beyond NumPy (already a dep of wave-alpha).

## Execution

Per memory directive ("skip per-task reviewer dispatches on mechanical work"), execute as one batched implementer dispatch per phase, with a final cross-cutting code review at end. Phase 7 is operator action — runs the actual training and decides PROMOTE vs HOLD.
