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
    """Protocol satisfied by HeuristicEngineRanker and LogisticEngineRanker.

    Unlike right_edge's ConfirmationModel which takes a separate `*, degree`
    kwarg, engine_ranker.predict_proba takes only `features` — degree is a
    field on EngineRankerFeatures and the model reads it from there. This
    keeps the call site uniform across logistic and heuristic models.
    """

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
            logger.warning(
                "engine_ranker: logistic unloadable, falling back to heuristic ({})", exc
            )
    return HeuristicEngineRanker()
