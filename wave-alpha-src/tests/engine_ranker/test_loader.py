"""Tests for load_engine_ranker auto-fallback chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.loader import load_engine_ranker
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.prefs.models import EngineRankerConfig


def _good_artifact() -> dict:
    return {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"2": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }


def test_auto_falls_back_to_heuristic_when_no_artifact(tmp_path: Path) -> None:
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=tmp_path / "nope.json")
    assert isinstance(model, HeuristicEngineRanker)


def test_auto_loads_logistic_when_present(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, LogisticEngineRanker)


def test_auto_falls_back_on_malformed_artifact(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text("not json {{{")
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_auto_falls_back_on_unpromoted_artifact(tmp_path: Path) -> None:
    art = _good_artifact()
    art["gate"]["verdict"] = "HOLD"
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(art))
    cfg = EngineRankerConfig(kind="auto")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_heuristic_kind_returns_heuristic_even_when_artifact_present(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(_good_artifact()))
    cfg = EngineRankerConfig(kind="heuristic")
    model = load_engine_ranker(cfg, logistic_path=path)
    assert isinstance(model, HeuristicEngineRanker)


def test_logistic_kind_raises_on_missing_artifact(tmp_path: Path) -> None:
    cfg = EngineRankerConfig(kind="logistic")
    with pytest.raises(FileNotFoundError):
        load_engine_ranker(cfg, logistic_path=tmp_path / "nope.json")
