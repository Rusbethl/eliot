from pathlib import Path

from wave_alpha.prefs.config import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_api_key,
    save_config,
)
from wave_alpha.prefs.models import AppConfig, LLMConfig


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg == AppConfig()


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    cfg = AppConfig(llm=LLMConfig(api_key="sk-test", alpha=0.7))
    save_config(cfg, target)
    loaded = load_config(target)
    assert loaded.llm.api_key == "sk-test"
    assert loaded.llm.alpha == 0.7


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    cfg = AppConfig(llm=LLMConfig(api_key="sk-file"))
    assert resolve_api_key(cfg) == "sk-env"


def test_resolve_api_key_falls_back_to_config(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = AppConfig(llm=LLMConfig(api_key="sk-file"))
    assert resolve_api_key(cfg) == "sk-file"


def test_default_config_path_under_dot_wave_alpha() -> None:
    assert DEFAULT_CONFIG_PATH.name == "config.yaml"
    assert DEFAULT_CONFIG_PATH.parent.name == ".wave_alpha"
