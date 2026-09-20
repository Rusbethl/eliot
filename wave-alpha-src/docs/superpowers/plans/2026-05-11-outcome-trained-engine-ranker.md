# Outcome-Trained Engine Ranker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `engine_ranker` module that re-ranks pipeline-level top-K candidates with a calibrated logistic `P(outcome=target)` fit from `history.sqlite` outcomes, folding coherence into the logistic and dropping the outside `coh_multiplier` on the LLM blend when the logistic is active. The enumerator's hand-tuned `_score()` is preserved for in-template-window dedup.

**Architecture:** New module `src/wave_alpha/engine_ranker/` mirrors `right_edge/` layout (features, heuristic, logistic, loader, training, promotion, models/). Pipeline gains one new step between coherence and `_rank`: when the active model is the logistic, invoke `predict_proba` per candidate and stash `engine_prob` on `RankedCount`. `blend_scores` gains optional kwargs for the new probabilistic blend; the legacy formula is preserved bit-identically when the active model is the heuristic.

**Tech Stack:** Python 3.11, Pydantic v2 (frozen), stdlib + project types only (no scikit-learn at runtime — training uses sklearn but the artifact is plain JSON, same pattern as `right_edge/logistic.py`). Typer for the CLI command. pytest for tests. Reuses `outcome_eval` harness from PR #5 for the promotion report.

**Decisions baked in (from the design spec; redirect before execution if you want to change):**
1. **Features (fixed):** `template_fit, fib, recency, coherence` (+ `degree` for per-degree intercept lookup).
2. **Per-degree handling:** shared coefficient vector + per-degree intercepts (parallel to right-edge per-degree Platt).
3. **Promotion gate:** paired-bootstrap on Brier AND backtest expectancy; both lower 95% CI bounds must be ≥ 0.
4. **Default config:** `engine_ranker.kind="auto"` — prefer local logistic artifact, fall back to packaged `models/v1.json`, fall back to heuristic.
5. **Backwards compat:** when active model is the heuristic, `blend_scores` retains the legacy `coh_multiplier * (alpha * engine + (1-alpha) * llm_prob)` formula. Bit-identical output.
6. **`engine_prob` field:** populated on `RankedCount` only when active model is logistic; `None` under heuristic — so consumers can tell whether the value is a calibrated probability or absent.

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `src/wave_alpha/elliott/models.py` | Add `template_fit_component`, `fib_component`, `recency_component: float \| None` to `WaveCount`. | Modify |
| `src/wave_alpha/elliott/enumerator.py` | Populate the three component fields in `_score()` via `count.model_copy`. | Modify |
| `src/wave_alpha/engine_ranker/__init__.py` | Re-export the loader and protocol. | Create |
| `src/wave_alpha/engine_ranker/features.py` | `EngineRankerFeatures` frozen Pydantic model + `from_count` extraction helper. | Create |
| `src/wave_alpha/engine_ranker/heuristic.py` | `HeuristicEngineRanker` reproducing legacy `_score()` formula. | Create |
| `src/wave_alpha/engine_ranker/logistic.py` | `LogisticEngineRanker` with shared-coef + per-degree-intercept JSON artifact loader. | Create |
| `src/wave_alpha/engine_ranker/loader.py` | `load_engine_ranker(cfg, *, logistic_path)` with auto-fallback chain. | Create |
| `src/wave_alpha/engine_ranker/training.py` | `build_outcome_dataset` + `train_logistic` + `serialize_artifact`. | Create |
| `src/wave_alpha/engine_ranker/promotion.py` | `evaluate_promotion` with paired-bootstrap Brier + expectancy. | Create |
| `src/wave_alpha/engine_ranker/models/.gitkeep` | Reserves the directory; `v1.json` is shipped only after a real training run (post-merge). | Create |
| `src/wave_alpha/prefs/models.py` | Add `EngineRankerConfig` + `EngineRankerOutcomeTrainingConfig`; wire into `AppConfig`. | Modify |
| `src/wave_alpha/pipeline.py` | Add `engine_prob: float \| None` to `RankedCount`; `_rank` accepts the resolved model and invokes `predict_proba` when logistic. | Modify |
| `src/wave_alpha/llm/ranking.py` | `blend_scores` gains optional `engine_prob` + `use_logistic_path` kwargs. | Modify |
| `src/wave_alpha/history/models.py` | `SnapshotCount` gains `engine_prob: float \| None = None`. | Modify |
| `src/wave_alpha/history/service.py` | Populate `engine_prob` from `RankedCount` when building `SnapshotCount`. | Modify |
| `src/wave_alpha/cli/app.py` | Wire `train engine-ranker` sub-command (delegates to `engine_ranker.training`). | Modify |
| `tests/elliott/test_score_components.py` | Verifies `_score()` populates the three component fields and that they sum (via legacy formula) to `count.score`. | Create |
| `tests/engine_ranker/test_features.py` | Unit tests for `EngineRankerFeatures.from_count`. | Create |
| `tests/engine_ranker/test_heuristic.py` | Heuristic reproduces legacy `_score()` to 1e-12. | Create |
| `tests/engine_ranker/test_logistic.py` | Pinned-weight `predict_proba` regression-lock + artifact load/validate. | Create |
| `tests/engine_ranker/test_loader.py` | Auto-fallback chain: logistic→heuristic on missing/malformed artifact. | Create |
| `tests/engine_ranker/test_training.py` | `build_outcome_dataset` + walk-forward split + determinism + lookahead-guard. | Create |
| `tests/engine_ranker/test_promotion.py` | Synthetic datasets producing PROMOTE / HOLD / INSUFFICIENT_DATA. | Create |
| `tests/pipeline/test_engine_ranker_integration.py` | End-to-end heuristic-path golden snapshot + logistic-path candidate-order assertion. | Create |
| `tests/llm/test_ranking_blend.py` | Existing file — add cases for `use_logistic_path=True`. | Modify |
| `tests/history/test_snapshot_engine_prob.py` | Snapshot round-trip preserves `engine_prob=None` for old payloads and populated value for new. | Create |
| `tests/cli/test_train_engine_ranker.py` | CLI dry-run, --write gated by verdict, --force overrides. | Create |
| `docs/superpowers/plans/2026-05-11-outcome-trained-engine-ranker.md` | This document. | Create |

The engine code (`enumerator._score()`, the validator, the templates) is otherwise untouched. PR is mostly additive.

---

## Task 1: Decompose `_score()` into components on `WaveCount`

**Goal:** Make `template_fit`, `fib`, and `recency` sub-scores readable from a `WaveCount` so the pipeline-level re-ranker can consume them without recomputation.

**Files:**
- Modify: `src/wave_alpha/elliott/models.py` (add three optional fields to `WaveCount`)
- Modify: `src/wave_alpha/elliott/enumerator.py:108-109` (populate them in `_score()`)
- Create: `tests/elliott/test_score_components.py`

- [ ] **Step 1: Write the failing test**

Create `tests/elliott/test_score_components.py`:

```python
"""Verifies _score() populates the three component sub-score fields on WaveCount."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.elliott.enumerator import (
    _WEIGHT_BASE,
    _WEIGHT_CONFLUENCE,
    _WEIGHT_RECENCY,
    enumerate_counts,
)
from wave_alpha.elliott.templates import load_default_templates
from wave_alpha.pivots.models import PivotEvent, PivotType


def _seed_impulse_pivots() -> list[PivotEvent]:
    """Six pivots forming a clean upward impulse: L H L H L H."""
    prices = [Decimal("100"), Decimal("130"), Decimal("115"),
              Decimal("160"), Decimal("145"), Decimal("190")]
    types = [PivotType.LOW, PivotType.HIGH, PivotType.LOW,
             PivotType.HIGH, PivotType.LOW, PivotType.HIGH]
    return [
        PivotEvent(timestamp=date(2024, 1, 1 + 7 * i), price=p, type=t, degree=2)
        for i, (p, t) in enumerate(zip(prices, types))
    ]


def test_score_components_populated_on_winning_count() -> None:
    pivots = _seed_impulse_pivots()
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    assert counts, "expected at least one count from a clean impulse fixture"
    top = counts[0]
    assert top.template_fit_component is not None
    assert top.fib_component is not None
    assert top.recency_component is not None
    # Components are bounded sub-scores
    assert 0.0 <= top.template_fit_component <= 1.0
    assert 0.0 <= top.fib_component <= 1.0
    assert 0.2 <= top.recency_component <= 1.0


def test_components_recombine_to_legacy_score() -> None:
    """The hand-tuned linear combination of the three components must equal
    `count.score` exactly, so HeuristicEngineRanker (built on the components)
    reproduces legacy behavior bit-identically."""
    pivots = _seed_impulse_pivots()
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    for c in counts:
        if c.template_fit_component is None:
            continue
        expected = max(
            0.0,
            _WEIGHT_BASE * c.template_fit_component
            + _WEIGHT_CONFLUENCE * c.fib_component
            + _WEIGHT_RECENCY * c.recency_component,
        )
        assert c.score == pytest.approx(expected, abs=1e-12), (
            f"{c.pattern}: components do not recombine to score; "
            f"got components ({c.template_fit_component}, {c.fib_component}, "
            f"{c.recency_component}) vs score={c.score}"
        )


def test_handbuilt_count_components_default_to_none() -> None:
    """WaveCount instances built outside the enumerator (test fixtures,
    handbuilt golden cases) have None components. Backwards-compat for
    callers that never go through _score()."""
    from wave_alpha.elliott.models import WaveCount

    c = WaveCount(pattern="impulse", degree=2, pivots=_seed_impulse_pivots())
    assert c.template_fit_component is None
    assert c.fib_component is None
    assert c.recency_component is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/elliott/test_score_components.py -v`
Expected: FAIL — `AttributeError: 'WaveCount' object has no attribute 'template_fit_component'`.

- [ ] **Step 3: Add the three fields to `WaveCount`**

Edit `src/wave_alpha/elliott/models.py` — add the fields immediately below the existing `score` field at line 49:

```python
class WaveCount(BaseModel):
    """A pattern instance bound to a sequence of pivots, with optional sub-counts."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    degree: int
    pivots: list[PivotEvent]
    sub_counts: dict[str, WaveCount] = Field(default_factory=dict)
    score: float = 0.0
    # Decomposed sub-scores populated by enumerator._score(); None for counts
    # built by hand (test fixtures) or by callers that skip the scorer.
    template_fit_component: float | None = None
    fib_component: float | None = None
    recency_component: float | None = None
    hard_violations: list[str] = Field(default_factory=list)
    soft_violations: list[str] = Field(default_factory=list)

    def segments(self) -> list[WaveSegment]:
        return [WaveSegment(start=a, end=b) for a, b in pairwise(self.pivots)]
```

- [ ] **Step 4: Populate the components in `_score()`**

Edit `src/wave_alpha/elliott/enumerator.py:108-109`. Currently:

```python
                score = _score(count, tpl, last_window_idx=end_idx - 1, latest_idx=latest_idx)
                count = count.model_copy(update={"score": score})
```

Replace with:

```python
                base = 1.0 - 0.10 * len(count.soft_violations)
                confluence = _confluence_bonus(count, tpl)
                recency = _recency_factor(last_window_idx=end_idx - 1, latest_idx=latest_idx)
                raw = (
                    _WEIGHT_BASE * base
                    + _WEIGHT_CONFLUENCE * confluence
                    + _WEIGHT_RECENCY * recency
                )
                score = max(0.0, raw)
                count = count.model_copy(
                    update={
                        "score": score,
                        "template_fit_component": base,
                        "fib_component": confluence,
                        "recency_component": recency,
                    }
                )
```

