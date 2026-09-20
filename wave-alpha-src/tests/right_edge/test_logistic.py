"""Tests for LogisticConfirmationModel (Task 4.1)."""

from __future__ import annotations

import json

import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures


def _make_features(
    *,
    progress: float = 0.6,
    bars_since: int = 5,
    vol_decline: float = 0.1,
    zone_match: float = 0.0,
    atr_regime: float = 0.5,
) -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=progress,
        bars_since_candidate=bars_since,
        volume_decline_pct=vol_decline,
        higher_degree_zone_match=zone_match,
        atr_regime=atr_regime,
    )


def _promote_artifact(tmp_path, *, platt_per_degree: dict | None = None) -> object:
    """Write a minimal PROMOTE artifact to tmp_path and return the path."""
    if platt_per_degree is None:
        platt_per_degree = {"1": {"a": 1.0, "b": 0.0}}
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                "weights": [0.5, 0.3, 0.1, 0.05, 0.05],
                "intercept": -0.5,
                "platt_per_degree": platt_per_degree,
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    return path


# ---------------------------------------------------------------------------
# Task 4.1 — required tests
# ---------------------------------------------------------------------------


def test_from_json_loads_promote_artifact(tmp_path):
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    path = _promote_artifact(tmp_path)
    m = LogisticConfirmationModel.from_json(path)
    assert m.version == "logistic_v1"
    assert len(m.weights) == 5
    assert m.intercept == pytest.approx(-0.5)


def test_from_json_refuses_hold_verdict(tmp_path):
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel, ModelArtifactError

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [],
                "weights": [],
                "intercept": 0.0,
                "platt_per_degree": {},
                "gate": {"verdict": "HOLD"},
            }
        )
    )
    with pytest.raises(ModelArtifactError):
        LogisticConfirmationModel.from_json(path)


def test_from_json_refuses_reject_verdict(tmp_path):
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel, ModelArtifactError

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [],
                "weights": [],
                "intercept": 0.0,
                "platt_per_degree": {},
                "gate": {"verdict": "REJECT"},
            }
        )
    )
    with pytest.raises(ModelArtifactError):
        LogisticConfirmationModel.from_json(path)


def test_from_json_refuses_missing_gate(tmp_path):
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel, ModelArtifactError

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [],
                "weights": [],
                "intercept": 0.0,
                "platt_per_degree": {},
            }
        )
    )
    with pytest.raises(ModelArtifactError):
        LogisticConfirmationModel.from_json(path)


def test_predict_proba_in_unit_interval(tmp_path):
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    path = _promote_artifact(tmp_path)
    m = LogisticConfirmationModel.from_json(path)
    f = _make_features()
    p = m.predict_proba(f, degree=1)
    assert 0.0 <= p <= 1.0


def test_predict_proba_matches_training_predict_logistic_then_platt(tmp_path):
    """Round-trip: model.predict_proba(f, degree=1) == apply_platt(platt[1], predict_logistic(params, f)).

    This locks in the no-train/serve-skew invariant: the vector assembly in
    LogisticConfirmationModel._vector must mirror _row_to_vector in training.py.
    """
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import (
        LogisticParams,
        PlattParams,
        apply_platt,
        predict_logistic,
    )

    weights = [0.8, 0.4, 0.2, 0.1, 0.05]
    intercept = -0.3
    platt_a = 1.2
    platt_b = -0.1

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                "weights": weights,
                "intercept": intercept,
                "platt_per_degree": {"1": {"a": platt_a, "b": platt_b}},
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )

    m = LogisticConfirmationModel.from_json(path)
    f = _make_features(progress=0.75, bars_since=8, vol_decline=0.2, zone_match=0.1, atr_regime=0.6)

    # Result from LogisticConfirmationModel
    model_proba = m.predict_proba(f, degree=1)

    # Reference from training.py functions
    params = LogisticParams(
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
        ),
        weights=tuple(weights),
        intercept=intercept,
    )
    platt = PlattParams(a=platt_a, b=platt_b)
    raw = predict_logistic(params, f)
    expected = apply_platt(platt, raw)

    assert model_proba == pytest.approx(expected, abs=1e-9)


def test_predict_proba_no_overflow_on_extreme_weights(tmp_path):
    """Predict with weights large enough to push z below -50 — must not raise OverflowError.

    Without the sigmoid clip, math.exp(-z) overflows when z is a large negative number.
    With the clip, z is clamped to -50 and the call returns a valid float in [0, 1].

    Note: after the logistic step, the Platt layer also passes through _sigmoid, so
    the final proba is _sigmoid(a * raw + b).  With identity Platt (a=1, b=0) and
    raw ≈ sigmoid(-50) ≈ 0, the final output is _sigmoid(0) = 0.5 — NOT 0.
    The important invariant is: no OverflowError, and the result is in [0, 1].
    """
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    path = tmp_path / "m_extreme.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                # Very large negative weight → z = -1000 * 0.8 = -800, clips to -50
                "weights": [-1000.0, 0.0, 0.0, 0.0, 0.0],
                "intercept": 0.0,
                "platt_per_degree": {"1": {"a": 1.0, "b": 0.0}},
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    m = LogisticConfirmationModel.from_json(path)
    f = _make_features(progress=0.8)

    # Must not raise OverflowError; result must be a valid probability
    proba = m.predict_proba(f, degree=1)
    assert 0.0 <= proba <= 1.0


