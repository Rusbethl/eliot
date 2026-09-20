"""Unit tests for LogisticEngineRanker artifact loading + prediction."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.logistic import (
    EngineRankerArtifactError,
    LogisticEngineRanker,
)


def _good_artifact() -> dict:
    return {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"1": -2.0, "2": -1.0, "3": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }


def test_predict_proba_pinned_weights(tmp_path: Path) -> None:
    """Regression-lock: identical input → identical output, forever."""
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    m = LogisticEngineRanker.from_json(path)
    f = EngineRankerFeatures(template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=2)
    # z = 1.0*0.8 + 1.0*0.5 + 1.0*0.7 + 1.0*0.6 + (-1.0) = 1.6
    expected = 1.0 / (1.0 + math.exp(-1.6))
    assert m.predict_proba(f) == pytest.approx(expected, abs=1e-9)


def test_predict_proba_unknown_degree_uses_median_intercept(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    m = LogisticEngineRanker.from_json(path)
    # Median of [-2, -1, 0] is -1.0.
    f_unknown = EngineRankerFeatures(
        template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=99
    )
    f_known = EngineRankerFeatures(template_fit=0.8, fib=0.5, recency=0.7, coherence=0.6, degree=2)
    assert m.predict_proba(f_unknown) == pytest.approx(m.predict_proba(f_known))


def test_refuses_to_load_unpromoted_artifact(tmp_path: Path) -> None:
    bad = _good_artifact()
    bad["gate"]["verdict"] = "HOLD"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(EngineRankerArtifactError, match="verdict"):
        LogisticEngineRanker.from_json(path)


def test_refuses_unknown_feature_name(tmp_path: Path) -> None:
    bad = _good_artifact()
    bad["feature_order"] = ["template_fit", "fib", "recency", "not_a_field"]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(EngineRankerArtifactError, match="not_a_field"):
        LogisticEngineRanker.from_json(path)


def test_sigmoid_clipping_no_overflow(tmp_path: Path) -> None:
    """Pathological weights must not raise OverflowError."""
    art = _good_artifact()
    art["weights"] = [1000.0, 1000.0, 1000.0, 1000.0]
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(art))
    m = LogisticEngineRanker.from_json(path)
    f = EngineRankerFeatures(template_fit=1.0, fib=1.0, recency=1.0, coherence=1.0, degree=2)
    p = m.predict_proba(f)
    assert 0.0 < p <= 1.0