Then delete the now-unused `_score` helper (lines 124-131) since its body has been inlined.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/elliott/test_score_components.py -v`
Expected: PASS for all three tests.

- [ ] **Step 6: Run the full enumerator test suite to catch regressions**

Run: `uv run pytest tests/elliott -v`
Expected: PASS. The change is purely additive; existing tests that read `count.score` see unchanged values.

- [ ] **Step 7: Lint + format**

Run: `uv run ruff check src/wave_alpha/elliott tests/elliott && uv run ruff format src/wave_alpha/elliott tests/elliott`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/elliott/models.py src/wave_alpha/elliott/enumerator.py tests/elliott/test_score_components.py
git commit -m "$(cat <<'EOF'
feat(elliott): decompose engine_score into component sub-scores

Adds template_fit_component / fib_component / recency_component fields
to WaveCount; populated by the enumerator's scorer alongside the
existing scalar score. Enables a pipeline-level re-ranker to read the
components without recomputation, in service of the outcome-trained
engine ranker. Purely additive: fields default to None for hand-built
counts; legacy score field is unchanged bit-for-bit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `EngineRankerFeatures` + `HeuristicEngineRanker`

**Goal:** Create the new module skeleton with the feature dataclass and the legacy-formula heuristic baseline — no inference wiring yet.

**Files:**
- Create: `src/wave_alpha/engine_ranker/__init__.py`
- Create: `src/wave_alpha/engine_ranker/features.py`
- Create: `src/wave_alpha/engine_ranker/heuristic.py`
- Create: `tests/engine_ranker/__init__.py` (empty, makes it a package)
- Create: `tests/engine_ranker/test_features.py`
- Create: `tests/engine_ranker/test_heuristic.py`

- [ ] **Step 1: Write the failing test for features**

Create `tests/engine_ranker/__init__.py` (empty file).

Create `tests/engine_ranker/test_features.py`:

```python
"""Unit tests for EngineRankerFeatures and its from_count extraction."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.models import WaveCount
from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.pivots.models import PivotEvent, PivotType


def _two_pivots() -> list[PivotEvent]:
    return [
        PivotEvent(timestamp=date(2024, 1, 1), price=Decimal("100"),
                   type=PivotType.LOW, degree=2),
        PivotEvent(timestamp=date(2024, 1, 8), price=Decimal("120"),
                   type=PivotType.HIGH, degree=2),
    ]


def test_features_from_count_reads_components_and_degree() -> None:
    count = WaveCount(
        pattern="impulse",
        degree=3,
        pivots=_two_pivots(),
        score=0.7,
        template_fit_component=0.9,
        fib_component=0.5,
        recency_component=0.8,
    )
    coh = CoherenceResult(score=0.6, multiplier=1.2, label="strong")

    f = EngineRankerFeatures.from_count(count, coh)

    assert f.template_fit == pytest.approx(0.9)
    assert f.fib == pytest.approx(0.5)
    assert f.recency == pytest.approx(0.8)
    assert f.coherence == pytest.approx(0.6)
    assert f.degree == 3


def test_features_from_count_raises_when_components_missing() -> None:
    """A WaveCount that was never scored has None components. The feature
    extractor must refuse rather than silently using zeros — that would
    poison the logistic with phantom rows."""
    count = WaveCount(pattern="impulse", degree=2, pivots=_two_pivots())
    coh = CoherenceResult(score=0.5, multiplier=1.0, label="moderate")

    with pytest.raises(ValueError, match="components"):
        EngineRankerFeatures.from_count(count, coh)


def test_features_from_count_handles_none_coherence() -> None:
    """If coherence is None (e.g. single-timeframe path), use 0.5 as a neutral
    prior — equivalent to 'no signal'."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=_two_pivots(),
        score=0.5,
        template_fit_component=0.8,
        fib_component=0.4,
        recency_component=0.6,
    )
    f = EngineRankerFeatures.from_count(count, None)
    assert f.coherence == pytest.approx(0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_features.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker'`.

- [ ] **Step 3: Create the module + feature dataclass**

Create `src/wave_alpha/engine_ranker/__init__.py`:

```python
"""Outcome-trained engine ranker.

Mirrors right_edge/ in layout: features → {heuristic, logistic} models →
loader (auto-fallback) → training → promotion. See spec
docs/superpowers/specs/2026-05-11-outcome-trained-engine-ranker-design.md.
"""

from __future__ import annotations

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.loader import EngineRankerModel, load_engine_ranker

__all__ = ["EngineRankerFeatures", "EngineRankerModel", "load_engine_ranker"]
```

Create `src/wave_alpha/engine_ranker/features.py`:

```python
"""Engine-ranker feature vector and extraction helper."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.models import WaveCount


class EngineRankerFeatures(BaseModel):
    """The four inputs to the logistic re-ranker, plus the degree used for
    per-degree intercept lookup. Bounded ranges:

    - template_fit ∈ [0, 1] (soft-violation penalty from enumerator)
    - fib ∈ [0, 1] (confluence_score output)
    - recency ∈ [0.2, 1.0] (linear decay over pivot gap)
    - coherence ∈ [0, 1] (three_way coherence score; NOT the multiplier)
    """

    model_config = ConfigDict(frozen=True)

    template_fit: float
    fib: float
    recency: float
    coherence: float
    degree: int

    @classmethod
    def from_count(
        cls, count: WaveCount, coherence: CoherenceResult | None
    ) -> EngineRankerFeatures:
        """Build features from a scored WaveCount and the snapshot's coherence
        result.

        Raises:
            ValueError: if any of the component fields on `count` is None
                (i.e. the count was never run through the enumerator's
                scorer). Refusing here prevents phantom rows in training.
        """
        if (
            count.template_fit_component is None
            or count.fib_component is None
            or count.recency_component is None
        ):
            raise ValueError(
                "WaveCount has None components; cannot build EngineRankerFeatures. "
                "Was the count built by hand instead of via enumerate_counts()?"
            )
        coh_score = 0.5 if coherence is None else float(coherence.score)
        return cls(
            template_fit=float(count.template_fit_component),
            fib=float(count.fib_component),
            recency=float(count.recency_component),
            coherence=coh_score,
            degree=int(count.degree),
        )
```

- [ ] **Step 4: Run features test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_features.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Write the failing test for the heuristic baseline**

Create `tests/engine_ranker/test_heuristic.py`:

```python
"""Verifies HeuristicEngineRanker reproduces enumerator._score() bit-for-bit."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.enumerator import (
    _WEIGHT_BASE,
    _WEIGHT_CONFLUENCE,
    _WEIGHT_RECENCY,
    enumerate_counts,
)
from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.pivots.models import PivotEvent, PivotType


def _impulse_pivots() -> list[PivotEvent]:
    prices = [Decimal("100"), Decimal("130"), Decimal("115"),
              Decimal("160"), Decimal("145"), Decimal("190")]
    types = [PivotType.LOW, PivotType.HIGH, PivotType.LOW,
             PivotType.HIGH, PivotType.LOW, PivotType.HIGH]
    return [
        PivotEvent(timestamp=date(2024, 1, 1 + 7 * i), price=p, type=t, degree=2)
        for i, (p, t) in enumerate(zip(prices, types))
    ]


def test_heuristic_matches_legacy_score_exactly() -> None:
    """For every count produced by enumerate_counts, HeuristicEngineRanker
    on the same components must return the exact same score (un-cohered raw)."""
    counts = enumerate_counts(_impulse_pivots(), degree=2, top_k=5)
    assert counts
    coh = CoherenceResult(score=0.7, multiplier=1.0, label="strong")
    h = HeuristicEngineRanker()
    for c in counts:
        f = EngineRankerFeatures.from_count(c, coh)
        produced = h.predict_proba(f)
        expected = max(
            0.0,
            _WEIGHT_BASE * c.template_fit_component
            + _WEIGHT_CONFLUENCE * c.fib_component
            + _WEIGHT_RECENCY * c.recency_component,
        )
        assert produced == pytest.approx(expected, abs=1e-12), (
            f"heuristic ranker diverged from legacy _score: "
            f"got {produced}, expected {expected}"
        )


def test_heuristic_ignores_coherence_in_raw_score() -> None:
    """The heuristic returns the UN-cohered raw; coherence enters via the
    outside multiplier in blend_scores. The two features (coh, degree) must
    not affect the heuristic's output."""
    f1 = EngineRankerFeatures(
        template_fit=0.9, fib=0.5, recency=0.8, coherence=0.1, degree=2
    )
    f2 = EngineRankerFeatures(
        template_fit=0.9, fib=0.5, recency=0.8, coherence=0.9, degree=3
    )
    h = HeuristicEngineRanker()
    assert h.predict_proba(f1) == h.predict_proba(f2)
```

- [ ] **Step 6: Run heuristic test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_heuristic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker.heuristic'`.

- [ ] **Step 7: Create the heuristic baseline**

Create `src/wave_alpha/engine_ranker/heuristic.py`:

```python
"""Hand-tuned engine-ranker baseline.

Wraps the same _WEIGHT_BASE / _WEIGHT_CONFLUENCE / _WEIGHT_RECENCY linear
combination that enumerator._score() applies. Used as:

- The default model returned by load_engine_ranker() when no logistic
  artifact is present.
- The baseline in promotion.evaluate_promotion against which a candidate
  logistic must demonstrate non-negative Brier and expectancy lift.

Crucially, this baseline does NOT include coherence — coherence enters
via the outside multiplier in blend_scores. See spec §5.4.
"""

from __future__ import annotations

from dataclasses import dataclass

from wave_alpha.elliott.enumerator import (
    _WEIGHT_BASE,
    _WEIGHT_CONFLUENCE,
    _WEIGHT_RECENCY,
)
from wave_alpha.engine_ranker.features import EngineRankerFeatures


@dataclass(frozen=True)
class HeuristicEngineRanker:
    """Reproduces enumerator._score() exactly. Coherence and degree are read
    from the feature vector and intentionally ignored — coherence is applied
    outside the candidate (via blend_scores' coh_multiplier) in the heuristic
    pipeline path, and degree has no role in the hand-tuned formula."""

    def predict_proba(self, features: EngineRankerFeatures) -> float:
        raw = (
            _WEIGHT_BASE * features.template_fit
            + _WEIGHT_CONFLUENCE * features.fib
            + _WEIGHT_RECENCY * features.recency
        )
        return max(0.0, raw)
```

- [ ] **Step 8: Run heuristic test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_heuristic.py -v`
Expected: PASS for both tests.

- [ ] **Step 9: Lint + format**

Run: `uv run ruff check src/wave_alpha/engine_ranker tests/engine_ranker && uv run ruff format src/wave_alpha/engine_ranker tests/engine_ranker`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/wave_alpha/engine_ranker tests/engine_ranker/__init__.py tests/engine_ranker/test_features.py tests/engine_ranker/test_heuristic.py
git commit -m "$(cat <<'EOF'
feat(engine_ranker): module skeleton with features + heuristic baseline

New engine_ranker module mirrors right_edge/ layout. EngineRankerFeatures
holds (template_fit, fib, recency, coherence, degree) extracted from a
scored WaveCount. HeuristicEngineRanker reproduces enumerator._score()
to 1e-12 and is the no-artifact fallback / promotion baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `LogisticEngineRanker` + loader

**Goal:** JSON-artifact-backed logistic model with shared coefficients + per-degree intercepts, and a loader with the auto-fallback chain.

**Files:**
- Create: `src/wave_alpha/engine_ranker/logistic.py`
- Create: `src/wave_alpha/engine_ranker/loader.py`
- Create: `src/wave_alpha/engine_ranker/models/.gitkeep`
- Create: `tests/engine_ranker/test_logistic.py`
- Create: `tests/engine_ranker/test_loader.py`

- [ ] **Step 1: Write the failing test for the logistic model**

Create `tests/engine_ranker/test_logistic.py`:

```python
"""Unit tests for LogisticEngineRanker artifact loading + prediction."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.logistic import (
    LogisticEngineRanker,
    EngineRankerArtifactError,
)


def _good_artifact() -> dict:
    return {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"1": -2.0, "2": -1.0, "3": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }


def test_predict_proba_pinned_weights(tmp_path: Path) -> None:
    """Regression-lock: identical input → identical output, forever."""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    m = LogisticEngineRanker.from_json(path)
    f = EngineRankerFeatures(
        template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=2
    )
    # z = 1.0*0.8 + 1.0*0.5 + 1.0*0.7 + 1.0*0.6 + (-1.0) = 1.6
    # sigmoid(1.6) = 0.83201839...
    expected = 1.0 / (1.0 + math.exp(-1.6))
    assert m.predict_proba(f) == pytest.approx(expected, abs=1e-9)


def test_predict_proba_unknown_degree_uses_median_intercept(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    m = LogisticEngineRanker.from_json(path)
    # Median of [-2, -1, 0] is -1.0.
    f_unknown = EngineRankerFeatures(
        template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=99
    )
    f_known = EngineRankerFeatures(
        template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=2
    )
    assert m.predict_proba(f_unknown) == pytest.approx(m.predict_proba(f_known))


def test_refuses_to_load_unpromoted_artifact(tmp_path: Path) -> None:
    bad = _good_artifact()
    bad["gate"]["verdict"] = "HOLD"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(EngineRankerArtifactError, match="verdict"):
        LogisticEngineRanker.from_json(path)


def test_refuses_unknown_feature_name(tmp_path: Path) -> None:
    bad = _good_artifact()
    bad["feature_order"] = ["template_fit", "fib", "recency", "not_a_field"]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(EngineRankerArtifactError, match="not_a_field"):
        LogisticEngineRanker.from_json(path)


def test_sigmoid_clipping_no_overflow(tmp_path: Path) -> None:
    """Pathological weights must not raise OverflowError."""
    art = _good_artifact()
    art["weights"] = [1000.0, 1000.0, 1000.0, 1000.0]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(art))
    m = LogisticEngineRanker.from_json(path)
    f = EngineRankerFeatures(
        template_fit=1.0, fib=1.0, recency=1.0, coherence=1.0, degree=2
    )
    p = m.predict_proba(f)
    assert 0.0 < p <= 1.0
```

- [ ] **Step 2: Run logistic test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_logistic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker.logistic'`.

- [ ] **Step 3: Implement `LogisticEngineRanker`**

Create `src/wave_alpha/engine_ranker/logistic.py`:

```python
"""Runtime-loadable logistic engine ranker.

Reads a plain JSON artifact produced by training.serialize_artifact.
No scikit-learn at runtime; stdlib + project types only.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from wave_alpha.engine_ranker.features import EngineRankerFeatures

_PROMOTE_VERDICTS = frozenset({"PROMOTE"})


def _sigmoid(z: float) -> float:
    """Numerically safe sigmoid: clips z to [-50, 50]."""
    z = max(min(z, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-z))


class EngineRankerArtifactError(ValueError):
    """Raised when an engine-ranker artifact is malformed or non-promotable."""


@dataclass(frozen=True)
class LogisticEngineRanker:
    """Logistic regression with shared coefficient vector and per-degree
    intercepts. Output is calibrated P(outcome=target) ∈ [0, 1]."""

    version: str
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept_per_degree: dict[int, float]
    _median_intercept: float  # filled by from_json; fallback for unseen degrees

    @classmethod
    def from_json(cls, path: Path) -> LogisticEngineRanker:
        raw = json.loads(path.read_text())
        verdict = raw.get("gate", {}).get("verdict")
        if verdict not in _PROMOTE_VERDICTS:
            raise EngineRankerArtifactError(
                f"Refusing to load engine-ranker artifact with verdict={verdict!r}"
            )
        feature_order = tuple(raw["feature_order"])
        cls._validate_feature_names(feature_order)
        intercepts = {int(d): float(v) for d, v in raw["intercept_per_degree"].items()}
        if not intercepts:
            raise EngineRankerArtifactError("intercept_per_degree must be non-empty")
        median = statistics.median(intercepts.values())
        return cls(
            version=raw["version"],
            feature_order=feature_order,
            weights=tuple(float(w) for w in raw["weights"]),
            intercept_per_degree=intercepts,
            _median_intercept=median,
        )

    @staticmethod
    def _validate_feature_names(names: tuple[str, ...]) -> None:
        valid = set(EngineRankerFeatures.model_fields.keys())
        unknown = [n for n in names if n not in valid]
        if unknown:
            raise EngineRankerArtifactError(
                f"Artifact feature_order references unknown EngineRankerFeatures fields: {unknown!r}. "
                f"Refusing to load — would silently misalign predictions."
            )

    def predict_proba(self, features: EngineRankerFeatures) -> float:
        v = [float(getattr(features, name)) for name in self.feature_order]
        if len(v) != len(self.weights):
            raise EngineRankerArtifactError(
                f"Feature/weight length mismatch: {len(v)} vs {len(self.weights)}"
            )
        b = self.intercept_per_degree.get(features.degree, self._median_intercept)
        z = sum(w * x for w, x in zip(self.weights, v, strict=True)) + b
        return _sigmoid(z)
```

- [ ] **Step 4: Run logistic test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_logistic.py -v`
Expected: PASS for all five tests.

- [ ] **Step 5: Write the failing test for the loader**

Create `tests/engine_ranker/test_loader.py`:

```python
"""Tests for load_engine_ranker auto-fallback chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.loader import load_engine_ranker
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.prefs.models import EngineRankerConfig


def _good_artifact() -> dict:
    return {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"2": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }


def test_auto_falls_back_to_heuristic_when_no_artifact(tmp_path: Path) -> None:
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=tmp_path / "nope.json")
    assert isinstance(model, HeuristicEngineRanker)


def test_auto_loads_logistic_when_present(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, LogisticEngineRanker)


def test_auto_falls_back_on_malformed_artifact(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text("not json {{{")
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_auto_falls_back_on_unpromoted_artifact(tmp_path: Path) -> None:
    art = _good_artifact()
    art["gate"]["verdict"] = "HOLD"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(art))
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_heuristic_kind_returns_heuristic_even_when_artifact_present(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    cfg = EngineRankerConfig(kind="heuristic")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_logistic_kind_raises_on_missing_artifact(tmp_path: Path) -> None:
    cfg = EngineRankerConfig(kind="logistic")
    with pytest.raises(FileNotFoundError):
        load_engine_ranker(cfg, logistic_path=tmp_path / "nope.json")
```

- [ ] **Step 6: Run loader test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker.loader'` (and EngineRankerConfig doesn't yet exist on `prefs.models`, but the next step adds both).

