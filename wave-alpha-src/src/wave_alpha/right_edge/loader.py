"""Right-edge model loader.

Selects the appropriate ConfirmationModel based on ``RightEdgeConfig``:

- ``"heuristic"`` — always returns ``HeuristicAdapter`` (wraps the hand-coded
  weighted-sum model, ignores the ``degree`` kwarg).
- ``"calibrated_heuristic"`` — loads ``CalibratedHeuristicModel`` from the
  default calibrated-heuristic artifact path.  Raises ``FileNotFoundError``
  if the file is absent.
- ``"logistic"`` — loads ``LogisticConfirmationModel`` from the default JSON
  artifact path (or the caller-supplied ``logistic_path``).  Raises
  ``FileNotFoundError`` if the file is absent — caller must handle explicitly.
- ``"auto"`` (default) — preference order: logistic > calibrated_heuristic >
  raw heuristic.  Falls back silently on absent files or malformed artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from loguru import logger

from wave_alpha.prefs.models import RightEdgeConfig
from wave_alpha.right_edge.calibrated_heuristic import (
    CalibratedHeuristicArtifactError,
    CalibratedHeuristicModel,
)
from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel
from wave_alpha.right_edge.logistic import (
    LogisticConfirmationModel,
    ModelArtifactError,
)


class ConfirmationModel(Protocol):
    """Protocol for right-edge confirmation models.

    ``HeuristicAdapter``, ``LogisticConfirmationModel``, and
    ``CalibratedHeuristicModel`` all satisfy this interface.  The ``degree``
    kwarg is required so callers can pass it uniformly; heuristic
    implementations ignore it.
    """

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float: ...


_DEFAULT_LOGISTIC_PATH = Path(__file__).parent / "models" / "right_edge_logistic_v1.json"
_DEFAULT_CALIBRATED_HEURISTIC_PATH = (
    Path(__file__).parent / "models" / "right_edge_calibrated_heuristic_v1.json"
)


class HeuristicAdapter:
    """Adapts ``HeuristicConfirmationModel`` to the ``ConfirmationModel`` protocol.

    The heuristic does not use ``degree``; the adapter accepts and silently
    discards it so callers can use the uniform ``predict_proba(f, degree=d)``
    signature regardless of which model is loaded.
    """

    def __init__(self) -> None:
        self._inner = HeuristicConfirmationModel()

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        del degree  # heuristic ignores degree
        return self._inner.predict_proba(features)


def load_right_edge_model(
    cfg: RightEdgeConfig,
    *,
    logistic_path: Path = _DEFAULT_LOGISTIC_PATH,
    calibrated_heuristic_path: Path = _DEFAULT_CALIBRATED_HEURISTIC_PATH,
    # Legacy alias: older callers pass ``path=`` instead of ``logistic_path=``.
    path: Path | None = None,
) -> ConfirmationModel:
    """Return the appropriate ConfirmationModel for the given config.

    Args:
        cfg: Application right-edge config (from ``AppConfig.right_edge``).
        logistic_path: Path to the logistic JSON artifact.  Defaults to the
            bundled model location.  Override in tests.
        calibrated_heuristic_path: Path to the calibrated-heuristic JSON
            artifact.  Defaults to the bundled model location.
        path: Deprecated legacy alias for ``logistic_path``.  If provided,
            it overrides ``logistic_path`` so older call sites continue to work.

    Returns:
        A ``ConfirmationModel`` instance.  In ``"auto"`` mode, always returns
        *something* (never raises); in ``"logistic"`` or
        ``"calibrated_heuristic"`` mode, propagates ``FileNotFoundError`` if
        the artifact is missing.
    """
    # Backward-compatible alias: older tests pass path= as a positional kwarg.
    if path is not None:
        logistic_path = path

    if cfg.model == "heuristic":
        return HeuristicAdapter()

    if cfg.model == "calibrated_heuristic":
        # Operator-explicit: raise if the artifact is absent.
        return CalibratedHeuristicModel.from_json(calibrated_heuristic_path)

    if cfg.model == "logistic":
        # Operator-explicit: raise if the artifact is absent.
        return LogisticConfirmationModel.from_json(logistic_path)

    # auto mode: prefer logistic > calibrated_heuristic > raw heuristic.
    if logistic_path.exists():
        try:
            return LogisticConfirmationModel.from_json(logistic_path)
        except (ModelArtifactError, ValueError, KeyError) as exc:
            logger.warning("right-edge: logistic unloadable, trying calibrated heuristic ({})", exc)
    if calibrated_heuristic_path.exists():
        try:
            return CalibratedHeuristicModel.from_json(calibrated_heuristic_path)
        except (CalibratedHeuristicArtifactError, ValueError, KeyError) as exc:
            logger.warning(
                "right-edge: calibrated heuristic unloadable, falling back to raw ({})", exc
            )
    return HeuristicAdapter()
