from __future__ import annotations

import os
from pathlib import Path

import yaml

from wave_alpha.prefs.models import AppConfig

DEFAULT_CONFIG_PATH: Path = Path.home() / ".wave_alpha" / "config.yaml"


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from YAML; return defaults if the file does not exist."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return AppConfig()
    raw = yaml.safe_load(target.read_text()) or {}
    return AppConfig.model_validate(raw)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


def resolve_api_key(cfg: AppConfig) -> str | None:
    """Return the effective API key. Env var takes precedence over config file."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    return cfg.llm.api_key


def resolve_stooq_api_key(cfg: AppConfig) -> str | None:
    """Return the effective Stooq API key. Env var STOOQ_API_KEY beats config."""
    env = os.environ.get("STOOQ_API_KEY")
    if env:
        return env
    return cfg.data.stooq_api_key