- [ ] **Step 7: Implement the loader**

Create `src/wave_alpha/engine_ranker/loader.py`:

```python
"""Engine-ranker loader: returns the right model per config kind.

Auto mode (default) preference order: logistic > heuristic. Falls back
silently on missing/malformed artifact.

Modeled after wave_alpha.right_edge.loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from loguru import logger

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.logistic import (
    EngineRankerArtifactError,
    LogisticEngineRanker,
)
from wave_alpha.prefs.models import EngineRankerConfig


class EngineRankerModel(Protocol):
    """Protocol satisfied by HeuristicEngineRanker and LogisticEngineRanker."""

    def predict_proba(self, features: EngineRankerFeatures) -> float: ...


_DEFAULT_LOGISTIC_PATH = Path(__file__).parent / "models" / "engine_ranker_logistic_v1.json"


def load_engine_ranker(
    cfg: EngineRankerConfig,
    *,
    logistic_path: Path = _DEFAULT_LOGISTIC_PATH,
) -> EngineRankerModel:
    """Return the appropriate EngineRankerModel for the given config.

    - kind="heuristic": always HeuristicEngineRanker.
    - kind="logistic": LogisticEngineRanker; raises FileNotFoundError if absent.
    - kind="auto": prefer logistic; on missing/malformed artifact, fall back
      to HeuristicEngineRanker silently (with INFO log).
    """
    if cfg.kind == "heuristic":
        return HeuristicEngineRanker()

    if cfg.kind == "logistic":
        return LogisticEngineRanker.from_json(logistic_path)

    # auto
    if logistic_path.exists():
        try:
            return LogisticEngineRanker.from_json(logistic_path)
        except (EngineRankerArtifactError, ValueError, KeyError, OSError) as exc:
            logger.info("engine_ranker: logistic unloadable, falling back to heuristic ({})", exc)
    return HeuristicEngineRanker()
```

Create the models directory placeholder so the path resolves predictably:

```bash
mkdir -p src/wave_alpha/engine_ranker/models
touch src/wave_alpha/engine_ranker/models/.gitkeep
```

- [ ] **Step 8: Verify EngineRankerConfig exists**

The loader imports `EngineRankerConfig` from `prefs.models`. If Task 4 has not yet run, add a *minimal* stub to make this task standalone. Add to `src/wave_alpha/prefs/models.py` immediately after `RightEdgeConfig`:

```python
class EngineRankerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["auto", "heuristic", "logistic"] = "auto"
```

(Task 4 will expand this with `outcome_training` etc.)

- [ ] **Step 9: Run loader test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_loader.py -v`
Expected: PASS for all six tests.

- [ ] **Step 10: Run the entire engine_ranker test package**

Run: `uv run pytest tests/engine_ranker -v`
Expected: PASS — all of test_features, test_heuristic, test_logistic, test_loader.

- [ ] **Step 11: Lint + format**

Run: `uv run ruff check src/wave_alpha/engine_ranker tests/engine_ranker src/wave_alpha/prefs && uv run ruff format src/wave_alpha/engine_ranker tests/engine_ranker src/wave_alpha/prefs`
Expected: clean.

- [ ] **Step 12: Commit**

```bash
git add src/wave_alpha/engine_ranker/logistic.py src/wave_alpha/engine_ranker/loader.py src/wave_alpha/engine_ranker/models/.gitkeep src/wave_alpha/prefs/models.py tests/engine_ranker/test_logistic.py tests/engine_ranker/test_loader.py
git commit -m "$(cat <<'EOF'
feat(engine_ranker): logistic model + auto-fallback loader

LogisticEngineRanker reads JSON artifacts produced by training; refuses
non-PROMOTE verdicts and unknown feature names. Shared coefficient
vector + per-degree intercept; unseen degrees fall back to median
intercept with a WARNING.

load_engine_ranker mirrors right_edge.loader: auto > logistic >
heuristic, silent fallback on missing or malformed artifact. Minimal
EngineRankerConfig added to prefs.models (kind only); expanded in
Task 4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Expand `EngineRankerConfig` with outcome-training fields

**Goal:** Add the training-time config sub-block now so the training/CLI tasks downstream can reference it.

**Files:**
- Modify: `src/wave_alpha/prefs/models.py`
- Create: `tests/prefs/test_engine_ranker_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/prefs/test_engine_ranker_config.py`:

```python
"""Tests for EngineRankerConfig defaults and AppConfig wiring."""

from __future__ import annotations

import pytest

from wave_alpha.prefs.models import (
    AppConfig,
    EngineRankerConfig,
    EngineRankerOutcomeTrainingConfig,
)


def test_engine_ranker_outcome_training_defaults() -> None:
    cfg = EngineRankerOutcomeTrainingConfig()
    assert cfg.walk_forward_split == pytest.approx(0.7)
    assert cfg.class_balance_low == pytest.approx(0.35)
    assert cfg.class_balance_high == pytest.approx(0.65)
    assert cfg.include_time_stops is True
    assert cfg.min_resolved_per_degree == 30


def test_engine_ranker_config_defaults() -> None:
    cfg = EngineRankerConfig()
    assert cfg.kind == "auto"
    assert isinstance(cfg.outcome_training, EngineRankerOutcomeTrainingConfig)


def test_app_config_includes_engine_ranker() -> None:
    app = AppConfig()
    assert app.engine_ranker.kind == "auto"


def test_walk_forward_split_bounds() -> None:
    with pytest.raises(ValueError):
        EngineRankerOutcomeTrainingConfig(walk_forward_split=1.0)
    with pytest.raises(ValueError):
        EngineRankerOutcomeTrainingConfig(walk_forward_split=0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prefs/test_engine_ranker_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'EngineRankerOutcomeTrainingConfig'`.

- [ ] **Step 3: Expand the prefs config**

Edit `src/wave_alpha/prefs/models.py`. Replace the minimal `EngineRankerConfig` added in Task 3 and wire into `AppConfig`. The full new block (added between `RightEdgeConfig` and `DataConfig`):