def test_predict_proba_no_overflow_on_very_positive_weights(tmp_path):
    """Predict with weights large enough to push z above 50 — must not raise OverflowError."""
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    path = tmp_path / "m_large.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                # Very large positive weight → z = 1000 * 0.8 = 800, clips to 50
                "weights": [1000.0, 0.0, 0.0, 0.0, 0.0],
                "intercept": 0.0,
                "platt_per_degree": {"1": {"a": 1.0, "b": 0.0}},
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    m = LogisticConfirmationModel.from_json(path)
    f = _make_features(progress=0.8)

    # Must not raise OverflowError; result must be a valid probability
    proba = m.predict_proba(f, degree=1)
    assert 0.0 <= proba <= 1.0


def test_predict_proba_unknown_degree_uses_identity(tmp_path):
    """A degree absent from platt_per_degree falls back to Platt (a=1, b=0) — identity."""
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import (
        LogisticParams,
        PlattParams,
        apply_platt,
        predict_logistic,
    )

    weights = [0.5, 0.3, 0.1, 0.05, 0.05]
    intercept = -0.5

    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                "weights": weights,
                "intercept": intercept,
                "platt_per_degree": {"1": {"a": 1.5, "b": -0.2}},
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    m = LogisticConfirmationModel.from_json(path)
    f = _make_features()

    # degree=99 is not in platt_per_degree — falls back to identity (a=1, b=0)
    proba_unknown = m.predict_proba(f, degree=99)

    # Verify it matches apply_platt with identity scaler
    params = LogisticParams(
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
        ),
        weights=tuple(weights),
        intercept=intercept,
    )
    raw = predict_logistic(params, f)
    identity = apply_platt(PlattParams(a=1.0, b=0.0), raw)
    # With identity Platt, apply_platt(raw) = sigmoid(1*raw + 0) != raw
    # but it should equal predict_proba with no-op scaler
    assert proba_unknown == pytest.approx(identity, abs=1e-9)


# ---------------------------------------------------------------------------
# Phase 2 — PROMOTE_CALIBRATION verdict acceptance in from_json
# ---------------------------------------------------------------------------


def test_from_json_loads_promote_calibration_artifact(tmp_path):
    """from_json must accept PROMOTE_CALIBRATION verdict (not just PROMOTE)."""
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    path = tmp_path / "m_cal.json"
    path.write_text(
        json.dumps(
            {
                "version": "logistic_v1",
                "feature_order": [
                    "retracement_progress",
                    "bars_since_candidate",
                    "volume_decline_pct",
                    "higher_degree_zone_match",
                    "atr_regime",
                ],
                "weights": [0.5, 0.3, 0.1, 0.05, 0.05],
                "intercept": -0.5,
                "platt_per_degree": {"1": {"a": 1.0, "b": 0.0}},
                "gate": {"verdict": "PROMOTE_CALIBRATION"},
            }
        )
    )
    m = LogisticConfirmationModel.from_json(path)
    assert m.version == "logistic_v1"
    # Must be able to predict without raising
    f = _make_features()
    p = m.predict_proba(f, degree=1)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# Task 11 — _vector reads from feature_order dynamically
# ---------------------------------------------------------------------------


def test_logistic_vector_uses_feature_order_dynamically() -> None:
    """Old 5-field artifacts should produce the same vector even after
    RightEdgeFeatures gains new default-zero fields."""
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    f = RightEdgeFeatures(
        retracement_progress=0.4,
        bars_since_candidate=10,
        volume_decline_pct=0.1,
        higher_degree_zone_match=0.5,
        atr_regime=0.2,
        # New fib fields default to 0
    )
    legacy = LogisticConfirmationModel(
        version="v1",
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
        ),
        weights=(1.0, 1.0, 1.0, 1.0, 1.0),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    v = legacy._vector(f)
    assert v == [0.4, 10.0 / 20.0, 0.1, 0.5, 0.2]


def test_logistic_vector_includes_new_fib_features_when_in_feature_order() -> None:
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel

    f = RightEdgeFeatures(
        retracement_progress=0.0,
        bars_since_candidate=0,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=0.0,
        fib_w2_w1_retrace=0.5,
        fib_w2_w1_retrace_present=1.0,
    )
    new_model = LogisticConfirmationModel(
        version="v2",
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
            "fib_w2_w1_retrace",
            "fib_w2_w1_retrace_present",
        ),
        weights=(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    v = new_model._vector(f)
    assert v[5] == 0.5
    assert v[6] == 1.0
