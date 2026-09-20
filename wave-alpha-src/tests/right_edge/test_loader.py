"""Tests for right_edge/loader.py (Task 6.1)."""

from __future__ import annotations

import json

import pytest

from wave_alpha.prefs.models import RightEdgeConfig
from wave_alpha.right_edge.loader import (
    HeuristicAdapter,
    load_right_edge_model,
)
from wave_alpha.right_edge.logistic import LogisticConfirmationModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _promote_artifact(tmp_path) -> object:
    """Write a minimal PROMOTE artifact and return the path.

    Note: uses a synthetic subset of degrees (degree 1 only).  Production
    artifacts cover all 5 degrees (0..4) emitted by ``_DEFAULT_ATR_MULTIPLES``.
    Tests use a subset because the loader is degree-count-agnostic and only
    needs one entry to exercise the load + predict path.
    """
    path = tmp_path / "model.json"
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
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    return path


def _hold_artifact(tmp_path) -> object:
    """Write a minimal HOLD artifact and return the path."""
    path = tmp_path / "model_hold.json"
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
    return path


# ---------------------------------------------------------------------------
# Task 6.1 — required tests
# ---------------------------------------------------------------------------


def test_loader_auto_no_file_returns_heuristic(tmp_path):
    """auto mode: missing artifact files → returns HeuristicAdapter."""
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=tmp_path / "missing.json",
        calibrated_heuristic_path=tmp_path / "missing-cal.json",
    )
    assert isinstance(m, HeuristicAdapter)


def test_loader_auto_promote_file_returns_logistic(tmp_path):
    """auto mode: PROMOTE artifact present → returns LogisticConfirmationModel."""
    path = _promote_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=path,
        calibrated_heuristic_path=tmp_path / "missing-cal.json",
    )
    assert isinstance(m, LogisticConfirmationModel)


def test_loader_auto_hold_file_falls_back(tmp_path):
    """auto mode: HOLD artifact present, no calibrated heuristic → falls back to HeuristicAdapter."""
    path = _hold_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=path,
        calibrated_heuristic_path=tmp_path / "missing-cal.json",
    )
    assert isinstance(m, HeuristicAdapter)


def test_loader_logistic_missing_file_raises(tmp_path):
    """explicit logistic mode: missing file → FileNotFoundError (operator must handle)."""
    cfg = RightEdgeConfig(model="logistic")
    with pytest.raises(FileNotFoundError):
        load_right_edge_model(cfg, path=tmp_path / "missing.json")


def test_loader_explicit_heuristic_returns_adapter(tmp_path):
    """explicit heuristic mode: always returns HeuristicAdapter regardless of path."""
    cfg = RightEdgeConfig(model="heuristic")
    # Even if a PROMOTE artifact exists, explicit heuristic means heuristic.
    path = _promote_artifact(tmp_path)
    m = load_right_edge_model(cfg, path=path)
    assert isinstance(m, HeuristicAdapter)


def test_loader_result_satisfies_protocol(tmp_path):
    """All three load paths return objects that satisfy ConfirmationModel protocol."""
    from wave_alpha.right_edge.features import RightEdgeFeatures

    def _make_features() -> RightEdgeFeatures:
        return RightEdgeFeatures(
            retracement_progress=0.5,
            bars_since_candidate=5,
            volume_decline_pct=0.1,
            higher_degree_zone_match=0.0,
            atr_regime=0.5,
        )

    path = _promote_artifact(tmp_path)

    for cfg_model in ("heuristic", "auto"):
        cfg = RightEdgeConfig(model=cfg_model)
        m = load_right_edge_model(cfg, path=path)
        p = m.predict_proba(_make_features(), degree=1)
        assert 0.0 <= p <= 1.0, f"{cfg_model}: proba={p!r} out of [0,1]"

    # logistic path (PROMOTE file exists)
    cfg = RightEdgeConfig(model="logistic")
    m = load_right_edge_model(cfg, path=path)
    p = m.predict_proba(_make_features(), degree=1)
    assert 0.0 <= p <= 1.0


def test_loader_heuristic_adapter_ignores_degree(tmp_path):
    """HeuristicAdapter.predict_proba returns the same value regardless of degree."""
    from wave_alpha.right_edge.features import RightEdgeFeatures

    f = RightEdgeFeatures(
        retracement_progress=0.7,
        bars_since_candidate=8,
        volume_decline_pct=0.2,
        higher_degree_zone_match=0.1,
        atr_regime=0.6,
    )
    adapter = HeuristicAdapter()
    p1 = adapter.predict_proba(f, degree=1)
    p2 = adapter.predict_proba(f, degree=3)
    assert p1 == p2