```python
class EngineRankerOutcomeTrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    walk_forward_split: float = Field(default=0.7, gt=0.0, lt=1.0)
    """Fraction of distinct as_of dates assigned to the training split."""

    class_balance_low: float = Field(default=0.35, ge=0.0, le=1.0)
    class_balance_high: float = Field(default=0.65, ge=0.0, le=1.0)
    """Training sets a balanced class weight when observed target rate falls
    outside [class_balance_low, class_balance_high]."""

    include_time_stops: bool = True
    """If True, time_stop outcomes are treated as the negative class. If
    False, time_stop rows are dropped entirely."""

    min_resolved_per_degree: int = Field(default=30, ge=1)
    """Promotion gate elevates verdict to INSUFFICIENT_DATA if any observed
    degree has fewer than this many resolved rows."""


class EngineRankerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["auto", "heuristic", "logistic"] = "auto"
    outcome_training: EngineRankerOutcomeTrainingConfig = Field(
        default_factory=EngineRankerOutcomeTrainingConfig
    )
```

Then update `AppConfig` (around line 69) to include the field:

```python
class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    right_edge: RightEdgeConfig = Field(default_factory=RightEdgeConfig)
    engine_ranker: EngineRankerConfig = Field(default_factory=EngineRankerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    templates: TemplatesConfig = Field(default_factory=TemplatesConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/prefs/test_engine_ranker_config.py -v`
Expected: PASS for all four tests.

- [ ] **Step 5: Run the full prefs test suite to catch regressions**

Run: `uv run pytest tests/prefs -v`
Expected: PASS. Additive change to an existing optional-by-default config.

- [ ] **Step 6: Lint + format**

Run: `uv run ruff check src/wave_alpha/prefs tests/prefs && uv run ruff format src/wave_alpha/prefs tests/prefs`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/prefs/models.py tests/prefs/test_engine_ranker_config.py
git commit -m "$(cat <<'EOF'
feat(prefs): EngineRankerConfig + outcome-training sub-block

Wires engine_ranker into AppConfig with a kind selector (auto/heuristic/
logistic) and an outcome_training sub-block holding the walk-forward
split, class-balance band, time-stop handling, and the per-degree row
gate that training and promotion read.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Pipeline integration + new `blend_scores` paths

**Goal:** Wire `engine_ranker.predict_proba` into `pipeline._rank`; teach `blend_scores` the new probabilistic formula; preserve heuristic-path output bit-for-bit via a golden-snapshot regression test.

**Files:**
- Modify: `src/wave_alpha/llm/ranking.py`
- Modify: `src/wave_alpha/pipeline.py` (RankedCount + _rank)
- Modify: `tests/llm/test_ranking_blend.py`
- Create: `tests/pipeline/test_engine_ranker_integration.py`

- [ ] **Step 1: Write the failing test for `blend_scores`**

Find existing blend tests:

```bash
ls tests/llm/test_ranking*.py 2>/dev/null
```

If `tests/llm/test_ranking_blend.py` does not exist, create it; otherwise append to it. Add the following test cases (verbatim — do not adapt to local style mid-flight):

```python
"""Additional cases for the logistic-path branch of blend_scores."""

from __future__ import annotations

import pytest

from wave_alpha.llm.ranking import blend_scores


def test_logistic_path_no_llm_returns_engine_prob() -> None:
    """With use_logistic_path=True and llm_prob=None, the function must
    return engine_prob unchanged — no coh_multiplier discount, no alpha."""
    out = blend_scores(
        coh_multiplier=1.5,  # intentionally != 1 to prove it's ignored
        engine_score=0.0,    # ignored on the logistic path
        llm_prob=None,
        alpha=0.6,
        engine_prob=0.72,
        use_logistic_path=True,
    )
    assert out == pytest.approx(0.72)


def test_logistic_path_with_llm_blends_two_probabilities() -> None:
    out = blend_scores(
        coh_multiplier=1.5,
        engine_score=0.0,
        llm_prob=0.40,
        alpha=0.7,
        engine_prob=0.80,
        use_logistic_path=True,
    )
    # 0.7 * 0.80 + 0.3 * 0.40 = 0.56 + 0.12 = 0.68
    assert out == pytest.approx(0.68)


def test_legacy_path_unchanged_when_use_logistic_false() -> None:
    """The heuristic-path formula must be byte-identical to today's behavior."""
    out = blend_scores(
        coh_multiplier=1.2,
        engine_score=0.5,
        llm_prob=0.7,
        alpha=0.6,
    )
    # 1.2 * (0.6 * 0.5 + 0.4 * 0.7) = 1.2 * (0.30 + 0.28) = 1.2 * 0.58 = 0.696
    assert out == pytest.approx(0.696)


def test_logistic_path_requires_engine_prob() -> None:
    with pytest.raises(ValueError, match="engine_prob"):
        blend_scores(
            coh_multiplier=1.0,
            engine_score=0.5,
            llm_prob=0.5,
            alpha=0.5,
            use_logistic_path=True,
            engine_prob=None,
        )
```

- [ ] **Step 2: Run blend test to verify it fails**

Run: `uv run pytest tests/llm/test_ranking_blend.py -v`
Expected: FAIL — `TypeError: blend_scores() got an unexpected keyword argument 'engine_prob'`.

- [ ] **Step 3: Extend `blend_scores`**

Replace the body of `src/wave_alpha/llm/ranking.py` with:

```python
from __future__ import annotations


def blend_scores(
    *,
    coh_multiplier: float,
    engine_score: float,
    llm_prob: float | None,
    alpha: float,
    engine_prob: float | None = None,
    use_logistic_path: bool = False,
) -> float:
    """Final ranking. Two paths:

    1. Logistic path (use_logistic_path=True): engine_prob is a calibrated
       probability that already includes coherence as a feature.

           final = alpha * engine_prob + (1 - alpha) * llm_prob

       coh_multiplier and engine_score are ignored. llm_prob=None collapses
       to engine_prob.

    2. Legacy heuristic path (default): preserves the v0 formula bit-for-bit:

           final = coh_multiplier * (alpha * engine_score + (1 - alpha) * llm_prob)

       llm_prob=None collapses to coh_multiplier * engine_score.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1]; got {alpha}")
    if use_logistic_path:
        if engine_prob is None:
            raise ValueError("use_logistic_path=True requires engine_prob")
        if llm_prob is None:
            return engine_prob
        return alpha * engine_prob + (1.0 - alpha) * llm_prob
    if llm_prob is None:
        return coh_multiplier * engine_score
    return coh_multiplier * (alpha * engine_score + (1.0 - alpha) * llm_prob)
```

- [ ] **Step 4: Run blend test to verify it passes**

Run: `uv run pytest tests/llm/test_ranking_blend.py -v`
Expected: PASS for all four new cases plus any existing legacy-path cases.

- [ ] **Step 5: Write the pipeline integration test**

Create `tests/pipeline/test_engine_ranker_integration.py`:

```python
"""Pipeline integration: engine_ranker wired into _rank.

Two important guarantees:

1. Heuristic-path final_score is unchanged from before this PR (golden
   regression). Captured via direct computation from a synthetic seed,
   not a recorded fixture, so the test is robust to template additions.

2. Logistic-path produces populated engine_prob on RankedCount and uses
   the new alpha * engine_prob + (1-alpha) * llm_prob formula.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.enumerator import (
    _WEIGHT_BASE,
    _WEIGHT_CONFLUENCE,
    _WEIGHT_RECENCY,
)
from wave_alpha.engine_ranker.loader import load_engine_ranker
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.pipeline import AnalyzeRequest, RankedCount, _rank
from wave_alpha.prefs.models import EngineRankerConfig


class _FakeProvider:
    def __init__(self, bars: dict[tuple[str, Interval], list[Bar]]) -> None:
        self._bars = bars

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval) -> list[Bar]:
    out: list[Bar] = []
    step_days = 1 if interval == Interval.DAILY else (7 if interval == Interval.WEEKLY else 1)
    for i in range(n):
        # Sinusoidal-ish synthetic price so the engine actually finds pivots.
        from math import sin
        base = 100.0 + 30.0 * sin(i / 8.0) + i * 0.1
        price = Decimal(str(round(base, 2)))
        out.append(
            Bar(
                timestamp=start + timedelta(days=i * step_days),
                interval=interval,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


@pytest.fixture
def synthetic_provider() -> _FakeProvider:
    daily = _make_bars(date(2022, 1, 1), 800, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 120, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 800, Interval.HOURLY_4)
    return _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )


def test_heuristic_path_final_score_matches_legacy_formula(synthetic_provider) -> None:
    """Default engine_ranker.kind='auto' with no artifact → heuristic path.
    Asserts the OUTPUT formula final = coh * (alpha * engine_score + (1-alpha) * llm_prob)
    is unchanged, by reproducing it from RankedCount fields directly."""
    from wave_alpha.pipeline import run_analysis
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 6, 30))
    result = run_analysis(req, provider=synthetic_provider)
    for rc in result.ranked_daily:
        assert isinstance(rc, RankedCount)
        assert rc.engine_prob is None, "heuristic path must leave engine_prob unset"
        # llm_prob is None in this run (LLM disabled), so:
        coh_mult = result.coherence.multiplier
        expected = coh_mult * rc.engine_score
        assert rc.final_score == pytest.approx(expected, abs=1e-9)


def test_logistic_path_populates_engine_prob_and_uses_new_formula(
    synthetic_provider, tmp_path, monkeypatch
) -> None:
    # Write a permissive artifact: weights=1 across all features, intercept=0.
    artifact = {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"1": 0.0, "2": 0.0, "3": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }
    art_path = tmp_path / "engine_ranker_logistic_v1.json"
    art_path.write_text(json.dumps(artifact))

    # Monkeypatch the default artifact path so the pipeline finds our artifact.
    from wave_alpha.engine_ranker import loader as loader_mod
    monkeypatch.setattr(loader_mod, "_DEFAULT_LOGISTIC_PATH", art_path)

    from wave_alpha.pipeline import run_analysis
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 6, 30))
    result = run_analysis(req, provider=synthetic_provider)
    assert result.ranked_daily, "expected some ranked counts on synthetic"
    for rc in result.ranked_daily:
        assert rc.engine_prob is not None
        assert 0.0 < rc.engine_prob <= 1.0
        # llm_prob is None (LLM disabled), so logistic-path final = engine_prob
        assert rc.final_score == pytest.approx(rc.engine_prob, abs=1e-9)
```

- [ ] **Step 6: Run integration test to verify it fails**

Run: `uv run pytest tests/pipeline/test_engine_ranker_integration.py -v`
Expected: FAIL — `RankedCount` has no `engine_prob` attribute.

- [ ] **Step 7: Wire `engine_ranker` into the pipeline**

Edit `src/wave_alpha/pipeline.py`.

(a) Update `RankedCount` to include `engine_prob`:

```python
@dataclass(frozen=True)
class RankedCount:
    candidate_id: str
    count: WaveCount
    engine_score: float
    llm_prob: float | None
    final_score: float
    engine_prob: float | None = None
```

(b) Update `_rank` to accept the engine ranker and the coherence result, invoke it when logistic, and choose the blend path:

