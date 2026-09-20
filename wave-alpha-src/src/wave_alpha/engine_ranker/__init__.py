"""Outcome-trained engine ranker.

Mirrors right_edge/ in layout: features → {heuristic, logistic} models →
loader (auto-fallback) → training → promotion. See spec
docs/superpowers/specs/2026-05-11-outcome-trained-engine-ranker-design.md.
"""

from __future__ import annotations

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.loader import EngineRankerModel, load_engine_ranker
from wave_alpha.engine_ranker.logistic import (
    EngineRankerArtifactError,
    LogisticEngineRanker,
)

__all__ = [
    "EngineRankerArtifactError",
    "EngineRankerFeatures",
    "EngineRankerModel",
    "HeuristicEngineRanker",
    "LogisticEngineRanker",
    "load_engine_ranker",
]
