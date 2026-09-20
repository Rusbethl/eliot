"""Tests for EngineRankerConfig defaults and AppConfig wiring."""

from __future__ import annotations

import pytest

from wave_alpha.prefs.models import (
    AppConfig,
    EngineRankerConfig,
    EngineRankerOutcomeTrainingConfig,
)


def test_engine_ranker_outcome_training_defaults() -> None:
    cfg = EngineRankerOutcomeTrainingConfig()
    assert cfg.walk_forward_split == pytest.approx(0.7)
    assert cfg.class_balance_low == pytest.approx(0.35)
    assert cfg.class_balance_high == pytest.approx(0.65)
    assert cfg.include_time_stops is True
    assert cfg.min_resolved_per_degree == 30


def test_engine_ranker_config_defaults() -> None:
    cfg = EngineRankerConfig()
    assert cfg.kind == "auto"
    assert isinstance(cfg.outcome_training, EngineRankerOutcomeTrainingConfig)


def test_app_config_includes_engine_ranker() -> None:
    app = AppConfig()
    assert app.engine_ranker.kind == "auto"


def test_walk_forward_split_bounds() -> None:
    with pytest.raises(ValueError):
        EngineRankerOutcomeTrainingConfig(walk_forward_split=1.0)
    with pytest.raises(ValueError):
        EngineRankerOutcomeTrainingConfig(walk_forward_split=0.0)
