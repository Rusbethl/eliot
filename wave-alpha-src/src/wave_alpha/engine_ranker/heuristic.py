"""Hand-tuned engine-ranker baseline.

Wraps the same WEIGHT_BASE / WEIGHT_CONFLUENCE / WEIGHT_RECENCY linear
combination that enumerator scoring applies. Used as:

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
    WEIGHT_BASE,
    WEIGHT_CONFLUENCE,
    WEIGHT_RECENCY,
)
from wave_alpha.engine_ranker.features import EngineRankerFeatures


@dataclass(frozen=True)
class HeuristicEngineRanker:
    """Reproduces enumerator legacy scoring exactly. Coherence and degree are
    read from the feature vector and intentionally ignored — coherence is
    applied outside the candidate (via blend_scores' coh_multiplier) in the
    heuristic pipeline path, and degree has no role in the hand-tuned formula."""

    version: str = "heuristic_v1"

    def predict_proba(self, features: EngineRankerFeatures) -> float:
        raw = (
            WEIGHT_BASE * features.template_fit
            + WEIGHT_CONFLUENCE * features.fib
            + WEIGHT_RECENCY * features.recency
        )
        return max(0.0, raw)
