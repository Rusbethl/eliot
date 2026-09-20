from wave_alpha.prefs.models import (
    AppConfig,
    HistoryConfig,
    LLMConfig,
    RightEdgeConfig,
    RightEdgeOutcomeTrainingConfig,
    TemplatesConfig,
)


def test_llm_config_defaults() -> None:
    cfg = LLMConfig()
    assert cfg.scan_model == "claude-haiku-4-5-20251001"
    assert cfg.deep_model == "claude-sonnet-4-6"
    assert cfg.alpha == 0.6
    assert cfg.api_key is None


def test_app_config_defaults() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.llm, LLMConfig)


def test_history_config_defaults() -> None:
    cfg = HistoryConfig()
    assert cfg.retention_days == 730


def test_app_config_includes_history_with_defaults() -> None:
    cfg = AppConfig()
    assert cfg.history.retention_days == 730


def test_app_config_history_override_via_model_validate() -> None:
    cfg = AppConfig.model_validate({"history": {"retention_days": 365}})
    assert cfg.history.retention_days == 365


def test_history_config_retention_zero_is_allowed() -> None:
    cfg = HistoryConfig(retention_days=0)
    assert cfg.retention_days == 0


def test_history_config_retention_negative_is_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HistoryConfig(retention_days=-1)


def test_right_edge_outcome_training_defaults() -> None:
    cfg = RightEdgeOutcomeTrainingConfig()
    assert cfg.n_target_per_degree == 100
    assert cfg.include_time_stops is True
    assert cfg.time_stop_label == 0.0


def test_right_edge_config_includes_outcome_training_defaults() -> None:
    cfg = RightEdgeConfig()
    assert cfg.outcome_training.n_target_per_degree == 100


def test_right_edge_outcome_training_overrides() -> None:
    cfg = RightEdgeConfig.model_validate(
        {"outcome_training": {"n_target_per_degree": 50, "include_time_stops": False}}
    )
    assert cfg.outcome_training.n_target_per_degree == 50
    assert cfg.outcome_training.include_time_stops is False


def test_templates_config_defaults() -> None:
    cfg = TemplatesConfig()
    assert cfg.enable_leading_diagonal is False
    assert cfg.enabled_opt_in_names() == frozenset()


def test_templates_config_enables_leading_diagonal() -> None:
    cfg = TemplatesConfig(enable_leading_diagonal=True)
    assert cfg.enable_leading_diagonal is True
    assert cfg.enabled_opt_in_names() == frozenset({"leading_diagonal"})


def test_app_config_templates_override_via_model_validate() -> None:
    cfg = AppConfig.model_validate({"templates": {"enable_leading_diagonal": True}})
    assert cfg.templates.enable_leading_diagonal is True
    assert cfg.templates.enabled_opt_in_names() == frozenset({"leading_diagonal"})


def test_app_config_templates_default_is_off() -> None:
    """Default AppConfig must not enable any opt-in templates. Locks the
    contract that loading a config-less project produces backtest-neutral
    behaviour (see 2026-05-10 LD spec §10)."""
    cfg = AppConfig()
    assert cfg.templates.enable_leading_diagonal is False
    assert cfg.templates.enabled_opt_in_names() == frozenset()
