from pathlib import Path

from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(
        "wave_alpha.web.routes.settings._config_path", lambda: tmp_path / "config.yaml"
    )
    return TestClient(create_app())


def test_settings_get_renders_form(tmp_path: Path, monkeypatch) -> None:
    res = _client(monkeypatch, tmp_path).get("/settings")
    assert res.status_code == 200
    assert "Anthropic" in res.text or "API" in res.text
    assert "alpha" in res.text


def test_settings_post_persists_values(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    res = _client(monkeypatch, tmp_path).post(
        "/settings",
        data={
            "api_key": "sk-test",
            "scan_model": "claude-haiku-4-5-20251001",
            "deep_model": "claude-sonnet-4-6",
            "alpha": "0.7",
        },
    )
    assert res.status_code in (200, 303)
    assert "sk-test" in cfg.read_text()
    assert "alpha: 0.7" in cfg.read_text()


def test_settings_post_invalid_alpha_returns_400(tmp_path: Path, monkeypatch) -> None:
    res = _client(monkeypatch, tmp_path).post(
        "/settings",
        data={"api_key": "", "scan_model": "x", "deep_model": "y", "alpha": "1.5"},
    )
    assert res.status_code == 400


def test_settings_post_preserves_existing_api_key_when_empty(tmp_path: Path, monkeypatch) -> None:
    """Empty api_key submission must NOT clear an existing key."""
    cfg = tmp_path / "config.yaml"
    client = _client(monkeypatch, tmp_path)
    # Seed an existing key
    client.post(
        "/settings",
        data={
            "api_key": "sk-original",
            "scan_model": "haiku",
            "deep_model": "sonnet",
            "alpha": "0.6",
        },
    )
    assert "sk-original" in cfg.read_text()
    # Submit again with blank api_key — key must remain
    client.post(
        "/settings",
        data={"api_key": "", "scan_model": "haiku", "deep_model": "sonnet", "alpha": "0.7"},
    )
    after = cfg.read_text()
    assert "sk-original" in after
    assert "alpha: 0.7" in after


def test_settings_alpha_slider_includes_popover(tmp_path: Path, monkeypatch) -> None:
    """The alpha slider gets a trust popover with /about deep link."""
    res = _client(monkeypatch, tmp_path).get("/settings")
    assert res.status_code == 200
    body = res.text
    assert "Why our default" in body
    assert "0.6" in body  # mentioned in the narrative
    assert "popover-trigger" in body
    assert 'href="/about#alpha-blend"' in body
