"""Banner shown once on first run, never again, suppressible by env var."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli import app as cli_app

runner = CliRunner()

# Typer 0.25.x merges stderr into stdout; result.output contains both.
# We assert against result.output for cross-version compatibility.


def _patch_sentinel(monkeypatch, tmp_path: Path) -> Path:
    sentinel = tmp_path / ".disclaimer_ack"
    monkeypatch.setattr(cli_app, "_disclaimer_sentinel_path", lambda: sentinel)
    monkeypatch.delenv("WAVE_ALPHA_NO_DISCLAIMER", raising=False)
    return sentinel


def test_banner_shown_on_first_run(monkeypatch, tmp_path):
    sentinel = _patch_sentinel(monkeypatch, tmp_path)
    assert not sentinel.exists()

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "wave-alpha disclaimer" in result.output
    assert sentinel.exists()


def test_banner_suppressed_after_first_run(monkeypatch, tmp_path):
    sentinel = _patch_sentinel(monkeypatch, tmp_path)
    sentinel.write_text("acknowledged\n")

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "disclaimer" not in result.output.lower()


def test_banner_suppressed_by_env_var(monkeypatch, tmp_path):
    _patch_sentinel(monkeypatch, tmp_path)
    monkeypatch.setenv("WAVE_ALPHA_NO_DISCLAIMER", "1")

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "disclaimer" not in result.output.lower()
