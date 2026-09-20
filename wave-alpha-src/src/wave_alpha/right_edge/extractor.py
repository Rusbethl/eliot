from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.right_edge.features import RightEdgeFeatures, extract_features


class FeatureExtractor:
    """Single source of truth for right-edge feature computation.

    Used by both training pipeline (offline) and live/backtest inference.
    Identical code in both paths prevents train/serve skew. The class is a
    thin delegator over the pure ``extract_features`` function — it exists
    to give callers a stable handle they can construct once and reuse.
    """

    def extract(
        self,
        *,
        candidate: PivotEvent,
        point_in_time_bars: list[Bar],
        atr_at_candidate: Decimal,
        required_move: Decimal,
        higher_degree_zone_match: float,
        top_count: WaveCount | None = None,
    ) -> RightEdgeFeatures:
        return extract_features(
            candidate=candidate,
            bars=point_in_time_bars,
            atr_at_candidate=atr_at_candidate,
            required_move=required_move,
            higher_degree_zone_match=higher_degree_zone_match,
            top_count=top_count,
        )
