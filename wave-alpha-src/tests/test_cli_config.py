from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app

runner = CliRunner()


def test_config_show_returns_yaml(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "show"])
    assert res.exit_code == 0
    assert "llm:" in res.stdout


def test_config_set_writes_file(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "set", "api_key", "sk-test"])
    assert res.exit_code == 0
    assert "sk-test" in cfg_path.read_text()


def test_config_set_alpha_validates_range(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "set", "alpha", "1.5"])
    assert res.exit_code != 0


def test_cli_cached_mode_works_without_api_key(tmp_path, monkeypatch) -> None:
    """Spec §6: CACHED mode must support paper-reproducible runs without an API key."""
    from typer.testing import CliRunner as _CliRunner

    from tests.test_pipeline import _SyntheticImpulseProvider
    from wave_alpha.cli import app as app_module

    _runner = _CliRunner()
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    monkeypatch.setenv("HOME", str(tmp_path))  # isolate ~/.wave_alpha
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticImpulseProvider())

    res = _runner.invoke(
        app, ["analyze", "AAPL", "--as-of", "2024-12-31", "--llm-mode", "cached", "--json"]
    )
    # CACHED mode with cold cache → CachedResponseMissError. Exit code may be non-zero.
    # The point: no MissingAPIKeyError / typer.BadParameter about the API key.
    assert "no Anthropic API key" not in res.stdout
    assert "no Anthropic API key" not in (res.stderr or "")