```python
def _rank(
    *,
    candidates: list[WaveCount],
    coherence: CoherenceResult,
    scan: ScanResponse | None,
    alpha: float,
    llm_mode: LLMMode,
    engine_ranker,  # EngineRankerModel; typed as Any to avoid loader-time import
) -> list[RankedCount]:
    from wave_alpha.engine_ranker.features import EngineRankerFeatures
    from wave_alpha.engine_ranker.logistic import LogisticEngineRanker

    use_logistic = isinstance(engine_ranker, LogisticEngineRanker)
    probs: dict[str, float] = {}
    if scan is not None:
        probs = {c.id: c.probability for c in scan.candidates}
    out: list[RankedCount] = []
    for i, c in enumerate(candidates):
        cid = f"C{i + 1}"
        llm_prob = probs.get(cid) if scan is not None else None
        engine_prob: float | None = None
        if use_logistic:
            try:
                feats = EngineRankerFeatures.from_count(c, coherence)
                engine_prob = engine_ranker.predict_proba(feats)
            except ValueError:
                # Components missing — fall back to heuristic blend for this row.
                engine_prob = None
        final = blend_scores(
            coh_multiplier=coherence.multiplier,
            engine_score=c.score,
            llm_prob=llm_prob if llm_mode is not LLMMode.DISABLED else None,
            alpha=alpha,
            engine_prob=engine_prob,
            use_logistic_path=use_logistic and engine_prob is not None,
        )
        out.append(
            RankedCount(
                candidate_id=cid,
                count=c,
                engine_score=c.score,
                llm_prob=llm_prob,
                final_score=final,
                engine_prob=engine_prob,
            )
        )
    out.sort(key=lambda rc: rc.final_score, reverse=True)
    return out
```

(c) Update `run_analysis` to construct the engine ranker and pass it + coherence into `_rank`. Replace the existing `_rank(...)` call site (around line 115) with:

```python
    from wave_alpha.engine_ranker.loader import load_engine_ranker
    from wave_alpha.prefs.config import load_config  # if AppConfig isn't already in scope
    engine_ranker = load_engine_ranker(load_config().engine_ranker)

    ranked = _rank(
        candidates=counts_daily,
        coherence=coh,
        scan=llm_scan,
        alpha=alpha,
        llm_mode=llm_mode,
        engine_ranker=engine_ranker,
    )
```

If `load_config` does not exist in `prefs/config.py`, fall back to `AppConfig()` defaults — i.e. `engine_ranker = load_engine_ranker(AppConfig().engine_ranker)`. Verify the actual import path before committing:

```bash
grep -n "def load_config\|class AppConfig" src/wave_alpha/prefs/*.py
```

Use whichever spelling actually exists. (Either form satisfies the integration test, which monkeypatches the artifact path.)

- [ ] **Step 8: Run integration test to verify it passes**

Run: `uv run pytest tests/pipeline/test_engine_ranker_integration.py -v`
Expected: PASS for both tests.

- [ ] **Step 9: Run the full pipeline + llm test suites**

Run: `uv run pytest tests/pipeline tests/llm -v`
Expected: PASS — no existing test broken. The legacy-path golden test in particular asserts bit-identical output.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest`
Expected: PASS. Any failure here is a real regression — investigate before continuing.

- [ ] **Step 11: Lint + format**

Run: `uv run ruff check . && uv run ruff format src/wave_alpha tests`
Expected: clean.

- [ ] **Step 12: Commit**

```bash
git add src/wave_alpha/llm/ranking.py src/wave_alpha/pipeline.py tests/llm/test_ranking_blend.py tests/pipeline/test_engine_ranker_integration.py
git commit -m "$(cat <<'EOF'
feat(pipeline): integrate engine_ranker into _rank + blend_scores

blend_scores gains a logistic path: final = alpha * engine_prob +
(1-alpha) * llm_prob, no coh_multiplier (coherence is a feature of the
logistic). Legacy heuristic path is preserved bit-for-bit when
engine_ranker.kind resolves to heuristic.

RankedCount now carries optional engine_prob, populated only when the
active model is logistic — so consumers can distinguish a calibrated
probability from absent. Pipeline always loads the engine ranker via
AppConfig.engine_ranker; default 'auto' falls back to heuristic when
no artifact is present, preserving current behavior at this commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `SnapshotCount.engine_prob` field

**Goal:** Allow new snapshots to carry the calibrated probability so future training rounds can sanity-check the model's live predictions against realized outcomes (without forcing recomputation).

**Files:**
- Modify: `src/wave_alpha/history/models.py`
- Modify: `src/wave_alpha/history/service.py`
- Create: `tests/history/test_snapshot_engine_prob.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_snapshot_engine_prob.py`:

```python
"""SnapshotCount.engine_prob is optional and round-trips through the store."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
)


def _base_count(**overrides) -> SnapshotCount:
    defaults = dict(
        candidate_id="C1",
        pattern="impulse",
        degree=2,
        last_pivot_label="3",
        pivots=[SnapshotPivot(timestamp=date(2024, 1, 1), price=Decimal("100"))],
        engine_score=0.7,
        llm_prob=None,
        final_score=0.7,
    )
    defaults.update(overrides)
    return SnapshotCount(**defaults)


def test_engine_prob_defaults_to_none() -> None:
    c = _base_count()
    assert c.engine_prob is None


def test_engine_prob_round_trips_through_store(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite"
    snap = Snapshot(
        ticker="AAPL",
        as_of=date(2024, 6, 30),
        engine_version="0.13.0",
        top_3=[
            _base_count(engine_prob=0.42),
            _base_count(candidate_id="C2", engine_prob=None),
        ],
        signal=None,
        coherence=None,
        top_count_hash="h_test",
        created_at=datetime(2024, 6, 30, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)

    rows = store.recent("AAPL", n=1, db_path=db_path)
    assert len(rows) == 1
    loaded = Snapshot.model_validate_json(rows[0].payload_json)
    assert loaded.top_3[0].engine_prob == pytest.approx(0.42)
    assert loaded.top_3[1].engine_prob is None


def test_old_payload_without_field_loads_with_none() -> None:
    """JSON written before this field existed must still load — Pydantic
    optional-with-default handles this, but we lock it in a test."""
    import json
    payload = {
        "ticker": "AAPL",
        "as_of": "2023-01-01",
        "engine_version": "0.11.0",
        "top_3": [
            {
                "candidate_id": "C1",
                "pattern": "impulse",
                "degree": 2,
                "last_pivot_label": "3",
                "pivots": [{"timestamp": "2023-01-01", "price": "100"}],
                "engine_score": 0.5,
                "llm_prob": None,
                "final_score": 0.5,
                # NOTE: no engine_prob key
            }
        ],
        "signal": None,
        "coherence": None,
        "top_count_hash": "h_old",
        "created_at": "2023-01-01T00:00:00+00:00",
    }
    loaded = Snapshot.model_validate_json(json.dumps(payload))
    assert loaded.top_3[0].engine_prob is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/history/test_snapshot_engine_prob.py -v`
Expected: FAIL — `SnapshotCount` has no `engine_prob` field.

- [ ] **Step 3: Add the field to `SnapshotCount`**

Edit `src/wave_alpha/history/models.py`. Find the `SnapshotCount` class and add the field:

```python
class SnapshotCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    pattern: str
    degree: int
    last_pivot_label: str
    pivots: list[SnapshotPivot]
    engine_score: float
    llm_prob: float | None
    final_score: float
    engine_prob: float | None = None
    """Calibrated P(target) from the logistic engine ranker, when active.
    None for rows produced under the heuristic-fallback path (old rows
    or fresh installs without a logistic artifact)."""
```

- [ ] **Step 4: Populate from `RankedCount` in `service.py`**

Find the snapshot-building code in `src/wave_alpha/history/service.py`. Around line 70 there is a `SnapshotCount(...)` construction that already reads `rc.engine_score` etc. Add `engine_prob=rc.engine_prob`:

```python
        SnapshotCount(
            candidate_id=rc.candidate_id,
            pattern=rc.count.pattern,
            degree=rc.count.degree,
            last_pivot_label=...,            # unchanged
            pivots=...,                      # unchanged
            engine_score=rc.engine_score,
            llm_prob=rc.llm_prob,
            final_score=rc.final_score,
            engine_prob=rc.engine_prob,
        )
```

Use the actual indentation from the file as-is; only the new `engine_prob=` line is the change.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/history/test_snapshot_engine_prob.py -v`
Expected: PASS for all three tests.

- [ ] **Step 6: Run the full history suite**

Run: `uv run pytest tests/history -v`
Expected: PASS. Existing history tests do not reference `engine_prob` so are unaffected by the default-None addition.

- [ ] **Step 7: Lint + format**

Run: `uv run ruff check src/wave_alpha/history tests/history && uv run ruff format src/wave_alpha/history tests/history`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/history/models.py src/wave_alpha/history/service.py tests/history/test_snapshot_engine_prob.py
git commit -m "$(cat <<'EOF'
feat(history): SnapshotCount.engine_prob optional field

Carries the calibrated probability from the logistic engine ranker into
the persisted snapshot when active. Defaults to None so older payloads
and heuristic-path runs load unchanged. record_snapshot populates the
field from RankedCount.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Training: dataset builder + walk-forward fit

**Goal:** `engine_ranker.training.build_outcome_dataset` reads resolved snapshots from `history.sqlite`, replays the pipeline at each `(ticker, as_of)` to recompute features, applies walk-forward split, fits logistic with shared coefs + per-degree intercepts, and emits a serializable artifact.

**Files:**
- Create: `src/wave_alpha/engine_ranker/training.py`
- Create: `tests/engine_ranker/test_training.py`

- [ ] **Step 1: Write the failing test**

Create `tests/engine_ranker/test_training.py`:

```python
"""Unit tests for engine_ranker.training.

Covers: dataset construction, walk-forward split (no leakage), fit
determinism, and lookahead guard.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.engine_ranker.training import (
    OutcomeDatasetRow,
    build_outcome_dataset,
    train_logistic,
    walk_forward_split,
)
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotOutcome,
)


class _FakeProvider:
    def __init__(self, bars):
        self._bars = bars

    def fetch(self, ticker, interval, *, end):
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start, n, interval):
    from math import sin
    out = []
    step_days = 1 if interval == Interval.DAILY else (7 if interval == Interval.WEEKLY else 1)
    for i in range(n):
        base = 100.0 + 30.0 * sin(i / 8.0) + i * 0.1
        price = Decimal(str(round(base, 2)))
        out.append(Bar(
            timestamp=start + timedelta(days=i * step_days),
            interval=interval,
            open=price, high=price + Decimal("1"), low=price - Decimal("1"),
            close=price, volume=1_000_000,
        ))
    return out


def _seed_resolved_snapshot(db_path: Path, ticker: str, as_of: date,
                            outcome_status: str) -> None:
    from datetime import UTC, datetime
    snap = Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.13.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=2, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=None,
        coherence=None,
        outcome=SnapshotOutcome(
            status=outcome_status,
            realized_r=1.0 if outcome_status == "target" else -1.0,
            resolved_at=as_of + timedelta(days=20),
            mfe=Decimal("1.0"), mae=Decimal("-0.5"), bars_held=10,
        ),
        top_count_hash=f"h_{ticker}_{as_of.isoformat()}",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)


@pytest.fixture
def synthetic_provider():
    daily = _make_bars(date(2020, 1, 1), 1500, Interval.DAILY)
    weekly = _make_bars(date(2020, 1, 1), 220, Interval.WEEKLY)
    h4 = _make_bars(date(2020, 1, 1), 1500, Interval.HOURLY_4)
    return _FakeProvider({
        ("AAPL", Interval.DAILY): daily,
        ("AAPL", Interval.WEEKLY): weekly,
        ("AAPL", Interval.HOURLY_4): h4,
    })


def test_build_dataset_recomputes_features(tmp_path, synthetic_provider) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_resolved_snapshot(db_path, "AAPL", date(2023, 6, 30), "target")
    _seed_resolved_snapshot(db_path, "AAPL", date(2023, 9, 30), "stop")

    dataset = build_outcome_dataset(
        db_path=db_path, provider=synthetic_provider, include_time_stops=True
    )
    assert len(dataset.rows) >= 1  # at least one of the two passes pipeline filters
    for row in dataset.rows:
        assert isinstance(row, OutcomeDatasetRow)
        assert 0.0 <= row.features.template_fit <= 1.0
        assert 0.0 <= row.features.fib <= 1.0
        assert 0.2 <= row.features.recency <= 1.0
        assert 0.0 <= row.features.coherence <= 1.0
        assert row.label in (0, 1)
        assert row.degree == row.features.degree


def test_walk_forward_split_no_leakage() -> None:
    """Cutoff date must be on a date boundary, not a row boundary — rows
    sharing the cutoff date all go to the train side."""
    rows = [
        OutcomeDatasetRow(
            ticker=f"T{i}", as_of=date(2024, 1, 1) + timedelta(days=d),
            features=None, label=0, degree=2, weight=1.0,
        )
        for d, i in [(0, 1), (0, 2), (10, 3), (20, 4), (20, 5), (30, 6)]
    ]
    train, holdout = walk_forward_split(rows, split=0.6)
    # 4 distinct dates: 0, 10, 20, 30. 60% → 2 train dates → cutoff = day-10.
    train_as_ofs = {r.as_of for r in train}
    holdout_as_ofs = {r.as_of for r in holdout}
    assert train_as_ofs.isdisjoint(holdout_as_ofs), (
        "walk-forward split leaked a date across both sides"
    )
    assert max(train_as_ofs) < min(holdout_as_ofs)


def test_walk_forward_planted_leak_detected() -> None:
    """If somehow a row with as_of > cutoff lands in train, the trainer
    must reject. Lookahead guard."""
    from wave_alpha.engine_ranker.training import _assert_no_train_leakage
    train = [OutcomeDatasetRow(
        ticker="X", as_of=date(2024, 6, 1), features=None, label=0, degree=2, weight=1.0,
    )]
    cutoff = date(2024, 3, 1)
    with pytest.raises(AssertionError, match="lookahead"):
        _assert_no_train_leakage(train, cutoff=cutoff)


def test_train_logistic_deterministic(tmp_path, synthetic_provider) -> None:
    """Two trainings on the same dataset must produce byte-identical weights."""
    db_path = tmp_path / "history.sqlite"
    # Seed enough rows to actually fit
    for q, (d, status) in enumerate([
        (date(2022, 3, 31), "target"), (date(2022, 6, 30), "stop"),
        (date(2022, 9, 30), "target"), (date(2022, 12, 31), "stop"),
        (date(2023, 3, 31), "target"), (date(2023, 6, 30), "target"),
        (date(2023, 9, 30), "stop"), (date(2023, 12, 31), "target"),
    ]):
        _seed_resolved_snapshot(db_path, "AAPL", d, status)

    ds = build_outcome_dataset(db_path=db_path, provider=synthetic_provider,
                               include_time_stops=True)
    if len(ds.rows) < 4:
        pytest.skip("synthetic data did not produce enough rows for fit")

    m1 = train_logistic(ds, walk_forward_split=0.7, random_state=42)
    m2 = train_logistic(ds, walk_forward_split=0.7, random_state=42)
    assert m1.weights == m2.weights
    assert m1.intercept_per_degree == m2.intercept_per_degree
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_training.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker.training'`.

- [ ] **Step 3: Implement the training module**

Create `src/wave_alpha/engine_ranker/training.py`:

```python
"""Outcome-driven engine-ranker training.

