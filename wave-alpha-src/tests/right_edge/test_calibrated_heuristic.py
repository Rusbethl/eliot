"""Tests for CalibratedHeuristicModel (Phase 3 — Task 3.1)."""

from __future__ import annotations

import json
import math

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


def _write_artifact(
    path,
    *,
    verdict: str = "PROMOTE_CALIBRATION",
    platt_per_degree: dict | None = None,
) -> None:
    if platt_per_degree is None:
        platt_per_degree = {"0": {"a": 1.5, "b": -0.3}, "1": {"a": 1.2, "b": 0.0}}
    path.write_text(
        json.dumps(
            {
                "version": "calibrated_heuristic_v1",
                "heuristic_version": "heuristic_v2",
                "platt_per_degree": platt_per_degree,
                "gate": {"verdict": verdict},
            }
        )
    )


# ---------------------------------------------------------------------------
# from_json — loading and verdict checks
# ---------------------------------------------------------------------------


def test_from_json_loads_promote_calibration_artifact(tmp_path):
    """Should load without raising when verdict is PROMOTE_CALIBRATION."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    path = tmp_path / "h.json"
    _write_artifact(path, verdict="PROMOTE_CALIBRATION")
    m = CalibratedHeuristicModel.from_json(path)
    assert m.version == "calibrated_heuristic_v1"
    assert m.heuristic_version == "heuristic_v2"
    assert 0 in m.platt_per_degree
    assert m.platt_per_degree[0] == (1.5, -0.3)
    assert m.platt_per_degree[1] == (1.2, 0.0)


def test_from_json_loads_promote_artifact(tmp_path):
    """Should also accept PROMOTE verdict (not just PROMOTE_CALIBRATION)."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    path = tmp_path / "h.json"
    _write_artifact(path, verdict="PROMOTE")
    m = CalibratedHeuristicModel.from_json(path)
    assert m.version == "calibrated_heuristic_v1"


def test_from_json_refuses_hold_verdict(tmp_path):
    """Should raise CalibratedHeuristicArtifactError for HOLD verdict."""
    from wave_alpha.right_edge.calibrated_heuristic import (
        CalibratedHeuristicArtifactError,
        CalibratedHeuristicModel,
    )

    path = tmp_path / "h.json"
    _write_artifact(path, verdict="HOLD", platt_per_degree={})
    with pytest.raises(CalibratedHeuristicArtifactError):
        CalibratedHeuristicModel.from_json(path)


def test_from_json_refuses_reject_verdict(tmp_path):
    """Should raise CalibratedHeuristicArtifactError for REJECT verdict."""
    from wave_alpha.right_edge.calibrated_heuristic import (
        CalibratedHeuristicArtifactError,
        CalibratedHeuristicModel,
    )

    path = tmp_path / "h.json"
    _write_artifact(path, verdict="REJECT", platt_per_degree={})
    with pytest.raises(CalibratedHeuristicArtifactError):
        CalibratedHeuristicModel.from_json(path)


def test_from_json_refuses_missing_gate(tmp_path):
    """Should raise CalibratedHeuristicArtifactError when gate key is absent."""
    from wave_alpha.right_edge.calibrated_heuristic import (
        CalibratedHeuristicArtifactError,
        CalibratedHeuristicModel,
    )

    path = tmp_path / "h.json"
    path.write_text(
        json.dumps(
            {
                "version": "calibrated_heuristic_v1",
                "heuristic_version": "heuristic_v2",
                "platt_per_degree": {},
            }
        )
    )
    with pytest.raises(CalibratedHeuristicArtifactError):
        CalibratedHeuristicModel.from_json(path)


# ---------------------------------------------------------------------------
# predict_proba
# ---------------------------------------------------------------------------


def test_predict_proba_in_unit_interval(tmp_path):
    """All outputs must be in [0, 1]."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    path = tmp_path / "h.json"
    _write_artifact(path)
    m = CalibratedHeuristicModel.from_json(path)
    f = _make_features()
    for degree in [0, 1, 2, 99]:
        p = m.predict_proba(f, degree=degree)
        assert 0.0 <= p <= 1.0, f"degree={degree}: proba={p!r} out of [0,1]"


def test_predict_proba_unknown_degree_uses_identity_platt(tmp_path):
    """A degree absent from platt_per_degree uses identity scaler (a=1, b=0).

    With identity Platt, predict_proba returns sigmoid(raw_heuristic), which
    is NOT the same as raw_heuristic (because sigmoid remaps [0,1] → [0.5, ~0.73]).
    But it DOES equal sigmoid(1.0 * raw + 0.0).
    """
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel
    from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel

    path = tmp_path / "h.json"
    # Only degree 0 has a scaler; degree 99 will use identity.
    _write_artifact(
        path,
        platt_per_degree={"0": {"a": 2.0, "b": -0.5}},
        verdict="PROMOTE_CALIBRATION",
    )
    m = CalibratedHeuristicModel.from_json(path)
    f = _make_features()

    raw = HeuristicConfirmationModel().predict_proba(f)
    expected_identity = 1.0 / (1.0 + math.exp(-max(min(raw, 50.0), -50.0)))

    proba_unknown = m.predict_proba(f, degree=99)
    assert proba_unknown == pytest.approx(expected_identity, abs=1e-9)


def test_predict_proba_applies_platt_when_degree_present(tmp_path):
    """When degree IS in platt_per_degree, the Platt scaler modifies the output."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel
    from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel

    path = tmp_path / "h.json"
    a, b = 1.5, -0.3
    _write_artifact(
        path,
        platt_per_degree={"1": {"a": a, "b": b}},
        verdict="PROMOTE_CALIBRATION",
    )
    m = CalibratedHeuristicModel.from_json(path)
    f = _make_features(progress=0.7, bars_since=8)

    raw = HeuristicConfirmationModel().predict_proba(f)
    z = max(min(a * raw + b, 50.0), -50.0)
    expected = 1.0 / (1.0 + math.exp(-z))

    proba = m.predict_proba(f, degree=1)
    assert proba == pytest.approx(expected, abs=1e-9)


def test_predict_proba_no_overflow_on_extreme_platt(tmp_path):
    """Extreme Platt values (large a) must not raise OverflowError."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    path = tmp_path / "h.json"
    _write_artifact(
        path,
        platt_per_degree={"0": {"a": 1000.0, "b": 500.0}},
        verdict="PROMOTE_CALIBRATION",
    )
    m = CalibratedHeuristicModel.from_json(path)
    f = _make_features()
    p = m.predict_proba(f, degree=0)
    assert 0.0 <= p <= 1.0
