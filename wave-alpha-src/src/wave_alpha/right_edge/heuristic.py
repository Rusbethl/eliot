from __future__ import annotations

from wave_alpha.right_edge.features import RightEdgeFeatures


class HeuristicConfirmationModel:
    """Hand-coded weighted sum. Replaced by a logistic model trained from
    backtest-derived labels in Plan 2."""

    version: str = "heuristic_v2"

    def predict_proba(self, f: RightEdgeFeatures) -> float:
        score = (
            0.40 * _clip(f.retracement_progress)
            + 0.25 * min(f.bars_since_candidate / 10.0, 1.0)
            + 0.15 * max(0.0, f.volume_decline_pct / 0.5)
            + 0.10 * _clip(f.higher_degree_zone_match)
            + 0.10 * _clip(f.atr_regime - 0.5)  # mild bias above neutral regime
        )
        return _clip(score)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))
