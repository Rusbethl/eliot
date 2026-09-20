"""Pure right-edge assessment: given point-in-time bars + a confirmation model,
return a probability for the most recent tentative degree-2 pivot.

Shared by the web deepdive view and the scan orchestrator.  Doesn't fetch data
or load models — callers are responsible for both, which keeps this function
testable without disk or network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES, _DEFAULT_DEGREE_FOR_COUNT
from wave_alpha.pivots.models import PivotEvent, PivotState
from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
from wave_alpha.right_edge.extractor import FeatureExtractor
from wave_alpha.right_edge.loader import ConfirmationModel


@dataclass(frozen=True)
class RightEdgeAssessment:
    proba: float
    degree: int
    pivot_timestamp: date
    pivot_type: str
    model_kind: str


def assess_from_bars(
    bars: list[Bar],
    *,
    model: ConfirmationModel,
    interval: Interval = Interval.DAILY,
    degree: int = _DEFAULT_DEGREE_FOR_COUNT,
) -> RightEdgeAssessment | None:
    if not bars:
        return None
    snapshots = detect_multi_degree_with_snapshots(
        bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
    )
    snap = snapshots.get(degree)
    if snap is None or snap.atr_at_last_bar is None or snap.required_move <= 0:
        return None
    tentative: list[PivotEvent] = [p for p in snap.pivots if p.state is PivotState.TENTATIVE]
    if not tentative:
        return None
    candidate = max(tentative, key=lambda p: p.timestamp)
    # Top-1 count from this degree's pivots feeds fib feature slots. Empty
    # list when no template fits — the extractor handles top_count=None
    # silently.
    counts = enumerate_counts(snap.pivots, degree=degree, top_k=1)
    top_count = counts[0] if counts else None
    features = FeatureExtractor().extract(
        candidate=candidate,
        point_in_time_bars=bars,
        atr_at_candidate=snap.atr_at_last_bar,
        required_move=snap.required_move,
        higher_degree_zone_match=0.0,
        top_count=top_count,
    )
    proba = model.predict_proba(features, degree=degree)
    return RightEdgeAssessment(
        proba=proba,
        degree=degree,
        pivot_timestamp=candidate.timestamp,
        pivot_type=candidate.type.value,
        model_kind=type(model).__name__,
    )
