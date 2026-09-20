"""Web-side right-edge confirmation: load the trained model once, fetch bars
via the cached provider, delegate to the shared assessment helper.

The pipeline emits wave counts at degree 2 (see ``pipeline._DEFAULT_DEGREE_FOR_COUNT``).
The right-edge model answers: how likely is the *terminal* tentative pivot of
that degree to be confirmed?  This is the natural confidence signal for the
deepdive view — a structurally similar setup with a low confirmation
probability is a weaker setup than the same structure with a high one.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval
from wave_alpha.prefs.config import load_config
from wave_alpha.right_edge.assessment import RightEdgeAssessment, assess_from_bars
from wave_alpha.right_edge.loader import ConfirmationModel, load_right_edge_model

__all__ = ["RightEdgeAssessment", "compute_right_edge_assessment"]


@lru_cache(maxsize=1)
def _model() -> tuple[ConfirmationModel, str]:
    cfg = load_config()
    m = load_right_edge_model(cfg.right_edge)
    return m, type(m).__name__


def _data_dir() -> Path:
    """Indirection so tests can monkeypatch the cache location.

    Mirrors the pattern in ``web.deps`` rather than importing it to avoid a
    circular import — both modules read the same on-disk cache directory.
    """
    return Path.home() / ".wave_alpha"


def compute_right_edge_assessment(ticker: str, as_of: date) -> RightEdgeAssessment | None:
    data_dir = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = CachedProvider(upstream=YahooProvider(), db_path=data_dir / "cache.sqlite")

    raw = provider.fetch(ticker.upper(), Interval.DAILY, end=as_of)
    if not raw:
        return None
    bars = PointInTimeView(raw, as_of).visible()

    model, _ = _model()
    return assess_from_bars(bars, model=model, interval=Interval.DAILY)