# ---------------------------------------------------------------------------
# Phase 3 — Task 3.3: calibrated_heuristic loader path
# ---------------------------------------------------------------------------


def _promote_calibrated_heuristic_artifact(tmp_path, *, suffix: str = "cal.json") -> object:
    """Write a minimal calibrated-heuristic PROMOTE_CALIBRATION artifact."""
    import json

    path = tmp_path / suffix
    path.write_text(
        json.dumps(
            {
                "version": "calibrated_heuristic_v1",
                "heuristic_version": "heuristic_v2",
                "platt_per_degree": {"1": {"a": 1.2, "b": -0.1}},
                "gate": {"verdict": "PROMOTE_CALIBRATION"},
            }
        )
    )
    return path


def test_loader_explicit_calibrated_heuristic_returns_model(tmp_path):
    """explicit calibrated_heuristic mode: returns CalibratedHeuristicModel."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    path = _promote_calibrated_heuristic_artifact(tmp_path)
    cfg = RightEdgeConfig(model="calibrated_heuristic")
    m = load_right_edge_model(cfg, calibrated_heuristic_path=path)
    assert isinstance(m, CalibratedHeuristicModel)


def test_loader_auto_prefers_logistic_over_calibrated_heuristic(tmp_path):
    """auto mode: logistic exists → returns logistic, not calibrated heuristic."""
    log = _promote_artifact(tmp_path)
    cal = _promote_calibrated_heuristic_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(cfg, logistic_path=log, calibrated_heuristic_path=cal)
    assert isinstance(m, LogisticConfirmationModel)


def test_loader_auto_falls_back_to_calibrated_heuristic_when_logistic_missing(tmp_path):
    """auto mode: logistic absent → falls back to calibrated heuristic if present."""
    from wave_alpha.right_edge.calibrated_heuristic import CalibratedHeuristicModel

    cal = _promote_calibrated_heuristic_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=tmp_path / "missing_logistic.json",
        calibrated_heuristic_path=cal,
    )
    assert isinstance(m, CalibratedHeuristicModel)


def test_loader_auto_falls_back_to_heuristic_when_both_missing(tmp_path):
    """auto mode: both artifacts missing → returns raw HeuristicAdapter."""
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=tmp_path / "missing_logistic.json",
        calibrated_heuristic_path=tmp_path / "missing_cal.json",
    )
    assert isinstance(m, HeuristicAdapter)


def test_loader_auto_falls_back_to_heuristic_when_logistic_hold_and_cal_missing(tmp_path):
    """auto mode: logistic is HOLD (fails), calibrated heuristic absent → raw heuristic."""
    hold = _hold_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg,
        logistic_path=hold,
        calibrated_heuristic_path=tmp_path / "missing_cal.json",
    )
    assert isinstance(m, HeuristicAdapter)


def test_loader_calibrated_heuristic_missing_file_raises(tmp_path):
    """explicit calibrated_heuristic mode: missing file → FileNotFoundError."""
    cfg = RightEdgeConfig(model="calibrated_heuristic")
    with pytest.raises(FileNotFoundError):
        load_right_edge_model(cfg, calibrated_heuristic_path=tmp_path / "missing_cal.json")


def test_loader_legacy_path_kwarg_still_works(tmp_path):
    """Backward-compatible: callers using path= (old signature) still work."""
    path = _promote_artifact(tmp_path)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(cfg, path=path)
    assert isinstance(m, LogisticConfirmationModel)


def test_loader_rejects_artifact_with_unknown_feature_name(tmp_path) -> None:
    """If an artifact's feature_order references a field that doesn't exist
    on RightEdgeFeatures, the loader must refuse rather than silently
    misalign features at predict time."""
    from wave_alpha.right_edge.logistic import ModelArtifactError

    artifact = tmp_path / "bad.json"
    artifact.write_text(
        json.dumps(
            {
                "version": "v99",
                "feature_order": ["nonexistent_field"],
                "weights": [1.0],
                "intercept": 0.0,
                "platt_per_degree": {"2": {"a": 1.0, "b": 0.0}},
                "gate": {"verdict": "PROMOTE"},
            }
        )
    )
    cfg = RightEdgeConfig(model="logistic")
    try:
        load_right_edge_model(cfg, logistic_path=artifact)
        raise AssertionError("expected ModelArtifactError")
    except ModelArtifactError:
        pass
