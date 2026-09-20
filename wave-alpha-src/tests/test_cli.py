import json
import re

from typer.testing import CliRunner

from tests.test_pipeline import _SyntheticImpulseProvider
from wave_alpha.cli.app import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_version_command() -> None:
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert res.stdout.strip() == _expected_version_string()


def test_analyze_help_documents_json_flag() -> None:
    res = runner.invoke(app, ["analyze", "--help"])
    assert res.exit_code == 0
    assert "--json" in _ANSI.sub("", res.stdout)


def test_analyze_with_offline_provider_outputs_valid_json(monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticImpulseProvider())

    res = runner.invoke(app, ["analyze", "AAPL", "--as-of", "2024-12-31", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["ticker"] == "AAPL"
    assert "counts_daily" in payload
    assert "coherence" in payload


def test_analyze_help_documents_llm_mode_flag() -> None:
    res = runner.invoke(app, ["analyze", "--help"])
    assert res.exit_code == 0
    stdout = _ANSI.sub("", res.stdout)
    assert "--llm-mode" in stdout
    assert "--alpha" in stdout


def test_analyze_disabled_mode_smoke(monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticImpulseProvider())
    res = runner.invoke(
        app, ["analyze", "AAPL", "--as-of", "2024-12-31", "--llm-mode", "disabled", "--json"]
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert "ranked_daily" in payload


def _expected_version_string() -> str:
    from wave_alpha import __version__

    return __version__
