"""Runtime-loadable logistic engine ranker.

Reads a plain JSON artifact produced by training.serialize_artifact.
No scikit-learn at runtime; stdlib + project types only.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from wave_alpha.engine_ranker.features import EngineRankerFeatures

# Engine ranker has no separate calibration step (per-degree intercepts
# subsume the role of right_edge's Platt scalers), so PROMOTE_CALIBRATION
# is not a valid verdict here. Only PROMOTE is accepted.
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
    _median_intercept: float = field(repr=False)  # filled by from_json; fallback for unseen degrees

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
        if features.degree in self.intercept_per_degree:
            b = self.intercept_per_degree[features.degree]
        else:
            logger.warning(
                "engine_ranker: degree {} not in trained intercepts {}, using median {}",
                features.degree,
                sorted(self.intercept_per_degree.keys()),
                self._median_intercept,
            )
            b = self._median_intercept
        z = sum(w * x for w, x in zip(self.weights, v, strict=True)) + b
        return _sigmoid(z)