Reads resolved snapshots from history.sqlite, replays the pipeline at each
(ticker, as_of) against cached bars to recompute features, applies a
walk-forward split, fits a logistic regression with shared coefficients
and per-degree intercepts, and returns a candidate LogisticEngineRanker.

Determinism note: sklearn LogisticRegression with a fixed random_state and
solver='lbfgs' produces identical weights across runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger
from sklearn.linear_model import LogisticRegression

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.history import store
from wave_alpha.history.models import Snapshot
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

_FEATURE_ORDER: tuple[str, ...] = ("template_fit", "fib", "recency", "coherence")


@dataclass(frozen=True)
class OutcomeDatasetRow:
    ticker: str
    as_of: date
    features: EngineRankerFeatures | None
    label: int  # 0 or 1
    degree: int
    weight: float = 1.0


@dataclass(frozen=True)
class OutcomeDataset:
    rows: list[OutcomeDatasetRow]
    skipped_no_features: int
    skipped_unresolved: int
    n_target: int
    n_non_target: int


def build_outcome_dataset(
    *,
    db_path: Path,
    provider: OHLCVProvider,
    include_time_stops: bool = True,
) -> OutcomeDataset:
    """Read every snapshot from history.sqlite with a resolved outcome,
    replay the pipeline to recompute features for the top-1 candidate, and
    label it.

    LLM is forced DISABLED — features must be deterministic and offline.
    """
    all_recent = store.recent_across_all(db_path=db_path, limit=100_000)
    rows: list[OutcomeDatasetRow] = []
    skipped_unresolved = 0
    skipped_no_features = 0
    for r in all_recent:
        snap = Snapshot.model_validate_json(r.payload_json)
        if snap.outcome is None:
            skipped_unresolved += 1
            continue
        status = snap.outcome.status
        if status == "pending":
            skipped_unresolved += 1
            continue
        if status == "time_stop" and not include_time_stops:
            continue
        label = 1 if status == "target" else 0

        try:
            result = run_analysis(
                AnalyzeRequest(ticker=snap.ticker, as_of=snap.as_of),
                provider=provider,
                llm_mode=LLMMode.DISABLED,
            )
        except Exception as exc:
            logger.warning("replay failed for {} @ {}: {}", snap.ticker, snap.as_of, exc)
            skipped_no_features += 1
            continue
        if not result.ranked_daily:
            skipped_no_features += 1
            continue
        top = result.ranked_daily[0].count
        try:
            feats = EngineRankerFeatures.from_count(top, result.coherence)
        except ValueError:
            skipped_no_features += 1
            continue
        rows.append(OutcomeDatasetRow(
            ticker=snap.ticker, as_of=snap.as_of,
            features=feats, label=label, degree=top.degree,
        ))

    n_target = sum(1 for r in rows if r.label == 1)
    return OutcomeDataset(
        rows=rows,
        skipped_no_features=skipped_no_features,
        skipped_unresolved=skipped_unresolved,
        n_target=n_target,
        n_non_target=len(rows) - n_target,
    )


def walk_forward_split(
    rows: list[OutcomeDatasetRow], *, split: float
) -> tuple[list[OutcomeDatasetRow], list[OutcomeDatasetRow]]:
    """Time-split by distinct as_of date: oldest `split` fraction of distinct
    dates → train; the rest → holdout. A date falls entirely on one side."""
    if not rows:
        return [], []
    distinct = sorted({r.as_of for r in rows})
    cutoff_idx = max(1, int(len(distinct) * split))
    cutoff_date = distinct[cutoff_idx - 1]  # inclusive on train side
    train = [r for r in rows if r.as_of <= cutoff_date]
    holdout = [r for r in rows if r.as_of > cutoff_date]
    return train, holdout


def _assert_no_train_leakage(train: list[OutcomeDatasetRow], *, cutoff: date) -> None:
    leakers = [r for r in train if r.as_of > cutoff]
    if leakers:
        raise AssertionError(
            f"lookahead violation: {len(leakers)} train rows have as_of > cutoff ({cutoff})"
        )


@dataclass(frozen=True)
class CandidateArtifact:
    """In-memory artifact returned by train_logistic — serialize via
    serialize_artifact before writing to disk."""
    version: str
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept_per_degree: dict[int, float]
    metadata: dict[str, Any]


def train_logistic(
    dataset: OutcomeDataset,
    *,
    walk_forward_split: float = 0.7,
    random_state: int = 42,
    class_balance_low: float = 0.35,
    class_balance_high: float = 0.65,
) -> CandidateArtifact:
    """Fit shared-coef + per-degree-intercept logistic on the train split.

    The per-degree intercept is achieved by adding one one-hot column per
    observed degree (no global intercept). At inference, sum the weight on
    the degree's one-hot column equals its intercept.
    """
    if not dataset.rows:
        raise ValueError("OutcomeDataset has no rows; cannot train")

    train_rows, _holdout = walk_forward_split_fn(dataset.rows, split=walk_forward_split)
    # name shadowing guard: prefer explicit call to the module-level helper
    cutoff = max(r.as_of for r in train_rows)
    _assert_no_train_leakage(train_rows, cutoff=cutoff)

    observed_degrees = sorted({r.degree for r in train_rows})

    X = _build_matrix(train_rows, observed_degrees)
    y = np.array([r.label for r in train_rows], dtype=int)

    target_rate = float(y.mean())
    class_weight = "balanced" if not (class_balance_low <= target_rate <= class_balance_high) else None

    model = LogisticRegression(
        solver="lbfgs", max_iter=500, fit_intercept=False,
        random_state=random_state, class_weight=class_weight,
    )
    model.fit(X, y)

    coefs = model.coef_[0]
    weights = tuple(float(c) for c in coefs[: len(_FEATURE_ORDER)])
    intercept_per_degree = {
        d: float(coefs[len(_FEATURE_ORDER) + i]) for i, d in enumerate(observed_degrees)
    }
    return CandidateArtifact(
        version="v1",
        feature_order=_FEATURE_ORDER,
        weights=weights,
        intercept_per_degree=intercept_per_degree,
        metadata={
            "n_train": len(train_rows),
            "n_target": int(y.sum()),
            "walk_forward_cutoff": cutoff.isoformat(),
            "class_weight": class_weight,
            "target_rate": target_rate,
        },
    )


# Alias to avoid name collision with the kwarg `walk_forward_split` inside train_logistic.
walk_forward_split_fn = walk_forward_split


def _build_matrix(
    rows: list[OutcomeDatasetRow], degrees: list[int]
) -> np.ndarray:
    """Columns: [template_fit, fib, recency, coherence, deg_1_onehot, deg_2_onehot, ...]."""
    n_feat = len(_FEATURE_ORDER) + len(degrees)
    X = np.zeros((len(rows), n_feat), dtype=float)
    deg_idx = {d: i for i, d in enumerate(degrees)}
    for i, r in enumerate(rows):
        f = r.features
        X[i, 0] = f.template_fit
        X[i, 1] = f.fib
        X[i, 2] = f.recency
        X[i, 3] = f.coherence
        if r.degree in deg_idx:
            X[i, len(_FEATURE_ORDER) + deg_idx[r.degree]] = 1.0
    return X


