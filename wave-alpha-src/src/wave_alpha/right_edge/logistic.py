"""Runtime-loadable logistic confirmation model.

Reads a plain JSON artifact written by ``training.serialize_artifact``.
Refuses to load any artifact whose gate verdict is not in ``_PROMOTE_VERDICTS``
(currently ``"PROMOTE"`` or ``"PROMOTE_CALIBRATION"``).

No scikit-learn, no pickle — only stdlib + the project's own types.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from wave_alpha.right_edge.features import RightEdgeFeatures


def _sigmoid(z: float) -> float:
    """Numerically safe sigmoid: clips z to [-50, 50] before computing 1/(1+exp(-z)).

    Clipping prevents OverflowError on pathological weights while keeping
    floating-point precision for all values of practical interest.
    """
    z = max(min(z, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-z))


# Verdicts that allow a model artifact to be loaded for live inference.
_PROMOTE_VERDICTS = frozenset({"PROMOTE", "PROMOTE_CALIBRATION"})


class ModelArtifactError(ValueError):
    """Raised when a model artifact is malformed or has a non-promotable verdict."""


@dataclass(frozen=True)
class LogisticConfirmationModel:
    """Calibrated logistic model loaded from a JSON artifact.

    Applies per-degree Platt calibration on top of the raw logistic score.
    Falls back to an identity Platt scaler ``(a=1, b=0)`` for any degree
    not found in ``platt_per_degree``.
    """

    version: str
    feature_order: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float
    platt_per_degree: dict[int, tuple[float, float]]  # degree → (a, b)

    @classmethod
    def from_json(cls, path: Path) -> LogisticConfirmationModel:
        """Load model from a JSON artifact file.

        Validates that all feature names in ``feature_order`` exist on
        ``RightEdgeFeatures`` to prevent silent misalignment at predict time.

        Raises:
            ModelArtifactError: if the artifact's gate verdict is not in
                ``_PROMOTE_VERDICTS`` (``"PROMOTE"`` or ``"PROMOTE_CALIBRATION"``),
                if the ``gate`` key is absent, or if any feature name in
                ``feature_order`` does not exist on ``RightEdgeFeatures``.
        """
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
        """Validate that all feature names exist on RightEdgeFeatures.

        Raises:
            ModelArtifactError: if any name is not a field on RightEdgeFeatures.
        """
        valid = set(RightEdgeFeatures.model_fields.keys())
        unknown = [n for n in names if n not in valid]
        if unknown:
            raise ModelArtifactError(
                f"Artifact feature_order references unknown RightEdgeFeatures "
                f"fields: {unknown!r}. Refusing to load — would silently misalign predictions."
            )

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        """Return a calibrated confirmation probability in [0, 1].

        The raw logistic score is computed from ``_vector(features)``, then
        passed through the per-degree Platt scaler.  If ``degree`` is not
        present in ``platt_per_degree`` the identity scaler ``(a=1, b=0)``
        is used as a fallback.

        Sigmoid inputs are clipped to [-50, 50] to prevent OverflowError on
        pathological weights (e.g. very large positive/negative values).
        """
        v = self._vector(features)
        z = sum(w * x for w, x in zip(self.weights, v, strict=True)) + self.intercept
        raw = _sigmoid(z)
        a, b = self.platt_per_degree.get(degree, (1.0, 0.0))
        return _sigmoid(a * raw + b)

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
