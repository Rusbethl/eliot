from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel


def _features(**overrides) -> RightEdgeFeatures:
    base = dict(
        retracement_progress=0.0,
        bars_since_candidate=0,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )
    base.update(overrides)
    return RightEdgeFeatures(**base)


def test_heuristic_zero_features_returns_low_probability() -> None:
    model = HeuristicConfirmationModel()
    # atr_regime=1.0 default means 0.10 * clip(1.0 - 0.5) = 0.05
    p = model.predict_proba(_features(atr_regime=0.0))
    assert p == 0.0


def test_heuristic_full_retrace_returns_meaningful_probability() -> None:
    model = HeuristicConfirmationModel()
    p = model.predict_proba(_features(retracement_progress=1.0, bars_since_candidate=10))
    assert 0.5 < p <= 1.0


def test_heuristic_clamps_to_unit_interval() -> None:
    model = HeuristicConfirmationModel()
    p = model.predict_proba(_features(retracement_progress=2.0, bars_since_candidate=100))
    assert 0.0 <= p <= 1.0


def test_heuristic_version_string() -> None:
    assert HeuristicConfirmationModel().version == "heuristic_v2"
