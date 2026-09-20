from wave_alpha.prefs.config import resolve_stooq_api_key
from wave_alpha.prefs.models import AppConfig, DataConfig


def test_env_var_overrides_config(monkeypatch) -> None:
    monkeypatch.setenv("STOOQ_API_KEY", "from-env")
    cfg = AppConfig(data=DataConfig(stooq_api_key="from-config"))
    assert resolve_stooq_api_key(cfg) == "from-env"


def test_falls_back_to_config(monkeypatch) -> None:
    monkeypatch.delenv("STOOQ_API_KEY", raising=False)
    cfg = AppConfig(data=DataConfig(stooq_api_key="from-config"))
    assert resolve_stooq_api_key(cfg) == "from-config"


def test_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("STOOQ_API_KEY", raising=False)
    cfg = AppConfig()
    assert resolve_stooq_api_key(cfg) is None
