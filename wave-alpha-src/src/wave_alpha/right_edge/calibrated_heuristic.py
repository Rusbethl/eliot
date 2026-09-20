# src/wave_alpha/right_edge/calibrated_heuristic.py
"""Runtime-loadable calibrated-heuristic confirmation model.

Wraps ``HeuristicConfirmationModel`` with per-degree Platt scalers fitted via
walk-forward on the same OOS pairs used for the logistic gate.

Smaller change than shipping the logistic: same hand-coded feature weighting,
just calibrated post-hoc so empirical confirmation rates match stated probabilities.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel

# Verdicts that allow this artifact to be loaded for live inference.
_PROMOTE_VERDICTS = frozenset({"PROMOTE", "PROMOTE_CALIBRATION"})

# Module-level heuristic instance — stateless and cheap; avoids re-constructing
# it on every predict_proba call without adding any statefulness to the model.
_heuristic = HeuristicConfirmationModel()


class CalibratedHeuristicArtifactError(ValueError):
    """Raised when a calibrated-heuristic artifact is malformed or has a
    non-promotable verdict."""


@dataclass(frozen=True)
class CalibratedHeuristicModel:
    """Heuristic + per-degree Platt scalers loaded from a JSON artifact.

    Smaller change than the logistic: same hand-coded feature weighting,
    just calibrated post-hoc to match empirical confirmation rates per degree.

    Attributes:
        version: Artifact schema version (e.g. ``"calibrated_heuristic_v1"``).
        heuristic_version: Version string of the underlying heuristic that was
            calibrated (e.g. ``"heuristic_v2"``).
        platt_per_degree: Mapping from degree index to ``(a, b)`` Platt scaler
            parameters.  The calibrated probability is
            ``sigmoid(a * raw_heuristic + b)``.  Degrees absent from this dict
            fall back to the identity scaler ``(a=1.0, b=0.0)``.
    """

    version: str
    heuristic_version: str
    platt_per_degree: dict[int, tuple[float, float]]  # degree → (a, b)

    @classmethod
    def from_json(cls, path: Path) -> CalibratedHeuristicModel:
        """Load a calibrated-heuristic artifact from a JSON file.

        Raises:
            CalibratedHeuristicArtifactError: if the artifact's gate verdict is
                not in ``_PROMOTE_VERDICTS`` or if the ``gate`` key is absent.
        """
        raw = json.loads(path.read_text())
        verdict = raw.get("gate", {}).get("verdict")
        if verdict not in _PROMOTE_VERDICTS:
            raise CalibratedHeuristicArtifactError(
                f"Refusing to load calibrated-heuristic artifact with verdict={verdict!r}"
            )
        return cls(
            version=raw["version"],
            heuristic_version=raw["heuristic_version"],
            platt_per_degree={
                int(d): (float(p["a"]), float(p["b"])) for d, p in raw["platt_per_degree"].items()
            },
        )

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        """Return a calibrated confirmation probability in [0, 1].

        Runs the raw heuristic, then applies the per-degree Platt scaler.
        If ``degree`` is not in ``platt_per_degree`` the identity scaler
        ``(a=1.0, b=0.0)`` is used as a fallback (sigmoid of raw heuristic score).

        Sigmoid inputs are clipped to [-50, 50] to prevent OverflowError on
        pathological calibration weights.
        """
        raw = _heuristic.predict_proba(features)
        a, b = self.platt_per_degree.get(degree, (1.0, 0.0))
        z = max(min(a * raw + b, 50.0), -50.0)
        return 1.0 / (1.0 + math.exp(-z))