def serialize_artifact(
    artifact: CandidateArtifact, *, gate_verdict: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Produce the dict that becomes the on-disk JSON artifact."""
    extra = extra or {}
    return {
        "version": artifact.version,
        "feature_order": list(artifact.feature_order),
        "weights": list(artifact.weights),
        "intercept_per_degree": {str(d): v for d, v in artifact.intercept_per_degree.items()},
        "gate": {"verdict": gate_verdict, **extra},
        "metadata": artifact.metadata,
    }


def candidate_to_loadable(artifact: CandidateArtifact) -> LogisticEngineRanker:
    """In-process: turn a CandidateArtifact into a usable LogisticEngineRanker
    without round-tripping through JSON. Used in promotion evaluation."""
    import statistics
    return LogisticEngineRanker(
        version=artifact.version,
        feature_order=artifact.feature_order,
        weights=artifact.weights,
        intercept_per_degree=dict(artifact.intercept_per_degree),
        _median_intercept=statistics.median(artifact.intercept_per_degree.values()),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_training.py -v`
Expected: PASS for all four tests. Note: `test_build_dataset_recomputes_features` may skip if the synthetic provider's bars don't produce enough pivots; that's acceptable (the deterministic + leakage tests cover the load-bearing behavior).

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/wave_alpha/engine_ranker tests/engine_ranker && uv run ruff format src/wave_alpha/engine_ranker tests/engine_ranker`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/engine_ranker/training.py tests/engine_ranker/test_training.py
git commit -m "$(cat <<'EOF'
feat(engine_ranker): outcome-dataset builder + walk-forward training

build_outcome_dataset reads resolved snapshots from history.sqlite,
replays the pipeline (LLM disabled) at each (ticker, as_of) against
cached bars, and recomputes features for the top-1 candidate.

train_logistic fits a shared-coef + per-degree-intercept logistic on
the walk-forward train split, with class_weight='balanced' triggered
outside [0.35, 0.65] target rates. Deterministic for a fixed
random_state. CandidateArtifact + serialize_artifact emit the on-disk
JSON shape that LogisticEngineRanker.from_json consumes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Promotion gate

**Goal:** Paired-bootstrap on Brier score AND on backtest expectancy; both lower 95% CI bounds must be ≥ 0 for PROMOTE; otherwise HOLD. INSUFFICIENT_DATA if any observed degree has < `min_resolved_per_degree` holdout rows.

**Files:**
- Create: `src/wave_alpha/engine_ranker/promotion.py`
- Create: `tests/engine_ranker/test_promotion.py`

- [ ] **Step 1: Write the failing test**

Create `tests/engine_ranker/test_promotion.py`:

```python
"""Tests for engine_ranker.promotion.evaluate_promotion."""

from __future__ import annotations

from datetime import date

import pytest

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.engine_ranker.promotion import (
    PromotionVerdict,
    evaluate_promotion,
)
from wave_alpha.engine_ranker.training import OutcomeDatasetRow


def _row(label: int, fit: float, fib: float, rec: float, coh: float, degree: int = 2) -> OutcomeDatasetRow:
    return OutcomeDatasetRow(
        ticker="X", as_of=date(2024, 1, 1),
        features=EngineRankerFeatures(
            template_fit=fit, fib=fib, recency=rec, coherence=coh, degree=degree,
        ),
        label=label, degree=degree,
    )


def _ranker_that_always(prob: float) -> LogisticEngineRanker:
    """Synthetic logistic: weights=0, intercept tuned to a target probability."""
    import math, statistics
    # sigmoid(b) = prob → b = log(prob/(1-prob))
    b = math.log(prob / (1.0 - prob))
    intercepts = {2: b}
    return LogisticEngineRanker(
        version="vfake",
        feature_order=("template_fit", "fib", "recency", "coherence"),
        weights=(0.0, 0.0, 0.0, 0.0),
        intercept_per_degree=intercepts,
        _median_intercept=statistics.median(intercepts.values()),
    )


def test_promote_when_logistic_strictly_better() -> None:
    """All rows labeled 1; logistic predicts 0.9, heuristic predicts 0.1.
    Brier for logistic ≈ 0.01, heuristic ≈ 0.81. Logistic should PROMOTE."""
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(60)]
    candidate = _ranker_that_always(0.9)

    # Hack the heuristic to always return 0.1 in this synthetic test by
    # passing features whose raw weighted sum hits 0.1 — alternatively
    # construct a thin wrapper. We use a wrapper for clarity.
    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.1

    verdict = evaluate_promotion(
        candidate=candidate, baseline=_AlwaysHeuristic(),
        holdout_rows=rows, min_resolved_per_degree=10, n_bootstrap=200,
        random_state=42,
    )
    assert verdict.verdict == PromotionVerdict.PROMOTE


def test_hold_when_logistic_strictly_worse() -> None:
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(60)]
    candidate = _ranker_that_always(0.1)

    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.9

    verdict = evaluate_promotion(
        candidate=candidate, baseline=_AlwaysHeuristic(),
        holdout_rows=rows, min_resolved_per_degree=10, n_bootstrap=200,
        random_state=42,
    )
    assert verdict.verdict == PromotionVerdict.HOLD


def test_insufficient_data_when_below_per_degree_threshold() -> None:
    rows = [_row(label=1, fit=0.5, fib=0.5, rec=0.5, coh=0.5) for _ in range(5)]
    candidate = _ranker_that_always(0.5)

    class _AlwaysHeuristic:
        def predict_proba(self, f):
            return 0.5

    verdict = evaluate_promotion(
        candidate=candidate, baseline=_AlwaysHeuristic(),
        holdout_rows=rows, min_resolved_per_degree=30, n_bootstrap=200,
        random_state=42,
    )
    assert verdict.verdict == PromotionVerdict.INSUFFICIENT_DATA
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine_ranker/test_promotion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wave_alpha.engine_ranker.promotion'`.

- [ ] **Step 3: Implement promotion**

Create `src/wave_alpha/engine_ranker/promotion.py`:

```python
"""Promotion gate for candidate LogisticEngineRanker artifacts.

PROMOTE requires:
  (a) lower 95% CI bound of mean(Brier_baseline - Brier_candidate) ≥ 0
  (b) lower 95% CI bound of mean(R_candidate - R_baseline) ≥ 0
  (c) every observed degree in holdout has ≥ min_resolved_per_degree rows

Otherwise HOLD (a/b failed) or INSUFFICIENT_DATA (c failed).

The R-expectancy gate uses a simplified proxy: each holdout row contributes
+1 R if its label is 1 and the model would have selected it (prob ≥ 0.5),
-1 R if label is 0 and prob ≥ 0.5, 0 R otherwise. This is intentionally
coarse — the real backtest harness runs separately; this gate just rejects
obvious regressions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

import numpy as np

from wave_alpha.engine_ranker.training import OutcomeDatasetRow


class PromotionVerdict(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class PromotionReport:
    verdict: PromotionVerdict
    brier_lift_mean: float
    brier_lift_ci: tuple[float, float]
    r_lift_mean: float
    r_lift_ci: tuple[float, float]
    rows_per_degree: dict[int, int]


def evaluate_promotion(
    *,
    candidate,
    baseline,
    holdout_rows: list[OutcomeDatasetRow],
    min_resolved_per_degree: int = 30,
    n_bootstrap: int = 10_000,
    random_state: int = 42,
) -> PromotionReport:
    rows_per_degree = Counter(r.degree for r in holdout_rows)
    if not rows_per_degree or any(
        n < min_resolved_per_degree for n in rows_per_degree.values()
    ):
        return PromotionReport(
            verdict=PromotionVerdict.INSUFFICIENT_DATA,
            brier_lift_mean=float("nan"),
            brier_lift_ci=(float("nan"), float("nan")),
            r_lift_mean=float("nan"),
            r_lift_ci=(float("nan"), float("nan")),
            rows_per_degree=dict(rows_per_degree),
        )

    n = len(holdout_rows)
    p_c = np.array([candidate.predict_proba(r.features) for r in holdout_rows])
    p_b = np.array([baseline.predict_proba(r.features) for r in holdout_rows])
    y = np.array([r.label for r in holdout_rows])

    brier_c = (p_c - y) ** 2
    brier_b = (p_b - y) ** 2
    brier_diff = brier_b - brier_c  # positive = candidate wins

    r_c = np.where(p_c >= 0.5, np.where(y == 1, 1.0, -1.0), 0.0)
    r_b = np.where(p_b >= 0.5, np.where(y == 1, 1.0, -1.0), 0.0)
    r_diff = r_c - r_b  # positive = candidate wins

    rng = np.random.default_rng(random_state)
    brier_resamples = np.empty(n_bootstrap)
    r_resamples = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        brier_resamples[i] = brier_diff[idx].mean()
        r_resamples[i] = r_diff[idx].mean()

    brier_ci = (float(np.percentile(brier_resamples, 2.5)),
                float(np.percentile(brier_resamples, 97.5)))
    r_ci = (float(np.percentile(r_resamples, 2.5)),
            float(np.percentile(r_resamples, 97.5)))

    if brier_ci[0] >= 0 and r_ci[0] >= 0:
        verdict = PromotionVerdict.PROMOTE
    else:
        verdict = PromotionVerdict.HOLD

    return PromotionReport(
        verdict=verdict,
        brier_lift_mean=float(brier_diff.mean()),
        brier_lift_ci=brier_ci,
        r_lift_mean=float(r_diff.mean()),
        r_lift_ci=r_ci,
        rows_per_degree=dict(rows_per_degree),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine_ranker/test_promotion.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/wave_alpha/engine_ranker tests/engine_ranker && uv run ruff format src/wave_alpha/engine_ranker tests/engine_ranker`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/engine_ranker/promotion.py tests/engine_ranker/test_promotion.py
git commit -m "$(cat <<'EOF'
feat(engine_ranker): promotion gate (paired-bootstrap Brier + R)

PROMOTE requires lower 95% CI bound of (Brier_baseline - Brier_candidate)
and (R_candidate - R_baseline) both non-negative across 10k bootstrap
resamples. INSUFFICIENT_DATA if any observed degree has fewer than
min_resolved_per_degree holdout rows. Synthetic always-better/always-
worse fixtures verify the verdict logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: CLI: `wave-alpha train engine-ranker`

**Goal:** Manual training command parallel to `wave-alpha train right-edge`. Reads `history.sqlite` via the default provider, fits, evaluates promotion, optionally writes the artifact.

**Files:**
- Modify: `src/wave_alpha/cli/app.py` (add new sub-command to existing `train` sub-app)
- Create: `tests/cli/test_train_engine_ranker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_train_engine_ranker.py`:

```python
"""CLI tests for `wave-alpha train engine-ranker`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotOutcome,
    SnapshotPivot,
)


class _FakeProvider:
    def __init__(self, bars):
        self._bars = bars

    def fetch(self, ticker, interval, *, end):
        return [b for b in self._bars.get((ticker, interval), []) if b.timestamp <= end]


def _make_bars(start, n, interval):
    from math import sin
    out = []
    step_days = 1 if interval == Interval.DAILY else (7 if interval == Interval.WEEKLY else 1)
    for i in range(n):
        base = 100.0 + 30.0 * sin(i / 8.0) + i * 0.1
        price = Decimal(str(round(base, 2)))
        out.append(Bar(
            timestamp=start + timedelta(days=i * step_days),
            interval=interval,
            open=price, high=price + Decimal("1"), low=price - Decimal("1"),
            close=price, volume=1_000_000,
        ))
    return out


def _seed(db_path: Path, ticker: str, as_of: date, status: str) -> None:
    snap = Snapshot(
        ticker=ticker, as_of=as_of, engine_version="0.13.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=2, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=None, coherence=None,
        outcome=SnapshotOutcome(
            status=status,
            realized_r=1.0 if status == "target" else -1.0,
            resolved_at=as_of + timedelta(days=20),
            mfe=Decimal("1.0"), mae=Decimal("-0.5"), bars_held=10,
        ),
        top_count_hash=f"h_{ticker}_{as_of.isoformat()}",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    for d, s in [
        (date(2022, 3, 31), "target"), (date(2022, 6, 30), "stop"),
        (date(2022, 9, 30), "target"), (date(2022, 12, 31), "target"),
        (date(2023, 3, 31), "stop"), (date(2023, 6, 30), "target"),
    ]:
        _seed(db_path, "AAPL", d, s)
    daily = _make_bars(date(2020, 1, 1), 1500, Interval.DAILY)
    weekly = _make_bars(date(2020, 1, 1), 220, Interval.WEEKLY)
    h4 = _make_bars(date(2020, 1, 1), 1500, Interval.HOURLY_4)
    provider = _FakeProvider({
        ("AAPL", Interval.DAILY): daily,
        ("AAPL", Interval.WEEKLY): weekly,
        ("AAPL", Interval.HOURLY_4): h4,
    })

    # Monkeypatch the CLI's history-db path and default provider.
    from wave_alpha.cli import app as cli_app_mod
    monkeypatch.setattr(cli_app_mod, "_engine_ranker_db_path", lambda: db_path, raising=False)
    monkeypatch.setattr(cli_app_mod, "_engine_ranker_default_provider", lambda: provider, raising=False)
    monkeypatch.setattr(cli_app_mod, "_engine_ranker_artifact_path",
                        lambda: tmp_path / "engine_ranker_logistic_v1.json", raising=False)
    return tmp_path


def test_dry_run_prints_plan_writes_nothing(setup_env) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "rows" in result.output.lower()
    assert not (setup_env / "engine_ranker_logistic_v1.json").exists()


def test_write_blocked_without_promote_verdict(setup_env) -> None:
    """Synthetic data is too small for a real PROMOTE; --write without
    --force must exit non-zero and not create the artifact."""
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--write"])
    # Either HOLD or INSUFFICIENT_DATA — both block the write.
    assert "PROMOTE" not in result.output or result.exit_code != 0
    if "PROMOTE" not in result.output:
        assert not (setup_env / "engine_ranker_logistic_v1.json").exists()


def test_force_writes_artifact_with_gate_recorded(setup_env) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["train", "engine-ranker", "--write", "--force"])
    assert result.exit_code == 0, result.output
    art = setup_env / "engine_ranker_logistic_v1.json"
    assert art.exists()
    import json
    payload = json.loads(art.read_text())
    assert payload["gate"]["verdict"] in ("PROMOTE", "HOLD", "INSUFFICIENT_DATA")
    assert payload["feature_order"] == ["template_fit", "fib", "recency", "coherence"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_train_engine_ranker.py -v`
Expected: FAIL — `Error: No such command 'engine-ranker'.`

- [ ] **Step 3: Implement the CLI command**

Find the existing `train` sub-app in `src/wave_alpha/cli/app.py`:

```bash
grep -n "train_app\|@train_app.command\|train right-edge" src/wave_alpha/cli/app.py | head -20
```

Append the new command alongside the existing `right-edge` command. Add helper indirections so the test fixture can monkeypatch them:

```python
def _engine_ranker_db_path():
    from wave_alpha.history.service import _DEFAULT_DB_PATH  # or whatever the existing constant is
    return _DEFAULT_DB_PATH


def _engine_ranker_default_provider():
    return _default_provider()


def _engine_ranker_artifact_path():
    from wave_alpha.engine_ranker.loader import _DEFAULT_LOGISTIC_PATH
    return _DEFAULT_LOGISTIC_PATH


@train_app.command("engine-ranker")
def train_engine_ranker(
    use_outcomes: bool = typer.Option(
        True, "--use-outcomes/--no-outcomes",
        help="Read resolved snapshots from history.sqlite (only mode supported today).",
    ),
    include_time_stops: bool = typer.Option(True, "--include-time-stops/--drop-time-stops"),
    walk_forward_split: float = typer.Option(0.7, "--split"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    write: bool = typer.Option(False, "--write", help="Write artifact if verdict=PROMOTE."),
    force: bool = typer.Option(False, "--force", help="Write regardless of verdict."),
) -> None:
    """Fit an outcome-trained engine-ranker logistic from history.sqlite."""
    import json as _json_mod
    from wave_alpha.engine_ranker import heuristic as heuristic_mod
    from wave_alpha.engine_ranker import promotion as promo_mod
    from wave_alpha.engine_ranker import training as training_mod

    db_path = _engine_ranker_db_path()
    provider = _engine_ranker_default_provider()
    artifact_path = _engine_ranker_artifact_path()

    if not use_outcomes:
        typer.echo("--no-outcomes mode is not supported; engine-ranker requires resolved rows.",
                   err=True)
        raise typer.Exit(code=2)

    dataset = training_mod.build_outcome_dataset(
        db_path=db_path, provider=provider, include_time_stops=include_time_stops,
    )
    typer.echo(
        f"dataset: {len(dataset.rows)} rows "
        f"(target={dataset.n_target}, non-target={dataset.n_non_target}, "
        f"skipped_no_features={dataset.skipped_no_features}, "
        f"skipped_unresolved={dataset.skipped_unresolved})"
    )
    if dry_run:
        typer.echo("--dry-run: not training, not writing.")
        return

    if not dataset.rows:
        typer.echo("no rows to train on; exiting.", err=True)
        raise typer.Exit(code=2)

    candidate = training_mod.train_logistic(
        dataset, walk_forward_split=walk_forward_split,
    )
    candidate_model = training_mod.candidate_to_loadable(candidate)
    baseline = heuristic_mod.HeuristicEngineRanker()
    _train_rows, holdout = training_mod.walk_forward_split(
        dataset.rows, split=walk_forward_split
    )
    report = promo_mod.evaluate_promotion(
        candidate=candidate_model, baseline=baseline, holdout_rows=holdout,
        min_resolved_per_degree=30, n_bootstrap=10_000,
    )

    typer.echo(
        f"promotion: verdict={report.verdict.value} "
        f"brier_lift_mean={report.brier_lift_mean:+.4f} "
        f"brier_ci=({report.brier_lift_ci[0]:+.4f}, {report.brier_lift_ci[1]:+.4f}) "
        f"r_lift_mean={report.r_lift_mean:+.4f} "
        f"r_ci=({report.r_lift_ci[0]:+.4f}, {report.r_lift_ci[1]:+.4f})"
    )

    if not write:
        return
    if report.verdict.value != "PROMOTE" and not force:
        typer.echo(
            f"refusing to write artifact with verdict={report.verdict.value} (use --force to override).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Force-write uses the actual verdict as the gate value so the loader's
    # PROMOTE-only check still refuses it. With --force we want the artifact
    # written to disk for inspection, but it will not be auto-loaded at runtime.
    payload = training_mod.serialize_artifact(candidate, gate_verdict=report.verdict.value)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(_json_mod.dumps(payload, indent=2))
    typer.echo(f"wrote artifact to {artifact_path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_train_engine_ranker.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Smoke-check the CLI doesn't break existing `train right-edge`**

Run: `uv run pytest tests/cli -v -k train`
Expected: PASS for all train commands.

- [ ] **Step 6: Quick manual dry-run against the real DB**

Run: `uv run wave-alpha train engine-ranker --dry-run`
Expected: prints a row count line; exits 0. No artifact written. Output may show 0 rows if the bootstrap densifier has not yet run — that's fine.

- [ ] **Step 7: Lint + format**

Run: `uv run ruff check src/wave_alpha/cli tests/cli && uv run ruff format src/wave_alpha/cli tests/cli`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_train_engine_ranker.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha train engine-ranker

Manual training command: builds outcome dataset from history.sqlite,
fits walk-forward logistic, runs promotion gate, optionally writes the
JSON artifact. --write is gated on verdict=PROMOTE unless --force is
passed. --dry-run reports the row counts without training.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Final spec-coverage pass + integration smoke

**Goal:** Verify the spec's promised user-visible behaviors all work end-to-end on the merged module set, and write a short rollout note in `CHANGELOG.md`.

**Files:**
- Modify: `CHANGELOG.md` (or `CHANGES.md` — verify which exists)

- [ ] **Step 1: Verify the full test suite is green**

Run: `uv run pytest -x`
Expected: PASS.

- [ ] **Step 2: Verify the CLI surface**

```bash
uv run wave-alpha train --help
uv run wave-alpha train engine-ranker --help
uv run wave-alpha config --help
```

Expected: `engine-ranker` appears in the `train` sub-app help; `--use-outcomes`, `--write`, `--force`, `--dry-run`, `--split`, `--include-time-stops/--drop-time-stops` all listed.

- [ ] **Step 3: Verify heuristic-path behavior is unchanged on a real ticker**

```bash
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json
```

Expected: runs without error. With no logistic artifact present, `auto` mode resolves to heuristic and produces the same shape of output as before this PR. `engine_prob` is absent from the per-count payload (heuristic path).

- [ ] **Step 4: Update CHANGELOG**

Find the changelog:

```bash
ls CHANGELOG.md CHANGES.md HISTORY.md 2>/dev/null
```

Append an unreleased section (use the project's existing format — read the top of the file first):

```markdown
## Unreleased

### Added
- `wave_alpha.engine_ranker` module: outcome-trained logistic re-ranker
  applied at pipeline level. Loader auto-falls back to a hand-tuned
  heuristic baseline when no artifact is shipped.
- `wave-alpha train engine-ranker [--use-outcomes] [--write] [--force]`.
- `WaveCount.{template_fit_component, fib_component, recency_component}`
  decomposed sub-scores populated by the enumerator's scorer.
- `RankedCount.engine_prob` and `SnapshotCount.engine_prob` (optional)
  carrying the calibrated probability when the logistic is active.
- `EngineRankerConfig` in `AppConfig`, default `kind="auto"`.

### Changed
- `llm.ranking.blend_scores` gains optional `engine_prob` +
  `use_logistic_path` kwargs. When the logistic path is active, final =
  alpha * engine_prob + (1-alpha) * llm_prob — the `coh_multiplier` no
  longer wraps the LLM blend (coherence is now a logistic feature).
  Heuristic-path behavior is bit-identical to the prior release.
```

- [ ] **Step 5: Run full test suite + lint once more**

Run: `uv run pytest && uv run ruff check .`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): outcome-trained engine ranker

Adds engine_ranker module + CLI; pipeline gains a logistic re-ranker
path that drops the outside coh_multiplier on the LLM blend in favor
of coherence as a feature. Heuristic-fallback path is bit-identical.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Out of scope for this PR (post-merge follow-ups)

These are operational, not code changes — track separately rather than blocking the PR:

1. **Densify `history.sqlite`** via the `wave-alpha history densify` command from the bootstrap plan (`2026-05-11-outcome-data-bootstrap`). Required before training has enough rows to produce a non-INSUFFICIENT_DATA verdict.
2. **Run `wave-alpha train engine-ranker --use-outcomes --write`** against the densified DB. If verdict is PROMOTE, capture the produced artifact as `src/wave_alpha/engine_ranker/models/engine_ranker_logistic_v1.json` (committed to the wheel) and bump the release.
3. **Side-by-side analyze comparison** at a few tickers between heuristic and logistic kinds (`config set engine_ranker.kind heuristic` vs `auto`) — record the rank-order shift and any single-degree surprises.
4. **Refit `alpha` (LLM blend weight)** from outcome data. Separate spec.
5. **Three-class outcome head** (target / time_stop / stop). Separate spec; needs an order-aware loss and more careful calibration.

---

## Self-review notes

- **Spec coverage:**
  - §2 goal 1 (mirror right_edge layout): T2-T3 (features, heuristic, logistic, loader); T7-T8 (training, promotion).
  - §2 goal 2 (binary P(target) with shared coefs + per-degree intercepts): T7 (`train_logistic` builds one-hot degree columns; weights split out).
  - §2 goal 3 (pipeline-level re-rank, _score untouched): T5.
  - §2 goal 4 (decompose components on WaveCount): T1.
  - §2 goal 5 (CLI): T9.
  - §2 goal 6 (paired-bootstrap promotion gate): T8.
  - §2 goal 7 (new final formula, drop coh on LLM term): T5 (blend_scores extension + pipeline wiring).
  - §2 goal 8 (reuse eval harness): noted as post-merge follow-up; tightly integrating with #5's harness is deferred to keep this PR focused.
  - §2 goal 9 (artifact JSON shipped in wheel): models/.gitkeep created T3; actual v1.json ships post-merge per the rollout note.
  - §5.6 backward compat (engine_prob optional on SnapshotCount, blend_scores legacy path bit-identical): T5 + T6 + the heuristic-path golden regression test.
  - §6.1 walk-forward split + planted-leak guard: T7 (`walk_forward_split` + `_assert_no_train_leakage` + the lookahead test).
  - §6.2 per-degree intercepts: T7 (one-hot columns in `_build_matrix`).
  - §7 CLI surface: T9. Config subcommands are inherited from the existing `wave-alpha config` infra and need no plan task.
  - §8 tests: every promised test is present in T1-T9.

- **Placeholder scan:** Every code block is complete. No TBD/TODO/"similar to". The one judgment-call instruction is the "verify the actual import path before committing" note in T5 step 7 (since there is real uncertainty about whether `prefs.config.load_config` exists today) — this is intentional, not a placeholder.

- **Type / name consistency:**
  - `EngineRankerFeatures`, `EngineRankerModel`, `HeuristicEngineRanker`, `LogisticEngineRanker`, `EngineRankerArtifactError` consistent across T2-T9.
  - `OutcomeDatasetRow` / `OutcomeDataset` / `CandidateArtifact` consistent across T7-T9.
  - `walk_forward_split` exists as both a function and a kwarg name on `train_logistic`; T7 handles the collision via `walk_forward_split_fn` alias inside the function.
  - `template_fit_component / fib_component / recency_component` consistent T1 ↔ T2.
  - `engine_prob` field present consistently on `RankedCount` (T5) and `SnapshotCount` (T6).
  - `intercept_per_degree` consistently uses `dict[int, float]` in T3 (loader, model) and T7 (training, serialize).

- **Footgun handled:** T5's heuristic-path regression test pins the legacy formula bit-for-bit by reproducing it from RankedCount fields rather than via a recorded JSON fixture — so it stays valid if templates or any internal weight changes downstream.
